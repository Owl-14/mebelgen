"""Генерация .cfrn из нашего project.json (панели) — для облачной CfrnToB3d.

.cfrn = zip с file.json: сцена БАЗИС {model, table, modelParams}.
Деталь = contour.size + thickness + material; размещение = матрица 4×4.
Три ориентации (vertical/horizont/front) ↔ три поворота (реверс-инжиниринг
реального .cfrn из облака). Это даёт device-independent путь:
ParamSpec → панели → .cfrn → облако CfrnToB3d → .b3d (без десктопа).
"""

from __future__ import annotations

import io
import json
import zipfile
from typing import Any

from .hardware import compute_drilling

# row-major 3×3 повороты по ориентации (из реального .cfrn БАЗИС-Облака)
_ROT = {
    "vertical": [[0, 0, 1], [0, 1, 0], [-1, 0, 0]],
    "horizont": [[1, 0, 0], [0, 0, -1], [0, 1, 0]],
    "horizontal": [[1, 0, 0], [0, 0, -1], [0, 1, 0]],
    "front": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
}

# (axis, dir) → единичный вектор сверления (в мировых осях)
_DRILL_VEC = {
    ("x", 1): {"x": 1, "y": 0, "z": 0}, ("x", -1): {"x": -1, "y": 0, "z": 0},
    ("y", 1): {"x": 0, "y": 1, "z": 0}, ("y", -1): {"x": 0, "y": -1, "z": 0},
    ("z", 1): {"x": 0, "y": 0, "z": 1}, ("z", -1): {"x": 0, "y": 0, "z": -1},
}


def _r(v: float) -> float:
    v = round(float(v), 3)
    return int(v) if v == int(v) else v


def _contour(orient: str, sx: float, sy: float, sz: float) -> dict[str, float]:
    # Контур и трансляция сверены с ЭТАЛОНОМ РОДНОГО шкафа БАЗИС (Мебельщик →
    # экспорт .cfrn, AutoSave18), который облако пересобирает корректно. Это
    # ground-truth, под который должен попадать наш .cfrn для верного импорта:
    #   vertical : {x: глубина(sz), y: высота(sy)},  trans.z = z1
    #   horizont : {x: ширина(sx),  y: глубина(sz)},  trans.z = z2  (см. _z_for)
    #   front    : {x: ширина(sx),  y: высота(sy)},   trans.z = z1
    if orient in ("vertical",):
        return {"x": _r(sz), "y": _r(sy)}
    if orient in ("horizont", "horizontal"):
        return {"x": _r(sx), "y": _r(sz)}
    return {"x": _r(sx), "y": _r(sy)}            # front


def _matrix(orient: str, x1: float, y1: float, z1: float) -> list[float]:
    r = _ROT.get(orient, _ROT["front"])
    return [r[0][0], r[0][1], r[0][2], 0,
            r[1][0], r[1][1], r[1][2], 0,
            r[2][0], r[2][1], r[2][2], 0,
            _r(x1), _r(y1), _r(z1), 1]


_GENERIC_COLOR = ("соглас", "уточн", "не задан", "любой")


def _board_material_name(m: dict[str, Any]) -> str:
    """Имя материала плиты в формате БАЗИС: «Декор (Код)», иначе тип плиты."""
    color = str(m.get("color", "")).strip()
    code = str(m.get("color_code", "")).strip()
    board = (str(m.get("board_material", "")).strip() or "ЛДСП")
    generic = (not color) or any(w in color.lower() for w in _GENERIC_COLOR) or color in ("—", "-")
    if not generic:
        return f"{color} ({code})" if code else color
    return board


