"""Генератор round_table: круглый стол на цилиндрическом пьедестале.

Круглая столешница (`shape: circle`) + цилиндрический пьедестал (`shape: cylinder`)
+ опц. база-диск. placement = габаритный бокс (квадрат×высота) — валидаторы
работают по AABB. Физическая сборка круга/цилиндра в БАЗИС требует поддержки
контура в импортёре (см. AKD-42, AKD-13/14).
"""

from __future__ import annotations

from typing import Any

from .base import read_carcass
from .helpers import build_project, panel


def generate(spec: dict[str, Any]) -> dict[str, Any]:
    c = read_carcass(spec)
    diameter = c.W                          # width=depth=диаметр
    R = diameter / 2
    cx = cz = diameter / 2
    top_t = spec.get("top_thickness", c.T)
    ped_d = spec.get("pedestal_diameter", round(diameter * 0.5, 2))
    pr = ped_d / 2
    use_base = spec.get("base", False)
    base_t = spec.get("base_thickness", c.T)
    base_d = spec.get("base_diameter", round(diameter * 0.8, 2))
    br = base_d / 2

    panels = []
    ped_y1 = base_t if use_base else 0
    if use_base:
        panels.append(panel("Основание (диск)", "bottom", "horizont",
                            (cx - br, cx + br), (0, base_t), (cz - br, cz + br),
                            thickness=base_t, material=c.mat, shape="circle", radius=br))
    panels.append(panel("Пьедестал", "vertical_partition", "vertical",
                        (cx - pr, cx + pr), (ped_y1, c.H - top_t), (cz - pr, cz + pr),
                        thickness=ped_d, material=c.mat, shape="cylinder", radius=pr, estimated=True))
    panels.append(panel("Столешница", "top", "horizont",
                        (0, diameter), (c.H - top_t, c.H), (0, diameter),
                        thickness=top_t, material=c.mat, shape="circle", radius=R))

    sec = [{"id": "main", "type": "round_table",
            "dimensions": {"width": diameter, "height": c.H, "depth": diameter, "estimated": False},
            "elements": [p["name"] for p in panels]}]
    cc = {"diameter": diameter, "H": c.H, "top_thickness": top_t,
          "pedestal_diameter": ped_d, "construction": "round_pedestal"}
    return build_project(spec, panels, sections=sec, carcass_calc=cc)
