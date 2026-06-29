"""Compose a full A4 reference sheet for one furniture item."""
from __future__ import annotations

from .. import config
from ..archetypes.base import Area
from ..archetypes.registry import get_archetype
from ..model.spec import FurnitureSpec
from .materials import draw_materials
from .svg import SVG
from .titleblock import draw_title_block

TB_W = 300
TB_H = 150


def build_sheet(spec: FurnitureSpec, date=None, manager=None) -> SVG:
    svg = SVG(config.SHEET_W, config.SHEET_H, background=config.COL_PAPER)
    m = config.SHEET_MARGIN

    # outer frame
    svg.rect(m, m, config.SHEET_W - 2 * m, config.SHEET_H - 2 * m,
             fill="none", stroke=config.COL_FRAME, width=1.6)

    # bottom band: materials (left) + title block (right)
    tb_x = config.SHEET_W - m - TB_W - 6
    tb_y = config.SHEET_H - m - TB_H - 6
    band_top = tb_y - 16

    # drawing area (everything above the bottom band)
    area = Area(m + 8, m + 12, config.SHEET_W - 2 * m - 16, band_top - (m + 12))

    arch = get_archetype(spec)
    arch.draw(svg, area)

    # materials block (kept inside the bottom band, left of the title block)
    mat_x = m + 14
    mat_w = tb_x - mat_x - 24
    title = _title(spec)
    mat_h = (config.SHEET_H - m) - (tb_y + 6)
    draw_materials(svg, mat_x, tb_y + 14, mat_w, spec.material_lines, max_h=mat_h)

    # title block
    draw_title_block(svg, tb_x, tb_y, TB_W, TB_H, title, date=date, manager=manager)
    return svg


def _title(spec: FurnitureSpec) -> str:
    label = spec.dims.label()
    name = _short_name(spec.name)
    return f"{name} {label}".strip()


def _short_name(name: str) -> str:
    n = name.strip()
    # title-case first letter, keep rest
    if n:
        n = n[0].upper() + n[1:]
    return n
