"""Видимая геометрия фурнитуры для 3D-вьювера: направляющие ящиков и петли дверей.

Строит РЕАЛЬНЫЕ детали механизмов (боксы в мм, мировые координаты) из project:
- направляющие: корпусный полоз (на боковине/перегородке) + полоз ящика (на коробе)
  в зазоре guide_gap; длина по hardware.drawer_guides.length_mm или стандартная;
- петли: чашка Ø35 на фасаде + плечо через кромку + ответная планка на боковине.

Правила — rules/hardware.md. Это слой ВИЗУАЛИЗАЦИИ (наш вьювер/лист):
в .cfrn меши фурнитуры не пишем — 3D из каталога БАЗИС (AKD-14, лицензия).
"""

from __future__ import annotations

from typing import Any

from .hardware import _hinge_levels, _n_hinges

_VERT = ("side_left", "side_right", "vertical_partition")

# металлики для вьювера
COL_RAIL = "#8f969e"
COL_HINGE = "#7a828b"
COL_CUP = "#666d75"

_STD_LENGTHS = (500, 450, 400, 350, 300, 250)

RAIL_H = {"шариковые": 45.0, "роликовые": 40.0}      # прочее → 45


def _box(kind: str, name: str, color: str,
         x1: float, x2: float, y1: float, y2: float, z1: float, z2: float) -> dict[str, Any]:
    return {"kind": kind, "name": name, "color": color,
            "x1": round(min(x1, x2), 1), "x2": round(max(x1, x2), 1),
            "y1": round(min(y1, y2), 1), "y2": round(max(y1, y2), 1),
            "z1": round(min(z1, z2), 1), "z2": round(max(z1, z2), 1)}


def _guide_length(pref: float | None, z1: float, z_limit: float, box_depth: float) -> float:
    """Длина направляющей: заявленная, если влезает; иначе стандартная вниз."""
    if pref and z1 + pref <= z_limit + 0.5:
        return float(pref)
    for L in _STD_LENGTHS:
        if z1 + L <= z_limit + 0.5 and L <= box_depth + 60:
            return float(L)
    return max(150.0, min(box_depth, z_limit - z1))


