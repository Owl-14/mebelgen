"""Map an archetype name to its drawing class."""
from __future__ import annotations

from ..model.classifier import classify
from ..model.spec import FurnitureSpec


def get_archetype(spec: FurnitureSpec):
    name = spec.archetype or classify(spec)
    spec.archetype = name

    from .casegood import CaseGood
    from .desk import DeskPanel, DrawerUnit
    from .tables import CoffeeRound, CoffeeFluted, CoffeeRect

    mapping = {
        "wardrobe": CaseGood,
        "cabinet": CaseGood,
        "desk_panel": DeskPanel,
        "drawer_unit": DrawerUnit,
        "coffee_round": CoffeeRound,
        "coffee_fluted": CoffeeFluted,
        "coffee_rect": CoffeeRect,
    }
    cls = mapping.get(name, CaseGood)
    return cls(spec)