def _hardware_entries(project: dict[str, Any]) -> list[dict[str, Any]]:
    """Фурнитура из material_refs → записи материала с артикулом (как в родном .cfrn)."""
    refs = project.get("material_refs") or {}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for slot in ("handles", "drawer_guides", "hinges", "legs", "locks"):
        r = refs.get(slot)
        if not isinstance(r, dict) or not r.get("resolved"):
            continue
        cand = None
        c = r.get("candidates")
        if isinstance(c, list) and c and isinstance(c[0], dict):
            cand = c[0]
        elif r.get("name"):
            cand = {"name": r.get("name"), "article": r.get("article")}
        if not cand or not cand.get("name") or cand["name"] in seen:
            continue
        seen.add(cand["name"])
        entry = {"name": cand["name"]}
        if cand.get("article"):
            entry["art"] = str(cand["article"])
        out.append(entry)
    return out


def project_to_cfrn_json(project: dict[str, Any]) -> dict[str, Any]:
    name = project.get("project_name", "model")
    m = project.get("materials", {}) or {}

    # 0 = плита (реальный декор), 1 = задник (если материал отличается)
    board_name = _board_material_name(m)
    materials: list[dict[str, Any]] = [{"name": board_name}]
    back_raw = str(m.get("back_wall_material", "")).strip()
    back_idx = 0
    if back_raw and back_raw.lower() not in board_name.lower():
        materials.append({"name": back_raw})
        back_idx = len(materials) - 1
    materials += _hardware_entries(project)   # фурнитура с артикулами (spec)

    objects: list[dict[str, Any]] = [{"objType": 7, "name": name, "isAssemblyUnit": False}]
    children: list[dict[str, Any]] = []

    for p in project.get("panels", []):
        pl = p.get("placement")
        if not pl:
            continue
        orient = str(p.get("basis_orientation") or "front").lower()
        sx, sy, sz = pl["x2"] - pl["x1"], pl["y2"] - pl["y1"], pl["z2"] - pl["z1"]
        cont = _contour(orient, sx, sy, sz)
        pname = str(p.get("name", "")).lower()
        mi = back_idx if ("задн" in pname or p.get("type") == "back") else 0
        idx = len(objects)
        objects.append({
            "objType": 2,
            "name": p.get("name", f"panel_{idx}"),
            "materialIndex": mi,
            "materialWidth": 0,
            "contour": {"size": cont, "pos": {"x": 0, "y": 0}},
            "thickness": _r(p.get("thickness", 16)),
            "textureOrientation": 0,
            "frontFace": 2,
            "sourceContour": {"size": cont, "pos": {"x": 0, "y": 0}},
            "clippedSourceContour": {"size": cont, "pos": {"x": 0, "y": 0}},
            "fullProductContour": {"size": cont, "pos": {"x": 0, "y": 0}},
        })
        # компенсация направления выдавливания толщины от точки привязки:
        #   горизонталь — контур растёт в −Z → привязка по задней грани z2;
        #   вертикаль   — толщина растёт в −X → привязка по правой грани x2
        # (иначе панель уезжает на толщину: боковина x[0,16] кодируется как x[-16,0]).
        tz = pl["z2"] if orient in ("horizont", "horizontal") else pl["z1"]
        tx = pl["x2"] if orient == "vertical" else pl["x1"]
        children.append({"tableIndex": idx, "matrix": _matrix(orient, tx, pl["y1"], tz)})

    table: dict[str, Any] = {"materials": materials, "objects": objects}
    _encode_drilling(project, objects, children, table)
    return {
        "model": {"tableIndex": -1, "objs": [{"tableIndex": 0, "objs": children}]},
        "table": table,
        "modelParams": {"name": name},
    }


