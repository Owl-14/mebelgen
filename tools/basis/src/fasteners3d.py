"""3D-тела метизов для .cfrn (AKD-168) — по механике эталона native_cabinet.cfrn.

Эталон: фурнитура = objType:5 {materialIndex, triangleData: <индекс в
table.triangles, строка = имя OBJ в models/>, holes: [{pos, dir, infoIndex}]}
+ узлы-инстансы {tableIndex, matrix} в model.objs. Отверстия локальные,
сверление вдоль ЛОКАЛЬНОЙ −Z; матрица ставит метиз в мир.

Мы генерируем меши процедурно (те же цилиндры, что рисует Studio) и
группируем присадки compute_drilling в инстансы по типоразмеру.
"""

from __future__ import annotations

import math
from typing import Any

# purpose → (kind, цвет Kd для .mtl)
_PURPOSE_KIND = {
    "стяжка (конфирмат)": ("confirmat", (0.60, 0.63, 0.65)),
    "короб ящика (саморез)": ("screw", (0.39, 0.42, 0.44)),
    "направляющая (винт)": ("screw", (0.39, 0.42, 0.44)),
    "петля (планка)": ("screw", (0.39, 0.42, 0.44)),
    "ручка (винт)": ("hscrew", (0.82, 0.84, 0.85)),
    "задник (гвоздь)": ("nail", (0.60, 0.63, 0.65)),
    "задник (саморез)": ("screw", (0.39, 0.42, 0.44)),
    "полкодержатель": ("shelfpin", (0.82, 0.84, 0.85)),
    "петля (чашка Ø35)": ("cup", (0.55, 0.57, 0.60)),
    "эксцентрик (чашка Ø15)": ("cam", (0.79, 0.70, 0.49)),
    "эксцентрик (шток)": ("bolt", (0.60, 0.63, 0.65)),
    "фасадная стяжка (эксцентрик Ø15)": ("cam", (0.79, 0.70, 0.49)),
    "фасадная стяжка (шток)": ("bolt", (0.60, 0.63, 0.65)),
    "замок (цилиндр Ø18)": ("lock", (0.82, 0.84, 0.85)),
    "штангодержатель (саморез)": ("screw", (0.39, 0.42, 0.44)),
    # шканты собираются парой торец+пласть → отдельная ветка
}
_BOM_NAME = {
    "confirmat": "Конфирмат 7×50", "screw": "Саморез 3,5×16",
    "hscrew": "Винт М4×16", "nail": "Гвоздь 1.6×25",
    "shelfpin": "Полкодержатель", "cup": "Петля (чашка)",
    "cam": "Эксцентрик Ø15", "bolt": "Шток эксцентрика",
    "dowel": "Шкант 8×30", "lock": "Замок мебельный",
}


def _cyl_obj(parts: list[tuple[float, float, float]], n: int = 12) -> str:
    """Wavefront OBJ из цилиндров (r, z_top, z_bottom) вдоль оси Z."""
    verts: list[str] = []
    faces: list[str] = []
    base = 0
    for (r, z_top, z_bot) in parts:
        ring_t, ring_b = [], []
        for i in range(n):
            a = 2 * math.pi * i / n
            x, y = round(r * math.cos(a), 3), round(r * math.sin(a), 3)
            verts.append(f"v {x} {y} {z_top}")
            verts.append(f"v {x} {y} {z_bot}")
            ring_t.append(base + 2 * i + 1)
            ring_b.append(base + 2 * i + 2)
        verts.append(f"v 0 0 {z_top}")
        verts.append(f"v 0 0 {z_bot}")
        c_t, c_b = base + 2 * n + 1, base + 2 * n + 2
        for i in range(n):
            j = (i + 1) % n
            faces.append(f"f {ring_t[i]} {ring_b[i]} {ring_b[j]} {ring_t[j]}")   # бок
            faces.append(f"f {c_t} {ring_t[i]} {ring_t[j]}")                     # крышка
            faces.append(f"f {c_b} {ring_b[j]} {ring_b[i]}")                     # дно
        base += 2 * n + 2
    return "\n".join(verts + faces) + "\n"


def _mesh(kind: str, depth: float, dia: float) -> list[tuple[float, float, float]]:
    """Части тела метиза (r, z_top, z_bottom); сверление вдоль −Z от z=0."""
    r = dia / 2
    if kind == "confirmat":
        return [(3.5, 0, -depth), (5.0, 1.4, 0)]              # стержень + головка
    if kind in ("screw", "hscrew"):
        return [(min(r, 2.5), 0, -depth), (min(r * 2, 4.5), 0.9, 0)]
    if kind == "nail":
        return [(0.9, 0, -depth), (1.8, 0.5, 0)]
    if kind == "shelfpin":
        return [(2.5, 0, -depth), (3.5, 5, 0)]                # штифт + носик под полку
    if kind == "cup":
        return [(r, 0, -depth)]                               # чашка петли
    if kind == "cam":
        return [(r, 0, -depth)]                               # корпус эксцентрика
    if kind == "bolt":
        return [(3.5, 0, -depth), (4.5, 0, -3)]               # шток с головкой в чашку
    if kind == "dowel":
        return [(4.0, 10, -20)]                               # шкант: 20 в торец, 10 в пласть
    if kind == "lock":
        return [(9.0, 0, -depth), (11.0, 1.5, 0)]             # цилиндр замка + фланец
    return [(r, 0, -depth)]


