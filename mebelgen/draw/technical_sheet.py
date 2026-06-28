"""Presentation-grade technical sheets.

This module is an experimental renderer for the "ME-RA sample" look: separate
orthographic views, a softer technical isometry, measured callouts, and a
classic title block.  It intentionally keeps the data path from the existing
parser/classifier, but bypasses the older generic archetype layout for views
that need more hand-tuned presentation.
"""
from __future__ import annotations

import math

from .. import config
from ..model.spec import FurnitureSpec
from . import dimensions as dim
from .sheet import build_sheet
from .svg import SVG
from .titleblock import draw_title_block


TB_W = 330
TB_H = 150


def build_technical_sheet(spec: FurnitureSpec, date=None, manager=None) -> SVG:
    """Build a presentation technical sheet.

    The prototype currently has a tuned desk renderer. Other archetypes fall
    back to the classic renderer until they get their own view-specific layout.
    """
    if spec.archetype == "desk_panel":
        return _desk_sheet(spec, date=date, manager=manager)
    return build_sheet(spec, date=date, manager=manager)


def _desk_sheet(spec: FurnitureSpec, date=None, manager=None) -> SVG:
    svg = SVG(config.SHEET_W, config.SHEET_H, background=config.COL_PAPER)
    _defs(svg)

    m = 14
    svg.rect(m, m, config.SHEET_W - 2 * m, config.SHEET_H - 2 * m,
             fill="none", stroke="#111111", width=1.8)
    svg.rect(m + 8, m + 8, config.SHEET_W - 2 * (m + 8),
             config.SHEET_H - 2 * (m + 8), fill="none", stroke="#222222", width=0.7)

    _view_label(svg, config.SHEET_W / 2, 74, "ВИД СПЕРЕДИ")
    _draw_desk_front(svg, spec, 50, 98, 695, 335)

    _view_label(svg, config.SHEET_W / 2, 482, "3D ИЗОМЕТРИЯ")
    _draw_desk_iso(svg, spec, 62, 510, 670, 330)

    _draw_desk_materials(svg, spec, 58, 845, 405)

    tb_x = config.SHEET_W - m - TB_W - 10
    tb_y = config.SHEET_H - m - TB_H - 12
    draw_title_block(svg, tb_x, tb_y, TB_W, TB_H, _title(spec),
                     date=date, manager=manager)
    return svg


def _defs(svg: SVG) -> None:
    svg.add_def("""
<linearGradient id="techTop" x1="0" y1="0" x2="0" y2="1">
  <stop offset="0%" stop-color="#fbfaf6"/>
  <stop offset="100%" stop-color="#e7e0d2"/>
</linearGradient>
<linearGradient id="techFront" x1="0" y1="0" x2="1" y2="1">
  <stop offset="0%" stop-color="#f5f1e8"/>
  <stop offset="55%" stop-color="#e8e0d2"/>
  <stop offset="100%" stop-color="#d5cdbb"/>
</linearGradient>
<linearGradient id="techSide" x1="0" y1="0" x2="1" y2="0">
  <stop offset="0%" stop-color="#ddd4c1"/>
  <stop offset="100%" stop-color="#f1ece2"/>
</linearGradient>
<linearGradient id="techDark" x1="0" y1="0" x2="1" y2="1">
  <stop offset="0%" stop-color="#2b2b2b"/>
  <stop offset="100%" stop-color="#050505"/>
</linearGradient>
<filter id="softDrop" x="-20%" y="-20%" width="140%" height="160%">
  <feDropShadow dx="0" dy="8" stdDeviation="7" flood-color="#000000" flood-opacity="0.16"/>
</filter>
<filter id="softInner" x="-15%" y="-15%" width="130%" height="130%">
  <feDropShadow dx="0" dy="4" stdDeviation="5" flood-color="#000000" flood-opacity="0.11"/>
</filter>
""")


def _view_label(svg: SVG, x, y, text):
    svg.text(x, y, text, size=17, fill="#222222", anchor="middle",
             weight="normal", family=config.FONT_SANS)


