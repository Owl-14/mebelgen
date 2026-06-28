"""'Материалы и описание' bulleted block, bottom-left of the sheet."""
from __future__ import annotations

from .. import config
from .callouts import _wrap


def draw_materials(svg, x, y, w, lines, max_h=None, title="МАТЕРИАЛЫ И ОПИСАНИЕ:"):
    svg.text(x, y, title, size=12, fill=config.COL_INK, weight="bold",
             family=config.FONT_SANS, spacing="0.5")
    cy = y + 19
    lh = 14
    limit_y = (y + max_h) if max_h else None
    char_w = config.FS_BODY * 0.52
    wrap_n = max(16, int((w - 14) / char_w))

    for ln in lines:
        wrapped = _wrap(ln, wrap_n)
        need = len(wrapped) * lh + 3
        if limit_y is not None and cy + need > limit_y:
            break
        svg.circle(x + 3, cy - 3.5, 1.8, fill=config.COL_INK, stroke="none")
        for part in wrapped:
            svg.text(x + 12, cy, part, size=config.FS_BODY, fill=config.COL_INK_SOFT,
                     family=config.FONT_SANS)
            cy += lh
        cy += 3
    return cy