def _encode_drilling(project: dict[str, Any], objects: list[dict[str, Any]],
                     children: list[dict[str, Any]], table: dict[str, Any]) -> None:
    """Присадки под фурнитуру (AKD-88) в .cfrn — по схеме эталона native_cabinet.cfrn:
    каталог `table.holes` [{depth,diameter,drillMode}] + объект `objType 5` с
    `holes:[{pos,dir,infoIndex}]` и матрицей. 3D-меш фурнитуры (triangleData) НЕ
    строим — он берётся из каталога БАЗИС (десктоп-импортёр AKD-14).

    Присадки считает hardware.compute_drilling по геометрии. Ошибка расчёта не должна
    ломать сборку .cfrn — тогда модель просто идёт без присадок."""
    try:
        drill = compute_drilling(project)
    except Exception:
        return
    if not drill:
        return
    catalog: list[dict[str, Any]] = []
    index: dict[tuple[float, float, int], int] = {}
    holes: list[dict[str, Any]] = []
    for h in drill:
        key = (_r(h["depth"]), _r(h["diameter"]), 1)   # drillMode 1 — как в эталоне
        if key not in index:
            index[key] = len(catalog)
            catalog.append({"depth": key[0], "diameter": key[1], "drillMode": key[2]})
        vec = _DRILL_VEC.get((h["axis"], int(h["dir"])), {"x": 0, "y": 0, "z": -1})
        holes.append({"pos": {"x": _r(h["x"]), "y": _r(h["y"]), "z": _r(h["z"])},
                      "dir": vec, "infoIndex": index[key]})
    idx = len(objects)
    # один служебный объект-«фурнитура» держит все присадки (pos = мировые, матрица 1)
    objects.append({"objType": 5, "name": "Присадки", "materialIndex": 0,
                    "triangleData": [], "holes": holes})
    children.append({"tableIndex": idx, "matrix": _matrix("front", 0, 0, 0)})
    table["holes"] = catalog
    table["triangles"] = []


def project_to_cfrn_bytes(project: dict[str, Any]) -> bytes:
    data = json.dumps(project_to_cfrn_json(project), ensure_ascii=False).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("file.json", data)
    return buf.getvalue()


def cfrn_world_boxes(project: dict[str, Any]) -> list[dict[str, Any]]:
    """Мировые AABB панелей, реконструированные ИЗ матриц .cfrn — как их строит
    БАЗИС при сборке .b3d. Позволяет проверить кодирование, а не только placement
    (совпадает с обратной выгрузкой b3d→cfrn из облака до миллиметра)."""
    d = project_to_cfrn_json(project)
    tobjs = d["table"]["objects"]
    nodes = d["model"]["objs"][0]["objs"]

    def xf(M: list[float], p: tuple[float, float, float]) -> tuple[float, float, float]:
        px, py, pz = p
        return (px * M[0] + py * M[4] + pz * M[8] + M[12],
                px * M[1] + py * M[5] + pz * M[9] + M[13],
                px * M[2] + py * M[6] + pz * M[10] + M[14])

    out: list[dict[str, Any]] = []
    for nd in nodes:
        g = tobjs[nd["tableIndex"]]
        if "contour" not in g:      # objType 5 (присадки) — не панель, пропускаем
            continue
        sz = g["contour"]["size"]
        th = g.get("thickness", 16)
        M = nd["matrix"]
        corners = [(x, y, z) for x in (0, sz["x"]) for y in (0, sz["y"]) for z in (0, th)]
        ws = [xf(M, c) for c in corners]
        xs = [w[0] for w in ws]
        ys = [w[1] for w in ws]
        zs = [w[2] for w in ws]
        out.append({"name": g.get("name"), "x1": min(xs), "x2": max(xs),
                    "y1": min(ys), "y2": max(ys), "z1": min(zs), "z2": max(zs)})
    return out


