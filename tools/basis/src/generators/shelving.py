"""Генератор shelving: короб + полки (одна секция на всю ширину)."""

from __future__ import annotations

from typing import Any

from .base import read_carcass
from .corpus import carcass_calc, cavity_section
from .helpers import build_project, carcass, shelf_levels, shelves


def generate(spec: dict[str, Any]) -> dict[str, Any]:
    c = read_carcass(spec)
    panels = carcass(c.W, c.D, c.H, c.T, c.T_back, c.Hleg, c.mat, c.mat_back,
                     leg_as_panel=c.leg_as_panel, leg_type=c.leg_type,
                     socle_recess=spec.get("socle_recess", 50))
    section = (spec.get("sections") or [{"kind": "shelves", "shelves": 0}])[0]
    n = section.get("shelves", 0)
    levels = section.get("shelf_levels") or shelf_levels(c.Hleg + c.T, c.H - c.T, n, c.T)
    panels += shelves(levels, c.W, c.D, c.T, c.T_back, c.mat, "main")
    sec = [cavity_section(c, [p["name"] for p in panels])]
    cc = carcass_calc(c)
    cc["shelf_levels"] = levels
    return build_project(spec, panels, sections=sec, carcass_calc=cc)
