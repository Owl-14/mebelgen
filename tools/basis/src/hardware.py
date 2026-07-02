"""Расчёт присадок (сверловки) под фурнитуру из геометрии проекта (AKD-88).

Формат-независимое ядро: по `project` (панели + hardware) считает КООРДИНАТЫ
отверстий под ручки, петли, полкодержатели и стяжки — это то, что сверлит ЧПУ и
что нужно для производственной модели. Кодирование в `.cfrn` (table.holes +
objType 5) — отдельный шаг (нужна облачная проверка). 3D-меш фурнитуры берётся из
каталога БАЗИС (десктоп-импортёр, AKD-14) — здесь не строим.

Каждое отверстие: {panel, purpose, x, y, z, diameter, depth, axis, dir}
где (x,y,z) — точка входа сверла в мировых координатах, axis∈{x,y,z}, dir=±1.
"""
from __future__ import annotations

from typing import Any

_VERT = ("side_left", "side_right", "vertical_partition")
_FACADE = ("door_front", "drawer_front")


def _hole(panel: str, purpose: str, x: float, y: float, z: float,
          diameter: float, depth: float, axis: str, dir: int) -> dict[str, Any]:
    return {"panel": panel, "purpose": purpose,
            "x": round(x, 1), "y": round(y, 1), "z": round(z, 1),
            "diameter": diameter, "depth": depth, "axis": axis, "dir": dir}


def _n_hinges(h: float) -> int:
    if h <= 900:
        return 2
    if h <= 1600:
        return 3
    if h <= 2000:
        return 4
    return 5


def _hinge_levels(y1: float, y2: float, n: int, margin: float = 100.0) -> list[float]:
    lo, hi = y1 + margin, y2 - margin
    if n <= 1:
        return [(lo + hi) / 2]
    return [lo + (hi - lo) * k / (n - 1) for k in range(n)]


