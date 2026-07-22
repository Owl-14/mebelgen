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
    "короб ящика (саморез)": ("screw", (0.39, 0.42, 0.44)),
    "направляющая (винт)": ("screw", (0.39, 0.42, 0.44)),
    "направляющая (саморез)": ("screw", (0.39, 0.42, 0.44)),
    "петля (планка)": ("screw", (0.39, 0.42, 0.44)),
    "петля (накол чашки)": ("screw", (0.39, 0.42, 0.44)),
    "ручка (винт)": ("hscrew", (0.82, 0.84, 0.85)),
    "задник (гвоздь)": ("nail", (0.60, 0.63, 0.65)),
    "задник (саморез)": ("screw", (0.39, 0.42, 0.44)),
    "полкодержатель": ("shelfpin", (0.82, 0.84, 0.85)),
    "эксцентрик полки (чашка Ø20)": ("cam", (0.90, 0.90, 0.88)),
    "эксцентрик полки (шток)": ("bolt", (0.60, 0.63, 0.65)),
    "евровинт (проход Ø8)": ("confirmat", (0.60, 0.63, 0.65)),
    "конфирмат": ("confirmat", (0.60, 0.63, 0.65)),
    "петля (чашка Ø35)": ("cup", (0.55, 0.57, 0.60)),
    "эксцентрик (чашка Ø15)": ("cam", (0.79, 0.70, 0.49)),
    # «эксцентрик (шток)» + «эксцентрик (канал Ø8)» собираются парой → bolt
    "фасадная стяжка (эксцентрик Ø15)": ("cam", (0.79, 0.70, 0.49)),
    "фасадная стяжка (шток)": ("bolt", (0.60, 0.63, 0.65)),
    "замок (цилиндр Ø18)": ("lock", (0.82, 0.84, 0.85)),
    "штангодержатель (саморез)": ("screw", (0.39, 0.42, 0.44)),
    "опора (саморез)": ("screw", (0.39, 0.42, 0.44)),
    "каркас (саморез)": ("screw", (0.39, 0.42, 0.44)),
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
        return [(4.0, 34, -depth)]                # шток Ø8 через плоскость стыка
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


def _box_obj(sx: float, sy: float, sz: float) -> str:
    """Wavefront OBJ бокса 0..sx × −sy/2..sy/2 × 0..sz (центрован по Y —
    симметрия нужна развороту модели правыми матрицами, AKD-188)."""
    y0, y1 = -sy / 2, sy / 2
    v = [(0, y0, 0), (sx, y0, 0), (sx, y1, 0), (0, y1, 0),
         (0, y0, sz), (sx, y0, sz), (sx, y1, sz), (0, y1, sz)]
    faces = [(1, 4, 3, 2), (5, 6, 7, 8), (1, 2, 6, 5),
             (2, 3, 7, 6), (3, 4, 8, 7), (4, 1, 5, 8)]
    lines = [f"v {p[0]} {p[1]} {p[2]}" for p in v]
    lines += [f"f {a} {b} {c} {d}" for a, b, c, d in faces]
    return "\n".join(lines) + "\n"


# kind тела фурнитуры → (имя для спецификации, запрос в базу, цвет Kd, форма)
_BODY_KINDS = {
    "handle": ("Ручка-скоба", "ручка скоба", (0.55, 0.57, 0.60), "box"),
    "rod": ("Штанга-вешало", "штанга", (0.72, 0.74, 0.77), "cyl"),
    "rod_bracket": ("Штангодержатель", "штангодержатель", (0.48, 0.51, 0.55), "box"),
    "leg": ("Опора регулируемая", "опора регулируемая", (0.22, 0.23, 0.24), "cyl"),
    "frame_leg": ("Металлокаркас: стойка 40×40", "каркас", (0.22, 0.23, 0.24), "box"),
    "frame_rail": ("Металлокаркас: царга 40×40", "каркас", (0.22, 0.23, 0.24), "box"),
}


