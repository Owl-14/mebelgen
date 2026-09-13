"""Сборка нативного .b3d (BZ85, формат версии 15) из нашего project.json без облака.

Почему это работает (эксперименты 2026-09-11, `out/b3d_experiments`, БАЗИС-Просмотр
3D 2026.5.6):

* Десктопный файл Мебельщика (Header.Version 15, Document 23.15) хвоста не имеет и
  воспроизводится нашим кодеком байт-в-байт (`test_desktop_file_is_reproduced_byte_for_byte`).
* Дерево облачного файла (Header.Version 17, Document 25.17) без 64-байтного хвоста
  Просмотр не читает («Ошибка чтения файла»); то же дерево с переименованной панелью
  и родным хвостом — тоже не читает. Значит хвост — подпись содержимого формата 17.
* То же облачное дерево, записанное как версия 15 без хвоста, Просмотр открывает.

Отсюда сборщик пишет формат версии 15. Геометрию, материалы и присадки он берёт из
того же промежуточного представления, что и `.cfrn` (`cfrn.project_to_cfrn_json`):
именно его облако превращало в `.b3d`, поэтому облачный файл того же изделия служит
эталоном для сверки (`tests/test_b3d_builder.py`). Соответствие, установленное по
эталону `qa/fixtures/wardrobe_demo_ours_cloud.b3d`:

    cfrn child.matrix (row-major 4×4) → Obj.Trans: X,Y,Z = строка переноса,
                                        Rx,Ry,Rz,Rw = кватернион транспонированного 3×3
    cfrn objType 2 (панель)            → Obj Type=4002 {Name, Trans, Mat, Thick, Contour, Butts}
    cfrn objType 7 isAssemblyUnit      → Obj Type=1005 {Name, Trans=единичная, Objs}
    cfrn objType 5 (метиз)             → FurnList.F<FastID> {Holes…} + Obj Type=3001 {Trans, FastID}
    contour.size {x,y}                 → blob: u32 n=4, затем 4×(u8 0x10, x1,y1,x2,y2 f64)

Что сборщик пока НЕ пишет: TriData (меши тел фурнитуры — ручки, опоры, штанга
остаются без видимого тела), Estimate, миниатюру. Открываемость файла Просмотром
проверяется отдельно (`main.py local-b3d build … --check-viewer`), офлайн-паритет —
`b3d_verify.verify_b3d_parity`.
"""

from __future__ import annotations

import datetime as _dt
import math
import struct
import zlib
from typing import Any

from .b3d_format import write_b3d
from .cfrn import project_to_cfrn_json

FORMAT_HEADER_VERSION = 15        # как у десктопного Мебельщика 2023.10 — без подписи
FORMAT_DOCUMENT_VERSION = (23, 15)
_CONTOUR_LINE = 0x10
_DELPHI_EPOCH = _dt.datetime(1899, 12, 30)


# ------------------------------------------------------------------ утилиты дерева

def _obj(name: str, children: list[tuple]) -> tuple:
    return (name, "obj", children)


def _id_node(value: int) -> tuple:
    """Идентификаторы в эталонах — u8 пока помещаются, дальше i32."""
    return ("ID", "u8", value) if 0 <= value < 256 else ("ID", "i32", value)


def _trans(x: float, y: float, z: float, q: tuple[float, float, float, float]) -> tuple:
    return _obj("Trans", [
        ("X", "f64", float(x)), ("Y", "f64", float(y)), ("Z", "f64", float(z)),
        ("Rx", "f64", q[0]), ("Ry", "f64", q[1]), ("Rz", "f64", q[2]), ("Rw", "f64", q[3]),
    ])