def _draw_desk_front(svg: SVG, spec: FurnitureSpec, x, y, w, h) -> None:
    W = spec.dims.w or 1800
    H = spec.dims.h or 750
    top_th = spec.features.get("thickness", {}).get("worktop", 50)
    screen_h = spec.features.get("thickness", {}).get("screen", 300)
    leg_w = 50
    overhang = 25

    scale = min((w - 86) / (W + 2 * overhang), (h - 70) / H)
    left = x + (w - (W + 2 * overhang) * scale) / 2
    top = y + 92
    bottom = top + H * scale
    obj_w = (W + 2 * overhang) * scale

    def X(mm):
        return left + mm * scale

    def Y(mm):
        return bottom - mm * scale

    top_y = Y(H)
    top_h = top_th * scale
    body_top = Y(H - top_th)
    screen_bottom = Y(H - top_th - screen_h)

    # total width / height dimensions
    dim.dim_h(svg, X(0), X(W + 2 * overhang), top_y, top_y - 30, _mm(W), size=14)
    dim.dim_v(svg, top_y, bottom, X(0), X(0) - 34, _mm(H), size=14)
    dim.dim_v(svg, top_y, body_top, X(W + 2 * overhang), X(W + 2 * overhang) + 26,
              _mm(top_th), text_side="right", size=14)
    dim.dim_v(svg, body_top, screen_bottom, X(0), X(0) - 20, _mm(screen_h), size=13)
    dim.dim_v(svg, top_y - 1, body_top + overhang * scale, X(0), X(0) - 54,
              _mm(overhang), size=12)

    # soft contact shadows
    svg.ellipse(left + obj_w * 0.5, bottom + 10, obj_w * 0.42, 13,
                fill="#000000", stroke="none", opacity=0.07)

    # brass rails just under the top
    rail_y = body_top + 6
    svg.line(X(overhang), rail_y, X(overhang + W), rail_y,
             stroke=config.COL_BRASS_DARK, width=1.2, opacity=0.9)
    svg.line(X(overhang), rail_y + 4, X(overhang + W), rail_y + 4,
             stroke=config.COL_BRASS_LIGHT, width=0.8, opacity=0.9)

    # side panel legs
    for lx in (overhang, overhang + W - leg_w):
        px = X(lx)
        svg.rect(px, body_top, leg_w * scale, bottom - body_top,
                 fill="url(#techSide)", stroke="#3a3936", width=1.0)
        svg.rect(px + leg_w * scale * 0.30, body_top + 6,
                 leg_w * scale * 0.14, bottom - body_top - 14,
                 fill=config.COL_BRASS_LIGHT, stroke=config.COL_BRASS_DARK, width=0.7)
        svg.polygon([(px + 2, bottom), (px + leg_w * scale - 2, bottom),
                     (px + leg_w * scale - 7, bottom + 8), (px + 7, bottom + 8)],
                    fill="#a98531", stroke="#5e4613", width=0.7)

    # front modesty panel / screen
    svg.rect(X(overhang + leg_w), body_top, (W - 2 * leg_w) * scale,
             screen_h * scale, fill="url(#techFront)", stroke="#4d4941",
             width=0.9, opacity=0.96)
    svg.rect(X(overhang + leg_w), body_top, (W - 2 * leg_w) * scale,
             screen_h * scale, fill="#ffffff", stroke="none", opacity=0.16)

    # worktop
    svg.rect(X(0), top_y, obj_w, top_h, fill="url(#techTop)",
             stroke="#242424", width=1.0, rx=2)
    svg.line(X(0), top_y + top_h - 1, X(W + 2 * overhang), top_y + top_h - 1,
             stroke="#8c8678", width=1.0, opacity=0.65)

    # cable tray and flexible channel
    tray_w = 150 * scale
    tray_x = X(overhang + W * 0.52) - tray_w / 2
    tray_y = body_top - 2
    svg.rect(tray_x, tray_y, tray_w, 9, fill="url(#techDark)",
             stroke="#000000", width=0.6)
    _draw_cable_hose(svg, tray_x + tray_w * 0.65, tray_y + 8,
                     X(overhang + W * 0.58), bottom + 3, scale=1.0)

    # system unit holder
    holder_x = X(overhang + W * 0.75)
    holder_y = body_top + 68
    _draw_pc_holder_front(svg, holder_x, holder_y, 45, 96)