def _basis(v: tuple[float, float, float]) -> list[list[float]]:
    """Строки поворота (X,Y,Z-оси в мире) так, что локальная −Z = направление v."""
    z = (-v[0], -v[1], -v[2])
    up = (0.0, 1.0, 0.0) if abs(z[1]) < 0.9 else (1.0, 0.0, 0.0)
    # X = up × Z, Y = Z × X (правая тройка)
    x = (up[1] * z[2] - up[2] * z[1], up[2] * z[0] - up[0] * z[2], up[0] * z[1] - up[1] * z[0])
    ln = math.sqrt(sum(c * c for c in x)) or 1.0
    x = (x[0] / ln, x[1] / ln, x[2] / ln)
    y = (z[1] * x[2] - z[2] * x[1], z[2] * x[0] - z[0] * x[2], z[0] * x[1] - z[1] * x[0])
    return [list(x), list(y), list(z)]


def _matrix16(rot: list[list[float]], t: tuple[float, float, float]) -> list[float]:
    return [rot[0][0], rot[0][1], rot[0][2], 0,
            rot[1][0], rot[1][1], rot[1][2], 0,
            rot[2][0], rot[2][1], rot[2][2], 0,
            round(t[0], 2), round(t[1], 2), round(t[2], 2), 1]


def _vec(h: dict[str, Any]) -> tuple[float, float, float]:
    d = int(h["dir"])
    return {"x": (d, 0, 0), "y": (0, d, 0), "z": (0, 0, d)}[h["axis"]]


def build_fastener_objects(holes: list[dict[str, Any]],
                           bom_articles: dict[str, str] | None = None
                           ) -> list[dict[str, Any]]:
    """Присадки → объекты-метизы для .cfrn.

    Возвращает [{key, name, art, obj_name, obj_text, mtl_text, holes,
    instances: [matrix16]}]; holes — локальные {pos, dir, depth, diameter}.
    """
    bom_articles = bom_articles or {}
    groups: dict[tuple, dict[str, Any]] = {}

    def _add(kind: str, color, depth: float, dia: float,
             pos: tuple[float, float, float], v: tuple[float, float, float],
             local_holes: list[dict[str, Any]]):
        key = (kind, round(depth, 1), round(dia, 1))
        g = groups.get(key)
        if g is None:
            nm = _BOM_NAME[kind]
            g = groups[key] = {
                "key": f"{kind}_{depth:g}_{dia:g}", "kind": kind,
                "name": nm, "art": bom_articles.get(_BOM_NAME[kind], ""),
                "color": color, "obj_parts": _mesh(kind, depth, dia),
                "holes": local_holes, "instances": [],
            }
        g["instances"].append(_matrix16(_basis(v), pos))

    used: set[int] = set()
    hole_list = list(holes)
    # шканты: пара торец+пласть в одной точке → один инстанс с двумя отверстиями
    for i, h in enumerate(hole_list):
        if h["purpose"] != "шкант 8×30 (торец)":
            continue
        mate = next((j for j, q in enumerate(hole_list)
                     if j not in used and q["purpose"] == "шкант 8×30 (пласть)"
                     and abs(q["x"] - h["x"]) < 1 and abs(q["y"] - h["y"]) < 1
                     and abs(q["z"] - h["z"]) < 1), None)
        used.add(i)
        if mate is not None:
            used.add(mate)
        v = _vec(h)
        _add("dowel", (0.85, 0.71, 0.51), 30, 8, (h["x"], h["y"], h["z"]), v,
             [{"pos": {"x": 0, "y": 0, "z": 0}, "dir": {"x": 0, "y": 0, "z": -1},
               "depth": h["depth"], "diameter": h["diameter"]},
              {"pos": {"x": 0, "y": 0, "z": 0}, "dir": {"x": 0, "y": 0, "z": 1},
               "depth": 12, "diameter": h["diameter"]}])

    for i, h in enumerate(hole_list):
        if i in used or h["purpose"].startswith("шкант"):
            continue
        pk = _PURPOSE_KIND.get(h["purpose"])
        if pk is None:
            continue
        kind, color = pk
        v = _vec(h)
        _add(kind, color, h["depth"], h["diameter"], (h["x"], h["y"], h["z"]), v,
             [{"pos": {"x": 0, "y": 0, "z": 0}, "dir": {"x": 0, "y": 0, "z": -1},
               "depth": h["depth"], "diameter": h["diameter"]}])

    out: list[dict[str, Any]] = []
    for g in groups.values():
        obj_name = f"{g['name']} [{g['key']}]"
        kd = g["color"]
        g["obj_name"] = f"{obj_name}.obj"
        g["obj_text"] = (f"mtllib {obj_name}.mtl\nusemtl m0\n"
                         + _cyl_obj(g.pop("obj_parts")))
        g["mtl_text"] = (f"newmtl m0\nKd {kd[0]:.3f} {kd[1]:.3f} {kd[2]:.3f}\n"
                         "Ka 0.2 0.2 0.2\nNs 32\n")
        out.append(g)
    return out