def quaternion_from_cfrn_matrix(matrix: list[float]) -> tuple[float, float, float, float]:
    """Кватернион (x, y, z, w) поворота из row-major матрицы .cfrn.

    БАЗИС хранит поворот для столбцовой записи, поэтому берётся транспонированная
    3×3 (проверено по всем панелям облачного эталона).
    """
    m = [float(v) for v in matrix]
    r = [[m[0], m[4], m[8]], [m[1], m[5], m[9]], [m[2], m[6], m[10]]]  # транспонирование
    trace = r[0][0] + r[1][1] + r[2][2]
    if trace > 0:
        s = math.sqrt(trace + 1.0) * 2
        w, x, y, z = 0.25 * s, (r[2][1] - r[1][2]) / s, (r[0][2] - r[2][0]) / s, (r[1][0] - r[0][1]) / s
    elif r[0][0] > r[1][1] and r[0][0] > r[2][2]:
        s = math.sqrt(1.0 + r[0][0] - r[1][1] - r[2][2]) * 2
        w, x, y, z = (r[2][1] - r[1][2]) / s, 0.25 * s, (r[0][1] + r[1][0]) / s, (r[0][2] + r[2][0]) / s
    elif r[1][1] > r[2][2]:
        s = math.sqrt(1.0 + r[1][1] - r[0][0] - r[2][2]) * 2
        w, x, y, z = (r[0][2] - r[2][0]) / s, (r[0][1] + r[1][0]) / s, 0.25 * s, (r[1][2] + r[2][1]) / s
    else:
        s = math.sqrt(1.0 + r[2][2] - r[0][0] - r[1][1]) * 2
        w, x, y, z = (r[1][0] - r[0][1]) / s, (r[0][2] + r[2][0]) / s, (r[1][2] + r[2][1]) / s, 0.25 * s
    # нормализуем к w >= 0, чтобы одинаковые повороты давали одинаковые байты
    if w < 0:
        x, y, z, w = -x, -y, -z, -w
    return (_clean(x), _clean(y), _clean(z), _clean(w))


def _clean(v: float) -> float:
    return 0.0 if abs(v) < 1e-12 else v


def rectangle_contour(width: float, height: float) -> bytes:
    """Контур панели: замкнутый прямоугольник из 4 отрезков от начала координат."""
    w, h = float(width), float(height)
    points = [(0.0, 0.0), (w, 0.0), (w, h), (0.0, h)]
    out = bytearray(struct.pack("<I", len(points)))
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
        out += bytes([_CONTOUR_LINE]) + struct.pack("<4d", x1, y1, x2, y2)
    return bytes(out)


def delphi_datetime(moment: _dt.datetime | None = None) -> float:
    moment = moment or _dt.datetime.now()
    delta = moment - _DELPHI_EPOCH
    return delta.days + delta.seconds / 86400.0 + delta.microseconds / 86400.0e6


def fast_id(name: str, holes: list[dict[str, Any]]) -> int:
    """Детерминированный положительный идентификатор шаблона крепежа."""
    payload = name + "|" + "|".join(
        f"{h['pos']['x']},{h['pos']['y']},{h['pos']['z']},{h['dir']['x']},{h['dir']['y']},{h['dir']['z']},{h['infoIndex']}"
        for h in holes
    )
    return (zlib.crc32(payload.encode("utf-8")) & 0x7FFFFFFF) or 1


def _material_label(entry: dict[str, Any]) -> str:
    name = str(entry.get("name") or "")
    art = str(entry.get("art") or "").strip()
    return f"{name}\r{art}" if art else name


# ------------------------------------------------------------------ сборка

_SHEET_KINDS = ("лдсп", "хдф", "двп", "мдф", "фанера", "дсп")


