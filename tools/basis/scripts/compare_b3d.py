"""Сравнить два .b3d деталь за деталью: файл технолога против нашей сборки.

    python scripts/compare_b3d.py qa/fixtures/wardrobe_demo_production.b3d out/wardrobe.b3d

Для каждого файла печатает панели (имя, толщина, размер контура, положение,
кромка по сторонам `Elem:толщина`, сборка), сводку библиотеки крепежа и
мультимножество отверстий (диаметр, глубина, DrillMode); в конце — разницу
по размерам деталей, материалам, кромке и наименованиям метизов. Ничего не
пишет и не вызывает облако.
"""

from __future__ import annotations

import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from src.b3d_format import parse_b3d  # noqa: E402
import extract_b3d_hardware as ex  # noqa: E402

_LINE = 0x10


def contour_size(blob: bytes) -> tuple[float, float] | None:
    """(w, h) контура из блоба: u32 count + отрезки 0x10 (4 double). Дуги не разбираем."""
    if not blob or len(blob) < 4:
        return None
    count = struct.unpack_from("<I", blob, 0)[0]
    xs: list[float] = []
    ys: list[float] = []
    p = 4
    for _ in range(count):
        if p >= len(blob) or blob[p] != _LINE or p + 33 > len(blob):
            return None
        x1, y1, x2, y2 = struct.unpack_from("<4d", blob, p + 1)
        xs += [x1, x2]
        ys += [y1, y2]
        p += 33
    if not xs:
        return None
    return round(max(xs) - min(xs), 2), round(max(ys) - min(ys), 2)


def panel_sizes(path: Path) -> list[tuple[str, tuple[float, float] | None]]:
    parsed = parse_b3d(path.read_bytes())
    document = next(root for _flag, root in parsed["sections"] if root[0] == "Document")
    out: list[tuple[str, tuple[float, float] | None]] = []

    def walk(node: tuple) -> None:
        if ex._val(node, "Type") == 4002:
            contour = ex._get(node, "Contour")
            out.append((str(ex._val(node, "Name", "")).split("\r")[0],
                        contour_size(contour[2] if contour else b"")))
        objs = ex._get(node, "Objs")
        if objs:
            for child in objs[2]:
                if child[0] == "Obj":
                    walk(child)

    for obj in ex._get(document, "Model")[2]:
        if obj[0] == "Obj":
            walk(obj)
    return out


def summarize(path: Path, label: str) -> tuple[dict, list]:
    data = ex.extract(str(path))
    sizes = panel_sizes(path)
    print(f"\n{'=' * 30} {label}: {path.name} ({path.stat().st_size} байт)")
    print(f"деталей {len(data['panels'])} · метизов {len(data['instances'])} · "
          f"записей библиотеки {len(data['library'])} · отверстий {len(data['world_holes'])}")
    print("-- детали: имя | толщина | контур w×h | положение | кромка Elem:толщина | сборка")
    for panel, (_name, size) in zip(data["panels"], sizes):
        butts = ",".join(f"{b['elem']}:{b['thick']}" for b in panel["butts"]) or "-"
        size_text = f"{size[0]}×{size[1]}" if size else "?"
        print(f"  {panel['name'][:34]:34} | {panel['thick']:>4} | {size_text:>14} | "
              f"{tuple(round(v, 1) for v in panel['pos'])} | {butts} | {panel['assembly'] or '/'}")
        print(f"      мат: {panel['mat'][:90]}")
    print("-- крепёж: наименование | отверстия | инстансов | шаблонов")
    inst_count = Counter(i["fastId"] for i in data["instances"])
    by_name: dict[str, dict] = defaultdict(lambda: {"inst": 0, "templates": 0, "holes": Counter(), "mesh": False})
    for fid, entry in data["library"].items():
        row = by_name[entry["name"][:58]]
        row["templates"] += 1
        row["inst"] += inst_count.get(int(fid), 0)
        row["mesh"] |= bool(entry["hasMesh"])
        for hole in entry["holes"]:
            row["holes"][(round(hole["d"], 1), round(hole["depth"], 1), hole["drillMode"])] += 1
    for name, row in sorted(by_name.items(), key=lambda kv: -kv[1]["inst"]):
        holes = ", ".join(f"Ø{d}×{depth}/m{mode}" for (d, depth, mode) in row["holes"]) or "без отверстий"
        print(f"  {name:58} | {holes:44} | ×{row['inst']:<3} | tpl {row['templates']:<3} | mesh={row['mesh']}")
    counts = Counter((round(h["d"], 1), round(h["depth"], 1), h["drillMode"]) for h in data["world_holes"])
    print("-- отверстия (Ø, глубина, DrillMode):")
    print("  ", dict(sorted(counts.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2] or -1))))
    return data, sizes


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    reference, ours = Path(argv[1]), Path(argv[2])
    ref, ref_sizes = summarize(reference, "ЭТАЛОН")
    our, our_sizes = summarize(ours, "НАША СБОРКА")

    print("\n" + "=" * 30 + " РАЗНИЦА")
    ref_set = Counter((round(p["thick"] or 0, 1), size) for p, (_n, size) in zip(ref["panels"], ref_sizes))
    our_set = Counter((round(p["thick"] or 0, 1), size) for p, (_n, size) in zip(our["panels"], our_sizes))
    print("детали (толщина, w×h) только в эталоне:", sorted((ref_set - our_set).items(), key=str))
    print("детали (толщина, w×h) только у нас:    ", sorted((our_set - ref_set).items(), key=str))
    print("материалы эталона:", Counter(p["mat"] for p in ref["panels"]))
    print("материалы наши:   ", Counter(p["mat"] for p in our["panels"]))
    print("кромка эталона:", Counter(b["thick"] for p in ref["panels"] for b in p["butts"]))
    print("кромка наша:   ", Counter(b["thick"] for p in our["panels"] for b in p["butts"]))
    print("метизы эталона:", Counter(i["name"] for i in ref["instances"]))
    print("метизы наши:   ", Counter(i["name"] for i in our["instances"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
