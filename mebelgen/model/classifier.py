"""Classify a FurnitureSpec into a drawing archetype.

Archetypes (see ``mebelgen/archetypes``):
    wardrobe         - tall 2-door wardrobe (rod/shelves)
    cabinet          - low/medium 2-door cabinet with a shelf
    desk_panel       - work desk on side panels (+ screen, brass legs)
    drawer_unit      - desk/pedestal with drawers + open knee space
    coffee_round     - round table on a cylindrical pedestal
    coffee_fluted    - fluted cylinder table
    coffee_rect      - rectangular / cube coffee table
"""
from __future__ import annotations

from .spec import FurnitureSpec


def classify(spec: FurnitureSpec) -> str:
    name = spec.name.lower()
    f = spec.features
    d = spec.dims

    # --- Round / cylindrical tables --------------------------------------
    if d.is_round:
        if f.get("fluted"):
            return "coffee_fluted"
        return "coffee_round"

    # --- Desks -----------------------------------------------------------
    if "стол" in name and ("рабоч" in name or f.get("screen_front") or f.get("pc_holder")):
        return "desk_panel"

    # --- Coffee / journal tables (low, no doors) -------------------------
    if ("журнальн" in name or "кофейн" in name) and not f.get("drawers"):
        if f.get("fluted"):
            return "coffee_fluted"
        return "coffee_rect"

    # --- Drawer pedestal / document tumba with drawers -------------------
    if f.get("drawers"):
        return "drawer_unit"

    # --- Wardrobes / cabinets with doors ---------------------------------
    h = d.h or 0
    is_wardrobe_name = "шкаф" in name
    tall = h >= 1500
    if is_wardrobe_name or tall:
        # combined wardrobe with rod -> wardrobe; otherwise shelves cabinet
        return "wardrobe"

    # medium / low cabinets ("тумба", "шкаф" low)
    if "тумба" in name or "шкаф" in name or f.get("shelves") or f.get("overlay_doors"):
        return "cabinet"

    # --- Fallbacks -------------------------------------------------------
    if "стол" in name:
        return "coffee_rect"
    return "cabinet"


def classify_all(specs):
    for s in specs:
        s.archetype = classify(s)
    return specs