def build_hardware_bodies(project: dict[str, Any],
                          bom_articles: dict[str, str] | None = None
                          ) -> list[dict[str, Any]]:
    """Тела фурнитуры (AKD-183) для .cfrn: штанга, держатели, опоры, каркас.

    Источник — hardware_geometry (те же тела, что рисует Studio). Механика
    эталона native_cabinet.cfrn: objType 5 + OBJ в models/, инстансы матрицами.
    Возвращает список групп в формате build_fastener_objects (holes пустые).
    """
    from .hardware_geometry import compute_hardware_geometry
    bom_articles = bom_articles or {}
    groups: dict[tuple, dict[str, Any]] = {}
    for g in compute_hardware_geometry(project):
        meta = _BODY_KINDS.get(g["kind"])
        if meta is None:
            continue
        label, _query, color, shape = meta
        sx, sy, sz = g["x2"] - g["x1"], g["y2"] - g["y1"], g["z2"] - g["z1"]
        if shape == "cyl":
            # цилиндр вдоль длинной оси бокса
            axis, L = max((("x", sx), ("y", sy), ("z", sz)), key=lambda t: t[1])
            r = min(v for a, v in (("x", sx), ("y", sy), ("z", sz)) if a != axis) / 2
            key = (g["kind"], round(L, 1), round(r, 1))
            grp = groups.get(key)
            if grp is None:
                grp = groups[key] = {
                    "key": f"{g['kind']}_{L:g}_{r:g}", "kind": g["kind"],
                    "name": f"{label} L={round(L)}", "label": label,
                    "art": bom_articles.get(label, ""), "color": color,
                    "obj_body": _cyl_obj([(r, L, 0)]), "holes": [], "instances": [],
                }
            # локальная +Z → мировая ось цилиндра (через _basis: локальная −Z = v)
            v = {"x": (-1.0, 0.0, 0.0), "y": (0.0, -1.0, 0.0), "z": (0.0, 0.0, -1.0)}[axis]
            cx = (g["x1"] + g["x2"]) / 2
            cy = (g["y1"] + g["y2"]) / 2
            cz = (g["z1"] + g["z2"]) / 2
            start = {"x": (g["x1"], cy, cz), "y": (cx, g["y1"], cz),
                     "z": (cx, cy, g["z1"])}[axis]
            grp["instances"].append(_matrix16(_basis(v), start))
        else:
            key = (g["kind"], round(sx, 1), round(sy, 1), round(sz, 1))
            grp = groups.get(key)
            if grp is None:
                grp = groups[key] = {
                    "key": f"{g['kind']}_{sx:g}x{sy:g}x{sz:g}", "kind": g["kind"],
                    "name": f"{label} {round(sx)}×{round(sy)}×{round(sz)}", "label": label,
                    "art": bom_articles.get(label, ""), "color": color,
                    "obj_body": _box_obj(sx, sy, sz), "holes": [], "instances": [],
                }
            grp["instances"].append(_matrix16(
                [[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]],
                (g["x1"], (g["y1"] + g["y2"]) / 2, g["z1"])))   # бокс центрован по Y

    out: list[dict[str, Any]] = []
    for g in groups.values():
        obj_name = f"{g['name']} [{g['key']}]"
        kd = g["color"]
        g["obj_name"] = f"{obj_name}.obj"
        g["obj_text"] = (f"mtllib {obj_name}.mtl\nusemtl m0\n" + g.pop("obj_body"))
        g["mtl_text"] = (f"newmtl m0\nKd {kd[0]:.3f} {kd[1]:.3f} {kd[2]:.3f}\n"
                         "Ka 0.2 0.2 0.2\nNs 32\n")
        out.append(g)
    return out


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
        mate_depth = 12.0
        if mate is not None:
            used.add(mate)
            mate_depth = float(hole_list[mate]["depth"])   # пласть: 11 (реверс) / 12
        v = _vec(h)
        _add("dowel", (0.85, 0.71, 0.51), 30, 8, (h["x"], h["y"], h["z"]), v,
             [{"pos": {"x": 0, "y": 0, "z": 0}, "dir": {"x": 0, "y": 0, "z": -1},
               "depth": h["depth"], "diameter": h["diameter"]},
              {"pos": {"x": 0, "y": 0, "z": 0}, "dir": {"x": 0, "y": 0, "z": 1},
               "depth": mate_depth, "diameter": h["diameter"]}])

    # минификс (AKD-202): шток в пласти + канал в торце соосны в одной точке
    # плоскости стыка → один болт Ø8 с двумя отверстиями
    for i, h in enumerate(hole_list):
        if h["purpose"] != "эксцентрик (шток)" or i in used:
            continue
        mate = next((j for j, q in enumerate(hole_list)
                     if j not in used and q["purpose"] == "эксцентрик (канал Ø8)"
                     and q["axis"] == h["axis"]
                     and abs(q["x"] - h["x"]) < 1 and abs(q["y"] - h["y"]) < 1
                     and abs(q["z"] - h["z"]) < 1), None)
        used.add(i)
        if mate is not None:
            used.add(mate)
        v = _vec(h)
        _add("bolt", (0.60, 0.63, 0.65), 45, 8, (h["x"], h["y"], h["z"]), v,
             [{"pos": {"x": 0, "y": 0, "z": 0}, "dir": {"x": 0, "y": 0, "z": -1},
               "depth": h["depth"], "diameter": h["diameter"]},
              {"pos": {"x": 0, "y": 0, "z": 0}, "dir": {"x": 0, "y": 0, "z": 1},
               "depth": 34, "diameter": 8}])

    # конфирмат (AKD-287): проход Ø8 сквозь пласть + тело Ø5×35 в торец — один
    # винт с двумя отверстиями (соосны вдоль оси сверления, разнесены на толщину)
    for i, h in enumerate(hole_list):
        if h["purpose"] != "евровинт (проход Ø8)" or i in used:
            continue
        t = float(h["depth"])                          # толщина прошиваемой детали
        mate = next((j for j, q in enumerate(hole_list)
                     if j not in used and q["purpose"] == "конфирмат"
                     and q["axis"] == h["axis"]
                     and abs((q["x"], q["y"], q["z"])[("x", "y", "z").index(h["axis"])]
                             - (h["x"], h["y"], h["z"])[("x", "y", "z").index(h["axis"])]) < t + 1
                     and all(abs(q[k] - h[k]) < 1 for k in "xyz" if k != h["axis"])), None)
        used.add(i)
        body_depth = 35.0
        if mate is not None:
            used.add(mate)
            body_depth = float(hole_list[mate]["depth"])
        v = _vec(h)
        _add("confirmat", (0.60, 0.63, 0.65), 50, 7, (h["x"], h["y"], h["z"]), v,
             [{"pos": {"x": 0, "y": 0, "z": 0}, "dir": {"x": 0, "y": 0, "z": -1},
               "depth": t, "diameter": h["diameter"]},
              {"pos": {"x": 0, "y": 0, "z": -t}, "dir": {"x": 0, "y": 0, "z": -1},
               "depth": body_depth, "diameter": 5}])

    # гвоздь задника (AKD-287): прокол Ø3 сквозь ДВП/ХДФ + тело Ø1×12 в торец
    for i, h in enumerate(hole_list):
        if h["purpose"] != "задник (прокол Ø3)" or i in used:
            continue
        t = float(h["depth"])
        mate = next((j for j, q in enumerate(hole_list)
                     if j not in used and q["purpose"] == "задник (гвоздь)"
                     and abs(q["x"] - h["x"]) < 1 and abs(q["y"] - h["y"]) < 1
                     and abs(q["z"] - h["z"]) < t + 2), None)
        used.add(i)
        body_depth, body_d = 12.0, 1.0
        if mate is not None:
            used.add(mate)
            body_depth = float(hole_list[mate]["depth"])
            body_d = float(hole_list[mate]["diameter"])
        v = _vec(h)
        _add("nail", (0.60, 0.63, 0.65), 16, 1.5, (h["x"], h["y"], h["z"]), v,
             [{"pos": {"x": 0, "y": 0, "z": 0}, "dir": {"x": 0, "y": 0, "z": -1},
               "depth": t, "diameter": h["diameter"]},
              {"pos": {"x": 0, "y": 0, "z": -t}, "dir": {"x": 0, "y": 0, "z": -1},
               "depth": body_depth, "diameter": body_d}])

    for i, h in enumerate(hole_list):
        if i in used or h["purpose"].startswith("шкант") \
                or h["purpose"] == "эксцентрик (канал Ø8)":
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