class _Builder:
    def __init__(self, doc: dict[str, Any], *, board_kind: str = "ЛДСП") -> None:
        self.board_kind = str(board_kind or "ЛДСП").strip() or "ЛДСП"
        self.objects: list[dict[str, Any]] = doc["table"]["objects"]
        self.materials: list[dict[str, Any]] = doc["table"]["materials"]
        self.hole_catalog: list[dict[str, Any]] = doc["table"].get("holes") or []
        self.name = str(doc.get("modelParams", {}).get("name") or "model")
        self.next_id = 3
        self.templates: dict[int, tuple] = {}      # tableIndex → FurnList entry
        self.template_ids: dict[int, int] = {}     # tableIndex → FastID

    def take_id(self) -> int:
        value = self.next_id
        self.next_id += 1
        return value

    def panel_material_label(self, entry: dict[str, Any], thickness: float) -> str:
        """«ЛДСП, 16 мм, Белый<CR>W1000 ST26» — форма записи базы материалов
        десктопного Мебельщика (эталон технолога); листовой материал без
        декора («ХДФ»/«ДВП») — «ХДФ, 4 мм»."""
        name = str(entry.get("name") or "").strip()
        art = str(entry.get("art") or "").strip()
        thick = f"{thickness:g}"
        if name.lower().startswith(_SHEET_KINDS):
            label = f"{name}, {thick} мм"
        else:
            label = f"{self.board_kind}, {thick} мм, {name}" if name else f"{self.board_kind}, {thick} мм"
        return f"{label}\r{art}" if art else label

    # --- панель
    def panel(self, node: dict[str, Any], obj: dict[str, Any]) -> tuple:
        matrix = node["matrix"]
        size = obj["contour"]["size"]
        children = [
            ("Type", "i32", 4002),
            ("Name", "str", str(obj.get("name") or "")),
            _id_node(self.take_id()),
            _trans(matrix[12], matrix[13], matrix[14], quaternion_from_cfrn_matrix(matrix)),
            ("Mat", "str", self.panel_material_label(self.materials[obj["materialIndex"]],
                                                     float(obj.get("thickness") or 16))),
            ("Thick", "f64", float(obj.get("thickness") or 16)),
            ("Contour", "blob", rectangle_contour(size["x"], size["y"])),
        ]
        butts = obj.get("butts") or []
        if butts:
            children.append(_obj("Butts", [self.butt(b) for b in butts]))
        return _obj("Obj", children)

    def butt(self, b: dict[str, Any]) -> tuple:
        material = self.materials[b["materialIndex"]]
        return _obj("Butt", [
            ("Mat", "str", _material_label(material)),
            ("Thick", "f64", float(b.get("thickness") or 0.5)),
            ("Width", "f64", float(b.get("width") or 19)),
            ("Elem", "u8", int(b.get("elemIndex") or 0)),
            ("Sign", "str", str(material.get("sign") or "")),
            ("Overhung", "f64", float(b.get("overhang") or 30)),
            ("Allowance", "f64", float(b.get("allowance") or 0.5)),
            ("Clip", "bool", bool(b.get("clip", False))),
        ])

    # --- сборочный узел
    def assembly(self, node: dict[str, Any], obj: dict[str, Any]) -> tuple:
        node_id = self.take_id()
        return _obj("Obj", [
            ("Type", "i32", 1005),
            ("Name", "str", str(obj.get("name") or "")),
            _id_node(node_id),
            ("BazisFlags", "u8", 1),
            _trans(0.0, 0.0, 0.0, (0.0, 0.0, 0.0, 1.0)),
            ("JointLength", "f64", 0.0),
            _obj("Objs", [self.convert(child) for child in node.get("objs", [])]),
        ])

    # --- метиз: шаблон в FurnList + инстанс
    def fastener(self, node: dict[str, Any], obj: dict[str, Any]) -> tuple:
        index = node["tableIndex"]
        label = _material_label(self.materials[obj["materialIndex"]])
        if index not in self.templates:
            holes = obj.get("holes") or []
            fid = fast_id(label, holes)
            hole_nodes = []
            extent = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]   # MinX, MinY, MinZ, MaxX, MaxY, MaxZ
            for h in holes:
                info = self.hole_catalog[h["infoIndex"]]
                radius, depth = float(info["diameter"]) / 2, float(info["depth"])
                p, d = h["pos"], h["dir"]
                hole_nodes.append(_obj("Hole", [
                    ("X", "f64", float(p["x"])), ("Y", "f64", float(p["y"])), ("Z", "f64", float(p["z"])),
                    ("DirX", "f64", float(d["x"])), ("DirY", "f64", float(d["y"])), ("DirZ", "f64", float(d["z"])),
                    ("Radius", "f64", radius), ("Depth", "f64", depth),
                    ("DrillMode", "u8", int(info.get("drillMode") or 1)),
                ]))
                for axis, (pv, dv) in enumerate(((p["x"], d["x"]), (p["y"], d["y"]), (p["z"], d["z"]))):
                    lo = min(float(pv) - radius, float(pv) + float(dv) * depth - radius)
                    hi = max(float(pv) + radius, float(pv) + float(dv) * depth + radius)
                    extent[axis] = min(extent[axis], lo)
                    extent[axis + 3] = max(extent[axis + 3], hi)
            self.templates[index] = _obj(f"F{fid}", [
                ("Name", "str", label),
                ("FastID", "i32", fid),
                ("DatumMode", "u8", 2),
                ("ParamOffset", "f64", 0.0),
                ("MinX", "f64", extent[0]), ("MinY", "f64", extent[1]), ("MinZ", "f64", extent[2]),
                ("MaxX", "f64", extent[3]), ("MaxY", "f64", extent[4]), ("MaxZ", "f64", extent[5]),
                _obj("Holes", hole_nodes),
                ("Edges", "blob", b"\x00\x00\x00\x00"),
                ("RotHoleIndex", "i32", -1),
                ("RotEdgeIndex", "i32", -1),
            ])
            self.template_ids[index] = fid
        matrix = node["matrix"]
        return _obj("Obj", [
            ("Type", "i32", 3001),
            ("Name", "str", label),
            _id_node(self.take_id()),
            _trans(matrix[12], matrix[13], matrix[14], quaternion_from_cfrn_matrix(matrix)),
            ("FastID", "i32", self.template_ids[index]),
            ("SalonType", "u8", 0),
        ])

    def convert(self, node: dict[str, Any]) -> tuple:
        obj = self.objects[node["tableIndex"]]
        kind = obj.get("objType")
        if kind == 2:
            return self.panel(node, obj)
        if kind == 7:
            return self.assembly(node, obj)
        if kind == 5:
            return self.fastener(node, obj)
        raise ValueError(f"Неизвестный objType в .cfrn-представлении: {kind!r}")

    def build(self, doc: dict[str, Any], *, saved_at: _dt.datetime | None = None) -> list[tuple[int, tuple]]:
        article_id = self.take_id()
        root_id = self.take_id()
        model_children = [self.convert(child) for child in doc["model"]["objs"][0]["objs"]]
        root = _obj("Obj", [
            ("Type", "i32", 1005),
            ("Name", "str", self.name),
            _id_node(root_id),
            ("JointLength", "f64", 0.0),
            _obj("Objs", model_children),
        ])
        header = _obj("Header", [
            ("Version", "u8", FORMAT_HEADER_VERSION),
            _obj("Thumbnail", []),
            _obj("Article", [
                ("Name", "str", self.name),
                _id_node(article_id),
                ("LDistance", "f64", 0.0),
                ("LDistance.Min", "f64", 0.0),
                ("LDistance.Max", "f64", 0.0),
                ("Furniture", "bool", False),
                _obj("ExtraInfo", []),
                _obj("RotationList", []),
            ]),
        ])
        document = _obj("Document", [
            ("VersionMajor", "u8", FORMAT_DOCUMENT_VERSION[0]),
            ("VersionMinor", "u8", FORMAT_DOCUMENT_VERSION[1]),
            _obj("Estimate", []),
            _obj("FurnList", [self.templates[k] for k in sorted(self.templates)]),
            _obj("Model", [root]),
            _obj("ModelParams", [("Name", "str", self.name)]),
            _obj("Camera", [
                ("AngleX", "f64", 0.0), ("AngleY", "f64", 0.0),
                ("MoveX", "f64", 0.0), ("MoveY", "f64", 0.0), ("Scale", "f64", 1.0),
                _obj("FixedPos", [("x", "f64", 0.0), ("y", "f64", 0.0), ("z", "f64", 0.0)]),
                ("Perspective", "bool", False), ("PerspAspect", "f64", 5.0),
                _obj("NavPos", [("x", "f64", 0.0), ("y", "f64", 0.0), ("z", "f64", 0.0)]),
                ("NavAngleOfView", "f64", 60.0),
                _obj("NavRot", [("x", "f64", 0.0), ("y", "f64", 0.0), ("z", "f64", 0.0), ("q", "f64", 1.0)]),
            ]),
            _obj("Undo", [_obj("Operations", [])]),
            ("DateTimeLastSaved", "date", delphi_datetime(saved_at)),
        ])
        return [(0x00, header), (0x01, document)]


def build_b3d_sections(project: dict[str, Any], *, saved_at: _dt.datetime | None = None) -> list[tuple[int, tuple]]:
    """Дерево BZ85 (секции Header/Document) для project.json."""
    doc = project_to_cfrn_json(project)
    board_kind = str((project.get("materials") or {}).get("board_material") or "ЛДСП")
    return _Builder(doc, board_kind=board_kind).build(doc, saved_at=saved_at)


def project_to_b3d_bytes(project: dict[str, Any], *, saved_at: _dt.datetime | None = None) -> bytes:
    """Готовый .b3d (формат версии 15, без подписи) для project.json."""
    return write_b3d(build_b3d_sections(project, saved_at=saved_at), b"")


__all__ = [
    "FORMAT_DOCUMENT_VERSION", "FORMAT_HEADER_VERSION", "build_b3d_sections",
    "delphi_datetime", "fast_id", "project_to_b3d_bytes", "quaternion_from_cfrn_matrix",
    "rectangle_contour",
]
