"""Генератор desk/table: столешница на панельных опорах ИЛИ на металлокаркасе.

Два исполнения:
- панельные опоры (по умолчанию): столешница + боковины-опоры + задняя царга;
- металлокаркас (`frame: "metal"` или `legs.type` содержит «металл»/«каркас»):
  опоры — фурнитура (не панели), панели = столешница (+ опц. передний экран
  `screen`). Реальные офисные столы на трубе 40×40 — этот режим.
"""

from __future__ import annotations

from typing import Any

from .base import read_carcass
from .helpers import build_project, panel


def _is_metal(spec: dict[str, Any]) -> bool:
    if spec.get("frame") == "metal":
        return True
    lt = str((spec.get("legs") or {}).get("type", "")).lower()
    return "металл" in lt or "каркас" in lt


def generate(spec: dict[str, Any]) -> dict[str, Any]:
    c = read_carcass(spec)
    top_z = spec.get("top_overhang")
    tz = tuple(top_z) if top_z else (0, c.D)
    top_bottom = c.H - c.T_top

    panels = [panel("Столешница", "top", "horizont", (0, c.W), (top_bottom, c.H), tz,
                    thickness=c.T_top, material=c.mat)]

    if _is_metal(spec):
        construction = "top_on_metal_frame"          # опоры — фурнитура (труба), не панель
        if spec.get("screen"):                        # передний экран (modesty)
            sh = spec.get("screen_height", 500)
            st = spec.get("screen_thickness", 16)
            mg = spec.get("screen_margin", 45)
            sz = spec.get("screen_z", 50)             # отступ экрана от переднего края по Z
            panels.append(panel("Передний экран", "screen", "front", (mg, c.W - mg),
                                (top_bottom - sh, top_bottom), (sz, sz + st),
                                thickness=st, material=c.mat))
    else:
        apron_h = spec.get("apron_height", 120)
        panels.append(panel("Опора левая", "side_left", "vertical", (0, c.T), (c.Hleg, top_bottom), (0, c.D), thickness=c.T, material=c.mat))
        panels.append(panel("Опора правая", "side_right", "vertical", (c.W - c.T, c.W), (c.Hleg, top_bottom), (0, c.D), thickness=c.T, material=c.mat))
        if spec.get("apron", True):
            panels.append(panel("Царга задняя", "back", "front", (c.T, c.W - c.T),
                                (top_bottom - apron_h, top_bottom), (c.D - c.T, c.D),
                                thickness=c.T, material=c.mat))
        construction = "top_on_supports"

    sec = [{"id": "main", "type": "desk",
            "dimensions": {"width": c.W - 2 * c.T, "height": top_bottom, "depth": c.D, "estimated": False},
            "elements": [p["name"] for p in panels]}]
    return build_project(spec, panels, sections=sec,
                         carcass_calc={"W": c.W, "D": c.D, "H": c.H, "T": c.T, "construction": construction})
