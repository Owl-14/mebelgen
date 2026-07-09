"""Общие извлечения параметров корпуса из ParamSpec."""

from __future__ import annotations

from typing import Any, NamedTuple


class Carcass(NamedTuple):
    W: float
    D: float
    H: float
    T: float
    T_back: float
    Hleg: float
    mat: str
    mat_back: str
    gap: float
    leg_as_panel: bool
    leg_type: str
    T_top: float = 0.0        # толщина крышки/столешницы (AKD-218; 0 → = T)


def read_carcass(spec: dict[str, Any]) -> Carcass:
    dim = spec["dimensions"]
    m = spec["materials"]
    legs = spec.get("legs") or {}
    gaps = spec.get("gaps") or {}
    T = m["board_thickness"]
    mat = m.get("board_material", "ЛДСП")
    return Carcass(
        W=dim["width"], D=dim.get("depth_carcass", dim["depth"]), H=dim["height"],
        T=T, T_back=m.get("back_thickness", T), Hleg=legs.get("height", 0) or 0,
        mat=mat, mat_back=m.get("back_material", mat), gap=gaps.get("facade", gaps.get("default", 2)),
        leg_as_panel=legs.get("as_panel", False), leg_type=legs.get("type", ""),
        T_top=float(m.get("top_thickness") or T),
    )
