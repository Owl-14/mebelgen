"""Генератор door_unit: короб + полки + распашной фасад (1 или 2 двери)."""

from __future__ import annotations

from typing import Any

from .base import read_carcass
from .columns import rod_in_column
from .corpus import carcass_calc, cavity_section
from .helpers import build_project, carcass, overlay_door, panel, shelf_levels, shelves


def generate(spec: dict[str, Any]) -> dict[str, Any]:
    c = read_carcass(spec)
    section = (spec.get("sections") or [{"kind": "door", "door": 1}])[0]
    panels = carcass(c.W, c.D, c.H, c.T, c.T_back, c.Hleg, c.mat, c.mat_back,
                     leg_as_panel=c.leg_as_panel, leg_type=c.leg_type,
                     socle_recess=spec.get("socle_recess", 50),
                     sides_over_top=spec.get("sides_over_top", False))

    # полки: явные уровни имеют приоритет; счётчик shelves — раскладка равномерно
    levels = section.get("shelf_levels")
    if levels is None and section.get("shelves"):
        levels = shelf_levels(c.Hleg + c.T, c.H - c.T, section["shelves"], c.T)
    if levels:
        panels += shelves(levels, c.W, c.D, c.T, c.T_back, c.mat, "main")

    ndoor = section.get("door", 1)
    g = c.gap
    y1, y2 = c.Hleg + c.T + g, c.H - c.T - g
    doors_meta: list[dict[str, Any]] = []
    if ndoor == 2:
        mid = c.W / 2
        panels.append(overlay_door(c.W, c.H, c.T, c.Hleg, g, c.mat, "main", "Фасад левый",
                                   x1=g, x2=mid - g / 2, y1=y1, y2=y2))
        panels.append(overlay_door(c.W, c.H, c.T, c.Hleg, g, c.mat, "main", "Фасад правый",
                                   x1=mid + g / 2, x2=c.W - g, y1=y1, y2=y2))
        doors_meta = [{"id": "door_1", "type": "распашная", "hinges": "накладные", "lock": False,
                       "dimensions": {"width": round(mid - g / 2 - g, 2), "height": y2 - y1},
                       "position": {"x": g, "y": y1, "z": 0}, "estimated": False},
                      {"id": "door_2", "type": "распашная", "hinges": "накладные", "lock": False,
                       "dimensions": {"width": round(c.W - g - (mid + g / 2), 2), "height": y2 - y1},
                       "position": {"x": mid + g / 2, "y": y1, "z": 0}, "estimated": False}]
    elif ndoor == 1:
        dpanel = overlay_door(c.W, c.H, c.T, c.Hleg, g, c.mat, "main", y1=y1, y2=y2)
        if section.get("door_swing"):                  # направление открывания (AKD-224)
            dpanel["swing"] = str(section["door_swing"]).lower()
        panels.append(dpanel)
        doors_meta = [{"id": "door_1", "type": "распашная", "hinges": "накладные", "lock": False,
                       "dimensions": {"width": c.W - 2 * g, "height": y2 - y1},
                       "position": {"x": g, "y": y1, "z": 0}, "estimated": False}]

    rods_meta = []
    rod = rod_in_column(c.T, c.W - c.T, section, c.H - c.T, 0, c.D - c.T_back, "main")
    if rod:
        rods_meta.append(rod)
        # зона подвеса (~900 вниз) должна быть свободной (AKD-186)
        hang_lo = rod["y1"] - 900
        busy = [p["name"] for p in panels if p.get("type") == "shelf"
                and p["placement"]["y2"] > hang_lo + 1
                and p["placement"]["y1"] < rod["y1"] - 1]
        if busy:
            spec.setdefault("warnings", []).append(
                f"Штанга: зона подвеса занята ({', '.join(busy[:3])}) — "
                "одежде на плечиках нужно ~900 мм свободной высоты")

    sec = [cavity_section(c, [p["name"] for p in panels] + (["Штанга"] if rods_meta else []),
                          stype="door")]
    cc = carcass_calc(c)
    return build_project(spec, panels, sections=sec, doors=doors_meta,
                         carcass_calc=cc, rods=rods_meta)