def _draw_desk_iso(svg: SVG, spec: FurnitureSpec, x, y, w, h) -> None:
    # Layout is intentionally hand-tuned for the current desk archetype; the
    # source dimensions still drive labels and proportions in future passes.
    shadow_cx = x + w * 0.52
    svg.ellipse(shadow_cx, y + h - 26, w * 0.37, 26,
                fill="#000000", stroke="none", opacity=0.10)

    # Top slab in a gentle presentation perspective.
    top = [(x + 98, y + 104), (x + 574, y + 148),
           (x + 660, y + 112), (x + 184, y + 68)]
    front = [(x + 98, y + 104), (x + 574, y + 148),
             (x + 574, y + 174), (x + 98, y + 130)]
    right = [(x + 574, y + 148), (x + 660, y + 112),
             (x + 660, y + 138), (x + 574, y + 174)]
    svg.polygon(front, fill="#d8d0bf", stroke="#2f2d28", width=0.9)
    svg.polygon(right, fill="#c8beaa", stroke="#2f2d28", width=0.9)
    svg.polygon(top, fill="url(#techTop)", stroke="#2b2924", width=1.0)

    # Left and right side panels.
    left_outer = [(x + 118, y + 129), (x + 164, y + 151),
                  (x + 164, y + 292), (x + 118, y + 268)]
    right_outer = [(x + 582, y + 173), (x + 637, y + 149),
                   (x + 637, y + 292), (x + 582, y + 322)]
    svg.polygon(left_outer, fill="url(#techSide)", stroke="#303030", width=0.95)
    svg.polygon(right_outer, fill="url(#techSide)", stroke="#303030", width=0.95)

    # Front screen set back slightly.
    screen = [(x + 166, y + 152), (x + 570, y + 190),
              (x + 570, y + 244), (x + 166, y + 205)]
    svg.polygon(screen, fill="url(#techFront)", stroke="#514d45", width=0.9,
                opacity=0.98)

    # Brass inserts on visible verticals.
    for pts in [
        ((x + 123, y + 134), (x + 123, y + 266)),
        ((x + 155, y + 149), (x + 155, y + 287)),
        ((x + 586, y + 176), (x + 586, y + 317)),
        ((x + 631, y + 153), (x + 631, y + 288)),
    ]:
        (x1, y1), (x2, y2) = pts
        svg.line(x1, y1, x2, y2, stroke=config.COL_BRASS_DARK, width=1.0)
        svg.line(x1 + 3, y1 + 1, x2 + 3, y2 - 1,
                 stroke=config.COL_BRASS_LIGHT, width=0.75)

    # Small brass feet / spacers.
    for px, py in [(x + 119, y + 269), (x + 154, y + 288),
                   (x + 582, y + 322), (x + 631, y + 291)]:
        svg.polygon([(px - 2, py), (px + 13, py + 5),
                     (px + 10, py + 13), (px - 5, py + 7)],
                    fill="#b48f34", stroke="#6f5318", width=0.6)

    # Cable tray and channel in isometry.
    svg.polygon([(x + 346, y + 174), (x + 464, y + 185),
                 (x + 452, y + 193), (x + 332, y + 181)],
                fill="url(#techDark)", stroke="#000000", width=0.6)
    _draw_cable_hose(svg, x + 452, y + 188, x + 478, y + 300,
                     scale=0.95, lean=-0.35)
    _draw_pc_holder_iso(svg, x + 550, y + 212)


def _draw_cable_hose(svg: SVG, x1, y1, x2, y2, scale=1.0, lean=0.28) -> None:
    mx = (x1 + x2) / 2 + 38 * lean
    d = f"M {_f(x1)} {_f(y1)} C {_f(mx)} {_f(y1 + 35 * scale)} {_f(mx)} {_f(y2 - 55 * scale)} {_f(x2)} {_f(y2)}"
    svg.add(f'<path d="{d}" fill="none" stroke="#050505" stroke-width="{_f(16 * scale)}" '
            f'stroke-linecap="round" opacity="0.98"/>')
    svg.add(f'<path d="{d}" fill="none" stroke="#343434" stroke-width="{_f(3.2 * scale)}" '
            f'stroke-linecap="round" opacity="0.55"/>')

    for i in range(10):
        t = i / 9
        px = _cubic(x1, mx, mx, x2, t)
        py = _cubic(y1, y1 + 35 * scale, y2 - 55 * scale, y2, t)
        ang = -22 + 20 * math.sin(t * math.pi)
        svg.add(f'<ellipse cx="{_f(px)}" cy="{_f(py)}" rx="{_f(9 * scale)}" '
                f'ry="{_f(2.2 * scale)}" fill="#111111" stroke="#5a5a5a" '
                f'stroke-width="{_f(0.45 * scale)}" transform="rotate({_f(ang)} {_f(px)} {_f(py)})"/>')
    svg.ellipse(x2, y2 + 5 * scale, 18 * scale, 5 * scale,
                fill="#070707", stroke="#000000", width=0.5)


