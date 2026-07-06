"""Генератор drawer_unit: короб + стек ящиков (короб на каждый) + верхняя ниша."""

from __future__ import annotations

from typing import Any

from .base import read_carcass
from .corpus import carcass_calc
from .helpers import build_project, carcass, facade_band, panel


def generate(spec: dict[str, Any]) -> dict[str, Any]:
    c = read_carcass(spec)
    section = (spec.get("sections") or [{"kind": "drawers", "drawers": 1}])[0]
    g = c.gap
    n = section["drawers"]

    # накладные фасады: полоса по высоте (перекрывает дно и крышку/столешницу)
    reveal = spec.get("facade_reveal", 2.0)
    band_bottom, band_top = facade_band(c.Hleg, c.H, c.T, has_overhang=bool(spec.get("top_overhang")),
                                        reveal=reveal)
    _fb = section.get("front_bottom")                             # низ нижнего фасада (Y-координата)
    fb = _fb if isinstance(_fb, (int, float)) and not isinstance(_fb, bool) else band_bottom
    heights = section.get("drawer_heights")
    if not heights:
        top = section.get("front_top", band_top)
        h = (top - fb - (n - 1) * g) / n
        heights = [round(h, 2)] * n

    # параметры короба ящика (drawer-construction, не выводятся из габарита)
    guide_gap = section.get("guide_gap", 14.5)
    box_z1 = section.get("box_z1", 0)                             # короб прижат к фасаду (z=0)
    box_depth = section.get("box_depth", round(c.D - c.T_back - box_z1 - 30, 2))
    box_y_off = section.get("box_y_offset", c.T)
    box_h = section.get("box_height", round(min(heights) * 0.52, 2))
    box_back = section.get("box_back_thickness", c.T)
    box_bot = section.get("box_bottom_thickness", c.T)

    panels = carcass(c.W, c.D, c.H, c.T, c.T_back, c.Hleg, c.mat, c.mat_back,
                     leg_as_panel=c.leg_as_panel, leg_type=c.leg_type,
                     socle_recess=spec.get("socle_recess", 50))

    ox1, ox2 = c.T, c.W - c.T                # внутренний проём (короб между боковинами)
    fx1, fx2 = round(reveal, 2), round(c.W - reveal, 2)   # накладной фасад — во всю ширину
    drawers_meta: list[dict[str, Any]] = []
    front_names: list[str] = []
    y = fb
    for k, h in enumerate(heights, start=1):
        fy1, fy2 = y, y + h
        panels.append(panel(f"Фасад ящик {k}", "drawer_front", "front", (fx1, fx2), (fy1, fy2), (-c.T, 0),
                            thickness=c.T, material=c.mat, section_id="drawer_stack", estimated=True))
        front_names.append(f"Фасад ящик {k}")
        bxl1 = ox1 + guide_gap
        bxl2 = bxl1 + c.T
        bxr2 = ox2 - guide_gap
        bxr1 = bxr2 - c.T
        by1 = fy1 + box_y_off
        by2 = by1 + box_h
        bz2 = box_z1 + box_depth
        panels.append(panel(f"Ящик {k} дно", "drawer_bottom", "horizont", (bxl2, bxr1), (by1, by1 + box_bot), (box_z1, bz2),
                            thickness=box_bot, material=c.mat, section_id="drawer_stack", estimated=True))
        panels.append(panel(f"Ящик {k} боковина левая", "drawer_side_left", "vertical", (bxl1, bxl2), (by1, by2), (box_z1, bz2),
                            thickness=c.T, material=c.mat, section_id="drawer_stack", estimated=True))
        panels.append(panel(f"Ящик {k} боковина правая", "drawer_side_right", "vertical", (bxr1, bxr2), (by1, by2), (box_z1, bz2),
                            thickness=c.T, material=c.mat, section_id="drawer_stack", estimated=True))
        # накладная стенка перекрывает торцы боковин (AKD-181) — есть куда крепить
        panels.append(panel(f"Ящик {k} задняя", "drawer_back", "front", (bxl1, bxr2), (by1, by2), (bz2, bz2 + box_back),
                            thickness=box_back, material=c.mat, section_id="drawer_stack", estimated=True))
        drawers_meta.append({"id": f"drawer_{k}", "count": 1,
                             "guide_type": section.get("guide_type", "шариковые"), "soft_close": False, "lock": False,
                             "dimensions": {"width": round(bxr1 - bxl2, 2), "height": box_h, "depth": box_depth},
                             "position": {"x": round(bxl2, 2), "y": round(by1, 2), "z": round(box_z1, 2)}, "estimated": True})
        y = fy2 + g

    sections_meta = [{"id": "drawer_stack", "type": "drawer_stack",
                      "dimensions": {"width": c.W - 2 * c.T, "height": round(y - g - fb, 2), "depth": box_depth, "estimated": True},
                      "elements": front_names}]

    # перекрытие стека (AKD-187): полка над ящиками всегда, когда стек не
    # доходит до крышки (cover_top: false — отключить явно)
    ny1 = fb + sum(heights) + (n - 1) * g         # верх верхнего фасада
    if section.get("cover_top", True) and ny1 + c.T <= (c.H - c.T) - 40:
        panels.append(panel("Полка под нишей", "shelf", "horizont", (c.T, c.W - c.T), (ny1, ny1 + c.T),
                            (section.get("niche_z_front", 0), c.D - c.T_back),
                            thickness=c.T, material=c.mat, section_id="top_open", estimated=True))
        # верхний фасад продлевается на T и перекрывает торец полки (AKD-191)
        top_f = max((q for q in panels if q.get("type") == "drawer_front"),
                    key=lambda q: q["placement"]["y2"], default=None)
        if top_f is not None:
            top_f["placement"]["y2"] = round(ny1 + c.T, 2)
            top_f["dimensions"]["height"] = round(top_f["dimensions"]["height"] + c.T, 2)
        sections_meta.append({"id": "top_open", "type": "open",
                              "dimensions": {"width": c.W - 2 * c.T, "height": round((c.H - c.T) - (ny1 + c.T), 2),
                                             "depth": round(c.D - c.T_back, 2), "estimated": True},
                              "elements": ["Ниша верх", "Полка под нишей"]})

    cc = carcass_calc(c)
    cc["drawer_heights"] = heights
    cc["drawer_box"] = f"{round((c.W - 2 * c.T) - 2 * guide_gap - 2 * c.T, 2)}×{box_depth}×{box_h}"
    return build_project(spec, panels, sections=sections_meta, drawers=drawers_meta, carcass_calc=cc)
