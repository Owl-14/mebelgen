"""Dimension lines: extension lines, arrowheads and dimension text.

All coordinates are in *screen* (sheet) pixels. Helpers cover the horizontal
and vertical linear dimensions used on the reference sheets.
"""
from __future__ import annotations

import math

from .. import config


def _arrow(svg, x, y, dx, dy, size=config.ARROW, color=config.COL_DIM):
    """Filled triangular arrowhead at (x,y) pointing along (dx,dy)."""
    ln = math.hypot(dx, dy) or 1.0
    ux, uy = dx / ln, dy / ln
    px, py = -uy, ux
    bx, by = x - ux * size, y - uy * size
    p1 = (bx + px * size * 0.32, by + py * size * 0.32)
    p2 = (bx - px * size * 0.32, by - py * size * 0.32)
    svg.polygon([(x, y), p1, p2], fill=color, stroke=color, width=0.5)


def dim_h(svg, x1, x2, y_obj, y_dim, text, color=config.COL_DIM,
          size=config.FS_DIM, ext=True, flip_text=False):
    """Horizontal dimension between x1 and x2; dim line at y_dim."""
    if x1 > x2:
        x1, x2 = x2, x1
    if ext:
        gap = config.EXT_GAP if y_dim < y_obj else -config.EXT_GAP
        over = config.EXT_OVER if y_dim < y_obj else -config.EXT_OVER
        svg.line(x1, y_obj - gap, x1, y_dim - over, stroke=color, width=0.7)
        svg.line(x2, y_obj - gap, x2, y_dim - over, stroke=color, width=0.7)
    svg.line(x1, y_dim, x2, y_dim, stroke=color, width=0.8)
    _arrow(svg, x1, y_dim, -1, 0, color=color)
    _arrow(svg, x2, y_dim, 1, 0, color=color)
    cx = (x1 + x2) / 2
    ty = y_dim - 4 if not flip_text else y_dim + size + 1
    svg.text(cx, ty, text, size=size, fill=color, anchor="middle",
             family=config.FONT_SANS)


def dim_v(svg, y1, y2, x_obj, x_dim, text, color=config.COL_DIM,
          size=config.FS_DIM, ext=True, text_side="left"):
    """Vertical dimension between y1 and y2; dim line at x_dim."""
    if y1 > y2:
        y1, y2 = y2, y1
    if ext:
        gap = config.EXT_GAP if x_dim < x_obj else -config.EXT_GAP
        over = config.EXT_OVER if x_dim < x_obj else -config.EXT_OVER
        svg.line(x_obj - gap, y1, x_dim - over, y1, stroke=color, width=0.7)
        svg.line(x_obj - gap, y2, x_dim - over, y2, stroke=color, width=0.7)
    svg.line(x_dim, y1, x_dim, y2, stroke=color, width=0.8)
    _arrow(svg, x_dim, y1, 0, -1, color=color)
    _arrow(svg, x_dim, y2, 0, 1, color=color)
    cy = (y1 + y2) / 2
    if text_side == "left":
        svg.text(x_dim - 4, cy + size * 0.35, text, size=size, fill=color,
                 anchor="end", family=config.FONT_SANS)
    else:
        svg.text(x_dim + 4, cy + size * 0.35, text, size=size, fill=color,
                 anchor="start", family=config.FONT_SANS)


def diameter(svg, cx, cy, r_px, text, color=config.COL_DIM, size=config.FS_DIM):
    """Diameter dimension across a circle/ellipse (horizontal)."""
    svg.line(cx - r_px, cy, cx + r_px, cy, stroke=color, width=0.8)
    _arrow(svg, cx - r_px, cy, -1, 0, color=color)
    _arrow(svg, cx + r_px, cy, 1, 0, color=color)
    svg.text(cx, cy - 4, text, size=size, fill=color, anchor="middle",
             family=config.FONT_SANS)
