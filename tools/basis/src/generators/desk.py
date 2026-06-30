"""Генератор desk/table: столешница на боковых опорах + задняя царга."""

from __future__ import annotations

from typing import Any

from .base import read_carcass
from .helpers import build_project, panel


def generate(spec: dict[str, Any]) -> dict[str, Any]:
    c = read_carcass(spec)
    top_z = spec.get("top_overhang")
    tz = tuple(top_z) if top_z else (0, c.D)
    apron_h = spec.get("apron_height", 120)

    panels = [
        panel("Столешница", "top", "horizont", (0, c.W), (c.H - c.T, c.H), tz, thickness=c.T, material=c.mat),
        panel("Опора левая", "side_left", "vertical", (0, c.T), (c.Hleg, c.H - c.T), (0, c.D), thickness=c.T, material=c.mat),
        panel("Опора правая", "side_right", "vertical", (c.W - c.T, c.W), (c.Hleg, c.H - c.T), (0, c.D), thickness=c.T, material=c.mat),
    ]
    if spec.get("apron", True):
        panels.append(panel("Царга задняя", "back", "front", (c.T, c.W - c.T),
                            (c.H - c.T - apron_h, c.H - c.T), (c.D - c.T, c.D),
                            thickness=c.T, material=c.mat))
    sec = [{"id": "main", "type": "desk",
            "dimensions": {"width": c.W - 2 * c.T, "height": c.H - c.T, "depth": c.D, "estimated": False},
            "elements": [p["name"] for p in panels]}]
    return build_project(spec, panels, sections=sec,
                         carcass_calc={"W": c.W, "D": c.D, "H": c.H, "T": c.T, "construction": "top_on_supports"})
