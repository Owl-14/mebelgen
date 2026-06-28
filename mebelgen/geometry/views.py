"""2D orthographic view: maps millimetre coordinates into a sheet rectangle.

A View2D fits a content bounding box (in mm) into a target rectangle (in px)
with uniform scaling, preserving aspect ratio. The mm Y axis points *up*
(natural for elevations); screen Y points down, so it is flipped.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class View2D:
    # target rectangle on the sheet (px)
    rx: float
    ry: float
    rw: float
    rh: float
    # content extents (mm)
    content_w: float
    content_h: float
    pad: float = 26.0
    align: str = "center"  # center | bottom

    def __post_init__(self):
        avail_w = max(1.0, self.rw - 2 * self.pad)
        avail_h = max(1.0, self.rh - 2 * self.pad)
        sx = avail_w / self.content_w if self.content_w else 1.0
        sy = avail_h / self.content_h if self.content_h else 1.0
        self.scale = min(sx, sy)
        draw_w = self.content_w * self.scale
        draw_h = self.content_h * self.scale
        self.ox = self.rx + (self.rw - draw_w) / 2.0
        if self.align == "bottom":
            self.oy_bottom = self.ry + self.rh - self.pad
        else:
            self.oy_bottom = self.ry + (self.rh + draw_h) / 2.0

    def px(self, xmm: float, ymm: float):
        """Map mm (origin at content bottom-left, y up) to sheet px."""
        return (self.ox + xmm * self.scale, self.oy_bottom - ymm * self.scale)

    def s(self, mm: float) -> float:
        return mm * self.scale