def _draw_pc_holder_front(svg: SVG, x, y, w, h) -> None:
    svg.rect(x, y, w, h, fill="url(#techDark)", stroke="#020202", width=0.8, rx=1.5)
    svg.rect(x + 7, y + 9, w - 14, h - 18, fill="#1d1d1d",
             stroke="#777777", width=0.55, rx=1)
    for sx in (x + 13, x + w - 17):
        svg.line(sx, y + 16, sx, y + h - 16, stroke="#8b8b8b", width=1.0)
    for cy in (y + 18, y + h - 18):
        svg.circle(x + 8, cy, 1.5, fill="#d6d6d6", stroke="none")
        svg.circle(x + w - 8, cy, 1.5, fill="#d6d6d6", stroke="none")
    svg.rect(x - 9, y + 14, 9, h - 28, fill="#101010", stroke="#030303", width=0.5)
    svg.rect(x + w, y + 14, 8, h - 28, fill="#101010", stroke="#030303", width=0.5)


def _draw_pc_holder_iso(svg: SVG, x, y) -> None:
    body = [(x, y), (x + 41, y + 12), (x + 41, y + 88), (x, y + 73)]
    svg.polygon(body, fill="url(#techDark)", stroke="#050505", width=0.7)
    svg.polyline([(x + 10, y + 12), (x + 30, y + 18), (x + 30, y + 76),
                  (x + 10, y + 68), (x + 10, y + 12)],
                 stroke="#8d8d8d", width=0.7, fill="none")
    for dx in (12, 28):
        svg.line(x + dx, y + 20, x + dx, y + 65, stroke="#7a7a7a", width=0.8)
    svg.polygon([(x - 9, y + 8), (x, y + 11), (x, y + 65), (x - 9, y + 60)],
                fill="#101010", stroke="#050505", width=0.5)


def _draw_desk_materials(svg: SVG, spec: FurnitureSpec, x, y, w) -> None:
    color = _color_label(spec)
    lines = [
        f"Столешница: МДФ 50мм, {color}, матовое.",
        f"Опоры: МДФ 50мм, {color}.",
        "Передний экран: МДФ 25мм, h=300мм.",
        "Вставки: латунь.",
        "Ножки: Валмакс h=22мм, латунь.",
        "Подвес СБ: профтруба 40x20мм, RAL 9011.",
        "Кабель-канал: в цвет + гибкий чёрный.",
        "Отрыв от опор: 25мм.",
        "",
        "*Фактические замеры обязательны.",
    ]
    yy = y
    for line in lines:
        if line:
            svg.text(x, yy, line, size=13, fill="#171717",
                     family=config.FONT_SANS)
        yy += 21


def _title(spec: FurnitureSpec) -> str:
    label = spec.dims.label()
    if spec.archetype == "desk_panel":
        return f"Рабочий стол {label}".strip()
    return f"{spec.name} {label}".strip()


def _color_label(spec: FurnitureSpec) -> str:
    if spec.material.color_system and spec.material.color_code:
        return f"{spec.material.color_system} {spec.material.color_code}"
    return "NCS 3000"


def _mm(v) -> str:
    if abs(v - round(v)) < 1e-6:
        return str(int(round(v)))
    return f"{v:g}"


def _f(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _cubic(a, b, c, d, t):
    return ((1 - t) ** 3 * a + 3 * (1 - t) ** 2 * t * b
            + 3 * (1 - t) * t ** 2 * c + t ** 3 * d)
