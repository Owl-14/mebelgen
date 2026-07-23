"""Тела метизов .cfrn не выходят за пределы панелей (AKD-287, баг просмотра).

Баг: после смены направления пар MNFX тело «болта» строилось 45 мм вдоль
штока — торчало на ~29 мм из крышки 16 мм; штифты SE01PB торчали из боковин.
Тест: бокс каждого инстанса (меш × матрица) покрыт объединением боксов
панелей с допуском TOL (шляпки гвоздей/саморезов и фланцы лежат НА пласти).
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.generators import generate_from_paramspec           # noqa: E402
from src.hardware import compute_drilling                    # noqa: E402
from src.fasteners3d import build_fastener_objects           # noqa: E402

TOL = 2.0
SPECS = ("tumba_moderatora", "wardrobe_demo")


def _project(name: str) -> dict:
    spec = json.loads((ROOT / "paramspecs" / f"{name}.json").read_text(encoding="utf-8"))
    return generate_from_paramspec(spec)


def _local_bbox(obj_text: str):
    pts = [tuple(map(float, ln.split()[1:4]))
           for ln in obj_text.splitlines() if ln.startswith("v ")]
    return (tuple(min(p[k] for p in pts) for k in range(3)),
            tuple(max(p[k] for p in pts) for k in range(3)))


def _world_bbox(m: list[float], lo, hi):
    ax, t = [m[0:3], m[4:7], m[8:11]], m[12:15]
    corners = [tuple(t[k] + cx * ax[0][k] + cy * ax[1][k] + cz * ax[2][k]
                     for k in range(3))
               for cx in (lo[0], hi[0]) for cy in (lo[1], hi[1])
               for cz in (lo[2], hi[2])]
    return (tuple(min(c[k] for c in corners) for k in range(3)),
            tuple(max(c[k] for c in corners) for k in range(3)))


def _uncovered_point(lo, hi, boxes):
    """Первая пробная точка бокса вне всех панелей (+TOL) или None.

    Пробы: станции каждые ≤8 мм вдоль длинной оси × (4 угла + центр) сечения.
    """
    dims = [hi[k] - lo[k] for k in range(3)]
    a = dims.index(max(dims))
    o1, o2 = [k for k in range(3) if k != a]
    n = max(2, math.ceil(dims[a] / 8) + 1)
    for i in range(n):
        s = lo[a] + dims[a] * i / (n - 1)
        for u, v in ((lo[o1], lo[o2]), (lo[o1], hi[o2]), (hi[o1], lo[o2]),
                     (hi[o1], hi[o2]),
                     ((lo[o1] + hi[o1]) / 2, (lo[o2] + hi[o2]) / 2)):
            p = [0.0, 0.0, 0.0]
            p[a], p[o1], p[o2] = s, u, v
            if not any(bl[0] - TOL <= p[0] <= bh[0] + TOL
                       and bl[1] - TOL <= p[1] <= bh[1] + TOL
                       and bl[2] - TOL <= p[2] <= bh[2] + TOL
                       for bl, bh in boxes):
                return tuple(round(c, 1) for c in p)
    return None


def _check_spec(name: str) -> list[str]:
    project = _project(name)
    boxes = [((pl["x1"], pl["y1"], pl["z1"]), (pl["x2"], pl["y2"], pl["z2"]))
             for p in project.get("panels", [])
             if (pl := p.get("placement"))]
    bad: list[str] = []
    for fo in build_fastener_objects(compute_drilling(project)):
        lo, hi = _local_bbox(fo["obj_text"])
        for idx, m in enumerate(fo["instances"]):
            wlo, whi = _world_bbox(m, lo, hi)
            p = _uncovered_point(wlo, whi, boxes)
            if p is not None:
                bad.append(f"{name}: {fo['key']} #{idx} точка {p} вне панелей "
                           f"(бокс {tuple(round(c, 1) for c in wlo)}"
                           f"..{tuple(round(c, 1) for c in whi)})")
    return bad


def test_fastener_bodies_inside_panels():
    bad = [msg for name in SPECS for msg in _check_spec(name)]
    assert not bad, "тела метизов торчат из панелей:\n" + "\n".join(bad[:20]) \
        + (f"\n… и ещё {len(bad) - 20}" if len(bad) > 20 else "")


def test_se01pb_group_names():
    """SE01PB в .cfrn — своё имя (не «Эксцентрик Ø15», хвост AKD-287)."""
    names = {fo["name"] for fo
             in build_fastener_objects(compute_drilling(_project("wardrobe_demo")))}
    assert "Эксцентрик Ø20 SE01PB" in names
    assert "Шток SE01PB" in names


if __name__ == "__main__":
    for fn in (test_fastener_bodies_inside_panels, test_se01pb_group_names):
        fn()
        print("OK", fn.__name__)