def compute_hardware_geometry(project: dict[str, Any]) -> list[dict[str, Any]]:
    panels = [p for p in project.get("panels", []) if isinstance(p.get("placement"), dict)]
    hw = project.get("hardware", {}) or {}
    out: list[dict[str, Any]] = []

    verticals = [p for p in panels if p.get("type") in _VERT]
    zs2 = max((p["placement"]["z2"] for p in panels), default=0)
    backs = [p for p in panels if p.get("type") == "back"]
    z_limit = min((b["placement"]["z1"] for b in backs), default=zs2)

    # --- направляющие: пара полозьев на каждый ящик ---
    guides = hw.get("drawer_guides") or {}
    pref_len = guides.get("length_mm")
    for d in project.get("drawers", []):
        pos, dim = d.get("position") or {}, d.get("dimensions") or {}
        if not pos or not dim:
            continue
        gtype = str(d.get("guide_type") or guides.get("type") or "шариковые").lower()
        rail_h = RAIL_H.get(gtype, 45.0)
        bx1, by1, bz1 = float(pos["x"]), float(pos["y"]), float(pos["z"])
        bw, bd = float(dim["width"]), float(dim["depth"])
        # внешние грани боковин короба (дно между боковинами: короб шире на 2 борта)
        box_l, box_r = bx1 - 16.0, bx1 + bw + 16.0
        L = _guide_length(pref_len, bz1, z_limit, bd)
        y1 = by1 + 5.0
        y2 = y1 + rail_h
        dy2 = y1 + 13.0                                    # полоз ящика — нижняя треть
        for side, bxo in (("left", box_l), ("right", box_r)):
            # ближайшая вертикаль с этой стороны
            if side == "left":
                v = max((v for v in verticals if v["placement"]["x2"] <= bxo + 0.6),
                        key=lambda q: q["placement"]["x2"], default=None)
                if v is None:
                    continue
                fx = v["placement"]["x2"]                  # внутренняя грань корпуса
                out.append(_box("guide_corpus", f"{d['id']} направляющая (корпус, {side})",
                                COL_RAIL, fx, fx + 7, y1, y2, bz1, bz1 + L))
                out.append(_box("guide_drawer", f"{d['id']} направляющая (ящик, {side})",
                                COL_RAIL, bxo - 6, bxo, y1, dy2, bz1, bz1 + min(L, bd)))
            else:
                v = min((v for v in verticals if v["placement"]["x1"] >= bxo - 0.6),
                        key=lambda q: q["placement"]["x1"], default=None)
                if v is None:
                    continue
                fx = v["placement"]["x1"]
                out.append(_box("guide_corpus", f"{d['id']} направляющая (корпус, {side})",
                                COL_RAIL, fx - 7, fx, y1, y2, bz1, bz1 + L))
                out.append(_box("guide_drawer", f"{d['id']} направляющая (ящик, {side})",
                                COL_RAIL, bxo, bxo + 6, y1, dy2, bz1, bz1 + min(L, bd)))

    # --- штанга-вешало (AKD-177): труба + держатели/рельса ---
    for rod in hw.get("rods") or []:
        rid = rod.get("id", "rod")
        x1, x2 = float(rod["x1"]), float(rod["x2"])
        y1, y2 = float(rod["y1"]), float(rod["y2"])
        z1, z2 = float(rod["z1"]), float(rod["z2"])
        out.append(_box("rod", f"{rid}: штанга", COL_RAIL, x1, x2, y1, y2, z1, z2))
        yc, zc, xc = (y1 + y2) / 2, (z1 + z2) / 2, (x1 + x2) / 2
        if rod.get("axis") == "z":
            # выдвижная: монтажная рельса над трубой до горизонта выше
            host_y = min((p["placement"]["y1"] for p in panels
                          if p.get("type") in ("shelf", "top", "bottom")
                          and 5 <= p["placement"]["y1"] - y2 <= 120
                          and p["placement"]["x1"] - 1 <= xc <= p["placement"]["x2"] + 1),
                         default=y2 + 20)
            out.append(_box("rod_bracket", f"{rid}: рельса", COL_HINGE,
                            xc - 16, xc + 16, y2, host_y, z1, z2))
        else:
            for ex in (x1, x2):
                px1, px2 = (ex, ex + 5) if ex == x1 else (ex - 5, ex)
                out.append(_box("rod_bracket", f"{rid}: держатель", COL_HINGE,
                                px1, px2, yc - 25, yc + 25, zc - 25, zc + 25))

    # --- петли: чашка + плечо + ответная планка на каждую точку ---
    for p in panels:
        if p.get("type") != "door_front":
            continue
        pl = p["placement"]
        h = pl["y2"] - pl["y1"]
        n = _n_hinges(h)
        nm = str(p.get("name", "")).lower()
        hinge_left = "прав" not in nm
        cup_x = pl["x1"] + 22 if hinge_left else pl["x2"] - 22
        door_in = pl["z2"]                                  # внутренняя плоскость фасада
        side_x = pl["x1"] if hinge_left else pl["x2"]
        side = min(verticals,
                   key=lambda v: abs(((v["placement"]["x1"] + v["placement"]["x2"]) / 2) - side_x),
                   default=None)
        for y in _hinge_levels(pl["y1"], pl["y2"], n):
            out.append(_box("hinge_cup", f"{p['name']}: петля чашка", COL_CUP,
                            cup_x - 17.5, cup_x + 17.5, y - 17.5, y + 17.5,
                            door_in - 12, door_in))
            if side is None:
                continue
            sp = side["placement"]
            fx = sp["x2"] if hinge_left else sp["x1"]       # внутренняя грань боковины
            # плечо: от чашки через кромку двери внутрь корпуса
            ax1, ax2 = (fx, cup_x + 10) if hinge_left else (cup_x - 10, fx)
            out.append(_box("hinge_arm", f"{p['name']}: петля плечо", COL_HINGE,
                            ax1, ax2, y - 9, y + 9, door_in, door_in + 42))
            # ответная планка на боковине (отступ от фронта 37, высота 60, толщина 8)
            px1, px2 = (fx, fx + 8) if hinge_left else (fx - 8, fx)
            out.append(_box("hinge_plate", f"{p['name']}: петля планка", COL_HINGE,
                            px1, px2, y - 30, y + 30, door_in + 26, door_in + 48))
    return out


def hardware_geometry_summary(parts: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for p in parts:
        out[p["kind"]] = out.get(p["kind"], 0) + 1
    return out
