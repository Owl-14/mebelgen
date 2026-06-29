"""Base archetype: shared helpers for drawing a furniture reference."""
from __future__ import annotations

import base64
import os
import struct
from dataclasses import dataclass

from .. import config
from ..model.colors import palette_for
from ..model.spec import FurnitureSpec


def _png_size(path):
    """Return (w, h) of a PNG by reading its IHDR header."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(24)
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            w, h = struct.unpack(">II", head[16:24])
            return w, h
    except Exception:
        pass
    return None


@dataclass
class Area:
    x: float
    y: float
    w: float
    h: float

    def sub(self, fx, fy, fw, fh):
        return Area(self.x + self.w * fx, self.y + self.h * fy,
                    self.w * fw, self.h * fh)


class Archetype:
    """Override draw(); use helpers for labels."""

    def __init__(self, spec: FurnitureSpec):
        self.spec = spec
        self.hero_png = getattr(spec, "hero_png", None)
        # 2D fills/outlines derived from the item's real colour (RAL/NCS/cream),
        # lightened so dimension lines stay readable.
        self.pal = palette_for(spec.material)

    # -- 3D hero render ---------------------------------------------------
    def draw_hero(self, svg, area: "Area", label="3D ВИЗУАЛИЗАЦИЯ") -> bool:
        """If a Blender render exists, embed it (with a soft shadow) into the
        area and return True so the caller skips the vector ISO."""
        png = self.hero_png
        if not png or not os.path.exists(png):
            return False
        size = _png_size(png)
        pad = 8
        ax, ay = area.x + pad, area.y + 18
        aw, ah = area.w - 2 * pad, area.h - 26
        if size:
            iw, ih = size
            sc = min(aw / iw, ah / ih)
            dw, dh = iw * sc, ih * sc
            preserve = "xMidYMid meet"
        else:
            dw = dh = min(aw, ah)
            preserve = "xMidYMid meet"
        dx = ax + (aw - dw) / 2
        dy = ay + (ah - dh) / 2
        try:
            with open(png, "rb") as fh:
                b64 = base64.b64encode(fh.read()).decode("ascii")
            href = "data:image/png;base64," + b64
        except Exception:
            return False
        if label:
            self.view_label(svg, area.x + area.w / 2, area.y + 6, label)
        svg.image(dx, dy, dw, dh, href, preserve=preserve)
        return True

    # -- helpers ----------------------------------------------------------
    def view_label(self, svg, cx, y, text):
        svg.text(cx, y, text, size=config.FS_TITLE, fill=config.COL_INK,
                 anchor="middle", weight="bold", family=config.FONT_SANS,
                 spacing="0.5")

    def color_text(self):
        m = self.spec.material
        if m.color_system:
            return f"{m.body} {m.color_system} {m.color_code}".strip()
        return m.body

    # -- entry point ------------------------------------------------------
    def draw(self, svg, area: Area):  # pragma: no cover - abstract
        raise NotImplementedError
