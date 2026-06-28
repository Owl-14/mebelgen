"""Resolve a furniture material to an sRGB colour and derive a 2D palette.

Kept in sync (by value) with the standalone Blender table in
``render/blender/build_scene.py`` so 2D drawings and 3D renders agree.
"""
from __future__ import annotations

from dataclasses import dataclass

RAL = {
    "8019": "#3D3635", "9011": "#1C1C1C", "9005": "#0A0A0A",
    "7016": "#383E42", "1015": "#E6D2B5",
}
NCS = {
    "2000-N": "#D4D4D2", "3000-N": "#B7B7B5", "4000-N": "#9C9C9A",
    "2000": "#D4D4D2", "3000": "#B7B7B5", "1000-N": "#E9E9E7",
}
CREAM = "#E9E2D4"   # warm MDF when colour is "по согласованию"
BRASS = "#C8A13C"
DARK = "#1A1815"


def resolve_hex(material) -> str:
    """material: object/dict with color_system, color_code, body."""
    def g(k):
        if isinstance(material, dict):
            return material.get(k) or ""
        return getattr(material, k, "") or ""

    sysname = g("color_system").upper()
    code = g("color_code").upper()
    if sysname == "RAL" and code in RAL:
        return RAL[code]
    if sysname == "NCS":
        return NCS.get(code, NCS.get(code.replace("S", "").strip("- "), CREAM))
    return CREAM


def _hex(c):
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _mix(a, b, t):
    ar, ag, ab = _hex(a)
    br, bg, bb = _hex(b)
    r = round(ar + (br - ar) * t)
    g = round(ag + (bg - ag) * t)
    bl = round(ab + (bb - ab) * t)
    return f"#{r:02X}{g:02X}{bl:02X}"


def tint(c, t):   # toward white
    return _mix(c, "#FFFFFF", t)


def shade(c, t):  # toward black
    return _mix(c, "#000000", t)


@dataclass
class Palette:
    base: str
    front: str
    side: str
    top: str
    line: str
    line_soft: str


def palette_for(material) -> Palette:
    base = resolve_hex(material)
    return Palette(
        base=base,
        front=tint(base, 0.50),
        side=tint(base, 0.36),
        top=tint(base, 0.64),
        line=shade(base, 0.55),
        line_soft=tint(shade(base, 0.55), 0.55),
    )
