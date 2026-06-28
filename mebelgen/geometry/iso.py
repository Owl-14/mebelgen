"""Isometric projection (30 degrees) for the 3D view.

World axes (mm): x = width (right), y = depth (back), z = height (up).
Screen mapping:
    sx = ox + (x - y) * cos30 * scale
    sy = oy + (x + y) * sin30 * scale - z * scale
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

from .. import config


@dataclass
class IsoView:
    ox: float          # screen origin x (projection of world 0,0,0)
    oy: float          # screen origin y
    scale: float = 0.1

    def p(self, x: float, y: float, z: float) -> Tuple[float, float]:
        sx = self.ox + (x - y) * config.ISO_COS * self.scale
        sy = self.oy + (x + y) * config.ISO_SIN * self.scale - z * self.scale
        return (sx, sy)

    # bounding helpers --------------------------------------------------
    @staticmethod
    def fit(w: float, d: float, h: float, rect, pad: float = 40.0) -> "IsoView":
        """Build an IsoView fitting a w*d*h box into a target rect (rx,ry,rw,rh)."""
        rx, ry, rw, rh = rect
        # projected extents (unit scale) of the 8 corners
        view = IsoView(0, 0, 1.0)
        xs, ys = [], []
        for cx in (0, w):
            for cy in (0, d):
                for cz in (0, h):
                    px, py = view.p(cx, cy, cz)
                    xs.append(px)
                    ys.append(py)
        span_x = max(xs) - min(xs)
        span_y = max(ys) - min(ys)
        avail_w = rw - 2 * pad
        avail_h = rh - 2 * pad
        scale = min(avail_w / span_x if span_x else 1, avail_h / span_y if span_y else 1)
        # recompute origin so the projected box is centred in rect
        view.scale = scale
        xs, ys = [], []
        for cx in (0, w):
            for cy in (0, d):
                for cz in (0, h):
                    px, py = view.p(cx, cy, cz)
                    xs.append(px)
                    ys.append(py)
        minx, maxx = min(xs), max(xs)
        miny, maxy = min(ys), max(ys)
        view.ox = rx + (rw - (maxx - minx)) / 2 - minx
        view.oy = ry + (rh - (maxy - miny)) / 2 - miny
        return view
