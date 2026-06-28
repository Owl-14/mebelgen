"""Reusable drawing primitives for furniture in isometric and 2D views."""
from __future__ import annotations

from typing import Optional

from .. import config
from .iso import IsoView


def iso_box(svg, iso: IsoView, x, y, z, w, d, h,
            top=config.COL_MAT_TOP, front=config.COL_MAT_FRONT,
            side=config.COL_MAT_SIDE, line=config.COL_MAT_LINE, lw=1.0,
            faces=("top", "front", "side")):
    """Draw a shaded isometric box with origin (x,y,z) and size (w,d,h)."""
    P = iso.p
    # 8 corners
    c000 = P(x, y, z);       c100 = P(x + w, y, z)
    c010 = P(x, y + d, z);   c110 = P(x + w, y + d, z)
    c001 = P(x, y, z + h);   c101 = P(x + w, y, z + h)
    c011 = P(x, y + d, z + h); c111 = P(x + w, y + d, z + h)

    # Visible faces for this isometric (camera above, looking at the +x,+y
    # corner). They meet at the near-top corner c111 and tile cleanly:
    #   side  = x=W plane, front = y=D plane, top = z=h plane.
    if "side" in faces:  # right face x=W
        svg.polygon([c100, c110, c111, c101], fill=side, stroke=line, width=lw)
    if "front" in faces:  # front face y=D
        svg.polygon([c010, c110, c111, c011], fill=front, stroke=line, width=lw)
    if "top" in faces:  # top face z=h
        svg.polygon([c001, c101, c111, c011], fill=top, stroke=line, width=lw)
    return {
        "front": [c010, c110, c111, c011],
        "side": [c100, c110, c111, c101],
        "top": [c001, c101, c111, c011],
    }


def iso_brass_frame(svg, iso: IsoView, x, y, z, w, h, inset=0.0,
                    color=config.COL_BRASS, lw=2.2):
    """Draw a brass rectangular frame on the front face (y=0 plane)."""
    P = iso.p
    pts = [P(x + inset, y, z + inset), P(x + w - inset, y, z + inset),
           P(x + w - inset, y, z + h - inset), P(x + inset, y, z + h - inset)]
    svg.polygon(pts, fill="none", stroke=color, width=lw)


def iso_cylinder(svg, iso: IsoView, cx, cy, z, radius, h,
                 top=config.COL_MAT_TOP, side=config.COL_MAT_FRONT,
                 line=config.COL_MAT_LINE, lw=1.0, fluted=False, ry_ratio=None):
    """Draw an isometric cylinder centred at (cx, cy), base z, given radius/height."""
    P = iso.p
    # ellipse radii in screen space
    rx = radius * config.ISO_COS * iso.scale
    ry = radius * config.ISO_SIN * iso.scale
    bx, by = P(cx, cy, z)            # base centre
    tx, ty = P(cx, cy, z + h)        # top centre
    # body
    body = (
        f'M {bx - rx:.2f} {by:.2f} '
        f'A {rx:.2f} {ry:.2f} 0 0 0 {bx + rx:.2f} {by:.2f} '
        f'L {tx + rx:.2f} {ty:.2f} '
        f'A {rx:.2f} {ry:.2f} 0 0 1 {tx - rx:.2f} {ty:.2f} Z'
    )
    svg.path(body, fill=side, stroke=line, width=lw)
    # vertical flutes
    if fluted:
        n = max(10, int(radius / 18))
        import math
        for i in range(1, n):
            ang = math.pi * i / n  # 0..pi across the front half
            fx = cx + radius * math.cos(ang)
            # depth offset to sit on the cylinder surface (front half)
            fy = cy - radius * math.sin(ang) * 0.0
            sx0, sy0 = P(fx, cy - radius * math.sin(ang), z)
            sx1, sy1 = P(fx, cy - radius * math.sin(ang), z + h)
            svg.line(sx0, sy0, sx1, sy1, stroke=config.COL_MAT_LINE_SOFT, width=0.7)
    # top ellipse
    svg.ellipse(tx, ty, rx, ry, fill=top, stroke=line, width=lw)
    return {"top_center": (tx, ty), "rx": rx, "ry": ry, "base_center": (bx, by)}
