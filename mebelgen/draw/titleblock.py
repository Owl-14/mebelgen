"""ME-RA title block (штамп) drawn in the bottom-right corner of the sheet."""
from __future__ import annotations

from .. import config


def draw_title_block(svg, x, y, w, h, item_title, date=None, manager=None):
    date = date or config.DEFAULT_DATE
    manager = manager or config.DEFAULT_MANAGER

    logo_h = h * 0.40
    name_h = h * 0.22
    row_h = (h - logo_h - name_h) / 2.0

    # outer border
    svg.rect(x, y, w, h, fill=config.COL_PAPER, stroke=config.COL_FRAME, width=1.4)

    # --- logo cell ---
    cx = x + w / 2
    svg.text(cx, y + logo_h * 0.55, config.BRAND_NAME, size=config.FS_BRAND,
             fill=config.COL_INK, anchor="middle", weight="bold",
             family=config.FONT_SERIF, spacing="2")
    svg.text(cx, y + logo_h * 0.82, config.BRAND_SLOGAN, size=config.FS_SLOGAN,
             fill=config.COL_INK_SOFT, anchor="middle", style="italic",
             family=config.FONT_SERIF)
    svg.line(x, y + logo_h, x + w, y + logo_h, stroke=config.COL_FRAME, width=1.0)

    # --- name cell ---
    ny = y + logo_h
    svg.text(cx, ny + name_h * 0.62, item_title, size=12.5, fill=config.COL_INK,
             anchor="middle", weight="bold", family=config.FONT_SANS)
    svg.line(x, ny + name_h, x + w, ny + name_h, stroke=config.COL_FRAME, width=1.0)

    # --- two data rows ---
    label_w = w * 0.42
    r1 = ny + name_h
    r2 = r1 + row_h
    for ry, label, value in [(r1, "Дата:", date), (r2, "Руководитель:", manager)]:
        svg.line(x, ry, x + w, ry, stroke=config.COL_FRAME, width=0.8)
        svg.line(x + label_w, ry, x + label_w, ry + row_h,
                 stroke=config.COL_FRAME, width=0.8)
        svg.text(x + 8, ry + row_h * 0.62, label, size=11, fill=config.COL_INK,
                 anchor="start", family=config.FONT_SANS)
        svg.text(x + label_w + 8, ry + row_h * 0.62, value, size=11,
                 fill=config.COL_INK, anchor="start", family=config.FONT_SANS)