def compute_drilling(project: dict[str, Any]) -> list[dict[str, Any]]:
    panels = project.get("panels", [])
    hw = project.get("hardware", {}) or {}
    holes: list[dict[str, Any]] = []

    by_type: dict[str, list[dict[str, Any]]] = {}
    for p in panels:
        by_type.setdefault(p.get("type", ""), []).append(p)
    verticals = [p for p in panels if p.get("type") in _VERT]

    # --- Ручки: 2 винта на фасад по межцентровому = handles.size ---
    handles = hw.get("handles") or {}
    size = float(handles.get("size") or 0)
    off = float(handles.get("offset_from_top", 40))
    if (handles.get("count") or 0) > 0 and size > 0:
        for p in panels:
            t = p.get("type")
            if t not in _FACADE:
                continue
            pl = p["placement"]
            zc = (pl["z1"] + pl["z2"]) / 2
            if t == "drawer_front":
                cx = (pl["x1"] + pl["x2"]) / 2
                y = pl["y2"] - off
                for dx in (-size / 2, size / 2):
                    holes.append(_hole(p["name"], "ручка (винт)", cx + dx, y, zc, 5, 25, "z", -1))
            else:  # door_front: ручка у кромки открывания, 2 винта по вертикали
                nm = p["name"].lower()
                hx = pl["x1"] + 40 if "прав" in nm else pl["x2"] - 40
                cy = (pl["y1"] + pl["y2"]) / 2
                for dy in (-size / 2, size / 2):
                    holes.append(_hole(p["name"], "ручка (винт)", hx, cy + dy, zc, 5, 25, "z", -1))

    # --- Петли: чашка Ø35 на фасаде + 2 отв ответной планки на боковине ---
    for p in panels:
        if p.get("type") != "door_front":
            continue
        pl = p["placement"]
        h = pl["y2"] - pl["y1"]
        n = _n_hinges(h)
        nm = p["name"].lower()
        hinge_left = "прав" not in nm            # левая дверь — петли слева
        cup_x = pl["x1"] + 22 if hinge_left else pl["x2"] - 22
        # ближайшая вертикаль со стороны петель
        side_x = pl["x1"] if hinge_left else pl["x2"]
        side = min(verticals, key=lambda v: abs(((v["placement"]["x1"] + v["placement"]["x2"]) / 2) - side_x), default=None)
        for y in _hinge_levels(pl["y1"], pl["y2"], n):
            holes.append(_hole(p["name"], "петля (чашка Ø35)", cup_x, y, (pl["z1"] + pl["z2"]) / 2, 35, 12, "z", -1))
            if side is not None:
                sx = side["placement"]["x2"] if hinge_left else side["placement"]["x1"]
                sdir = -1 if hinge_left else 1
                for dy in (-16, 16):
                    holes.append(_hole(side["name"], "петля (планка)", sx, y + dy, (pl["z1"] + pl["z2"]) / 2, 5, 12, "x", sdir))

    # --- Полкодержатели: 4 отв на съёмную полку (2 на каждую боковину/перегородку) ---
    for p in panels:
        if p.get("type") != "shelf":
            continue
        pl = p["placement"]
        yc = (pl["y1"] + pl["y2"]) / 2
        zf, zb = pl["z1"] + 37, pl["z2"] - 37
        for edge_x, want in ((pl["x1"], "x2"), (pl["x2"], "x1")):
            v = min((v for v in verticals if abs(v["placement"][want] - edge_x) < 1.0),
                    key=lambda v: abs(v["placement"][want] - edge_x), default=None)
            if v is None:
                continue
            into = 1 if want == "x2" else -1     # x2 совпал с левой гранью полки → сверлим в +X
            for z in (zf, zb):
                holes.append(_hole(v["name"], "полкодержатель", edge_x, yc, z, 5, 10, "x", into))

    # --- Стяжки: дно/крышка ↔ боковины/перегородки. Боковина стоит на дне
    #     (стык по Y, X-диапазоны вложены) → конфирмат вдоль Y в торец боковины. ---
    for p in panels:
        if p.get("type") not in ("bottom", "top"):
            continue
        pl = p["placement"]
        for v in verticals:
            vp = v["placement"]
            if not (vp["x1"] >= pl["x1"] - 1 and vp["x2"] <= pl["x2"] + 1):
                continue
            if p["type"] == "bottom" and abs(vp["y1"] - pl["y2"]) < 1:
                y, ydir = pl["y1"], 1          # снизу дна вверх в торец боковины
            elif p["type"] == "top" and abs(vp["y2"] - pl["y1"]) < 1:
                y, ydir = pl["y2"], -1         # сверху крышки вниз в торец боковины
            else:
                continue
            xc = (vp["x1"] + vp["x2"]) / 2
            for z in (pl["z1"] + 50, pl["z2"] - 50):
                holes.append(_hole(p["name"], "стяжка (конфирмат)", xc, y, z, 7, 50, "y", ydir))

    # --- Направляющие ящиков: винты на боковинах/перегородках у КОРОБА ---
    # (по коробу, а не по фасаду: накладной фасад шире проёма и не задаёт колонку)
    for d in project.get("drawers", []):
        pos, dim = d.get("position") or {}, d.get("dimensions") or {}
        if not pos or not dim:
            continue
        box_l, box_r = float(pos["x"]), float(pos["x"]) + float(dim["width"])
        guide_y = float(pos["y"]) + 12
        left = min((v for v in verticals if v["placement"]["x2"] <= box_l + 1),
                   key=lambda v: box_l - v["placement"]["x2"], default=None)
        right = min((v for v in verticals if v["placement"]["x1"] >= box_r - 1),
                    key=lambda v: v["placement"]["x1"] - box_r, default=None)
        for v, inx in ((left, 1), (right, -1)):
            if v is None:
                continue
            vp = v["placement"]
            sx = vp["x2"] if inx > 0 else vp["x1"]
            for z in (vp["z1"] + 30, (vp["z1"] + vp["z2"]) / 2, vp["z2"] - 50):
                holes.append(_hole(v["name"], "направляющая (винт)", sx, guide_y, z, 5, 12, "x", inx))
    return holes


def drilling_summary(holes: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for h in holes:
        out[h["purpose"]] = out.get(h["purpose"], 0) + 1
    return out
