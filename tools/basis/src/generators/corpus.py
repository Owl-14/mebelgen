"""Генератор corpus: пустой короб (дно/крышка/боковины/задник/опоры)."""

from __future__ import annotations

from typing import Any

from .base import read_carcass
from .helpers import build_project, carcass


def cavity_section(c, names: list[str], sid: str = "main", stype: str = "open") -> dict[str, Any]:
    return {
        "id": sid, "type": stype,
        "dimensions": {"width": c.W - 2 * c.T, "height": (c.H - c.T) - (c.Hleg + c.T),
                       "depth": c.D - c.T - c.T_back, "estimated": False},
        "elements": names,
    }


def carcass_calc(c) -> dict[str, Any]:
    return {"W": c.W, "D": c.D, "H": c.H, "T": c.T, "T_back": c.T_back, "Hleg": c.Hleg,
            "construction": "top_bottom_over_sides",
            "inner_width": f"W - 2*T = {c.W - 2 * c.T}",
            "side_y": f"{c.Hleg + c.T}..{c.H - c.T}"}


def generate(spec: dict[str, Any]) -> dict[str, Any]:
    c = read_carcass(spec)
    panels = carcass(c.W, c.D, c.H, c.T, c.T_back, c.Hleg, c.mat, c.mat_back,
                     leg_as_panel=c.leg_as_panel, leg_type=c.leg_type,
                     socle_recess=spec.get("socle_recess", 50),
                     sides_over_top=spec.get("sides_over_top", False))
    sec = [cavity_section(c, [p["name"] for p in panels])]
    return build_project(spec, panels, sections=sec, carcass_calc=carcass_calc(c))