def cfrn_holes(project: dict[str, Any]) -> list[dict[str, Any]]:
    """Мировые присадки, реконструированные ИЗ .cfrn (матрица объекта objType 5 +
    локальные pos отверстий + каталог table.holes). Для сверки с compute_drilling."""
    d = project_to_cfrn_json(project)
    tobjs = d["table"]["objects"]
    nodes = d["model"]["objs"][0]["objs"]
    catalog = d["table"].get("holes", [])
    node = next((n for n in nodes if tobjs[n["tableIndex"]].get("objType") == 5), None)
    if node is None:
        return []
    M = node["matrix"]
    obj = tobjs[node["tableIndex"]]

    def xf(p: dict[str, float]) -> tuple[float, float, float]:
        px, py, pz = p["x"], p["y"], p["z"]
        return (px * M[0] + py * M[4] + pz * M[8] + M[12],
                px * M[1] + py * M[5] + pz * M[9] + M[13],
                px * M[2] + py * M[6] + pz * M[10] + M[14])

    out: list[dict[str, Any]] = []
    for h in obj.get("holes", []):
        w = xf(h["pos"])
        cat = catalog[h["infoIndex"]] if h["infoIndex"] < len(catalog) else {}
        out.append({"x": w[0], "y": w[1], "z": w[2], "dir": h["dir"],
                    "diameter": cat.get("diameter"), "depth": cat.get("depth")})
    return out


def check_cfrn_holes(project: dict[str, Any], *, tol: float = 0.5) -> list[str]:
    """Сверяет присадки, закодированные в .cfrn, с compute_drilling (позиция,
    диаметр, глубина). Пусто = кодирование присадок корректно. Наличие присадок в
    самом .b3d подтверждается обратной выгрузкой b3d→cfrn из облака (round-trip)."""
    src = compute_drilling(project)
    enc = cfrn_holes(project)
    issues: list[str] = []
    if len(src) != len(enc):
        issues.append(f"число присадок: compute={len(src)} ≠ .cfrn={len(enc)}")
        return issues
    for s, e in zip(src, enc):
        if abs(s["x"] - e["x"]) > tol or abs(s["y"] - e["y"]) > tol or abs(s["z"] - e["z"]) > tol:
            issues.append(
                f"{s['purpose']}: .cfrn ({e['x']:.1f},{e['y']:.1f},{e['z']:.1f}) "
                f"≠ compute ({s['x']},{s['y']},{s['z']})")
        elif s["diameter"] != e["diameter"] or s["depth"] != e["depth"]:
            issues.append(f"{s['purpose']}: Ø/глубина .cfrn ({e['diameter']}/{e['depth']}) "
                          f"≠ compute ({s['diameter']}/{s['depth']})")
    return issues


def check_cfrn_encoding(project: dict[str, Any], *, tol: float = 0.5) -> list[str]:
    """Сверяет мировые AABB из .cfrn с placement и ловит пересечения на уровне
    кодирования (толщина панели должна выдавливаться в нужную сторону). Пусто = ок.

    Именно этот класс багов placement-чек НЕ видит: placement может быть корректен,
    а матрица .cfrn — уводить деталь на толщину, создавая нахлёсты в самом .b3d."""
    boxes = cfrn_world_boxes(project)
    panels = [p for p in project.get("panels", []) if p.get("placement")]
    issues: list[str] = []
    for p, b in zip(panels, boxes):
        pl = p["placement"]
        for ax in ("x", "y", "z"):
            if abs(b[ax + "1"] - pl[ax + "1"]) > tol or abs(b[ax + "2"] - pl[ax + "2"]) > tol:
                issues.append(
                    f"{p.get('name')}: .cfrn {ax}[{b[ax+'1']:.1f},{b[ax+'2']:.1f}] "
                    f"≠ placement {ax}[{pl[ax+'1']},{pl[ax+'2']}]")
    n = len(boxes)
    for i in range(n):
        a = boxes[i]
        for j in range(i + 1, n):
            c = boxes[j]
            ox = min(a["x2"], c["x2"]) - max(a["x1"], c["x1"])
            oy = min(a["y2"], c["y2"]) - max(a["y1"], c["y1"])
            oz = min(a["z2"], c["z2"]) - max(a["z1"], c["z1"])
            if ox > tol and oy > tol and oz > tol:
                issues.append(
                    f"пересечение в .cfrn: {a['name']} × {c['name']} "
                    f"на {ox:.1f}×{oy:.1f}×{oz:.1f} мм")
    return issues
