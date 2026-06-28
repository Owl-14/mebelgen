"""Structured representation of a single furniture item from the spec."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Dimensions:
    """Overall size in millimetres (width x depth x height)."""
    w: Optional[float] = None   # ширина (X)
    d: Optional[float] = None   # глубина (Y)
    h: Optional[float] = None   # высота (Z)
    diameter: Optional[float] = None  # for round items (Ø)

    @property
    def is_round(self) -> bool:
        return self.diameter is not None

    def label(self) -> str:
        if self.is_round and self.h:
            return f"Ø{_n(self.diameter)}x{_n(self.h)}"
        parts = [self.w, self.d, self.h]
        if all(p is not None for p in parts):
            return f"{_n(self.w)}x{_n(self.d)}x{_n(self.h)}"
        return ""


@dataclass
class Material:
    """Material / finish description."""
    body: str = "МДФ"          # основной материал
    color_system: str = ""     # RAL / NCS
    color_code: str = ""       # e.g. 8019 / 3000
    matte: bool = True
    raw: str = ""


@dataclass
class FurnitureSpec:
    """A fully parsed furniture item."""
    index: str = ""                 # № п/п
    name: str = ""                  # Наименование товара
    qty: str = ""                   # количество
    unit: str = "шт."
    raw_characteristics: str = ""   # original text block

    dims: Dimensions = field(default_factory=Dimensions)
    material: Material = field(default_factory=Material)

    # Parsed feature flags / values
    features: dict = field(default_factory=dict)
    # Material/feature lines for the "Материалы и описание" block
    material_lines: list = field(default_factory=list)
    # Callout annotations: list of dicts {text, target} attached later by archetype
    annotations: list = field(default_factory=list)

    # Classification result
    archetype: str = ""

    # Reference image(s) from the spec table
    image_path: Optional[str] = None
    image_paths: list = field(default_factory=list)

    # Photoreal 3D render produced by Blender (PNG path)
    hero_png: Optional[str] = None

    def has(self, key: str) -> bool:
        return bool(self.features.get(key))


def _n(v) -> str:
    if v is None:
        return ""
    if abs(v - round(v)) < 1e-6:
        return str(int(round(v)))
    return f"{v:g}"
