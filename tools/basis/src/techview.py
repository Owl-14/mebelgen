"""Технический чертёж листа: фронт + вид сбоку, размерки и выноски (AKD-7/AKD-8).

Строится из project.json (точные placement — якоря по РЕАЛЬНЫМ деталям, не догадки):
- фронтальная проекция XY (painter по глубине) — фасады, кромки корпуса, полки
  в открытых секциях, цоколь/опоры, контактная тень: фронт читается как аккуратный
  клиентский чертёж, а не «однотонное мыло» (AKD-7);
- вид сбоку ZY — силуэт боковины + нахлёст фасада + задняя стенка; глубина
  показана ТОЛЬКО здесь (без дублей с фронтом, AKD-8);
- размерки: габарит W/H на фронте, D на боку; внутренние цепочки — уровни полок
  (слева, ранг 1) и ширины секций (снизу, ранг 1) там, где есть наполнение;
- выноски: слоты справа от фронта и справа от бока, аллокатор stack() из
  sheet_layout (без перекрытий и перекрещиваний), автоперенос длинных подписей,
  запрет выхода за рамку;
- self-check: check-функция считает пересечения текстовых боксов и выходы за
  рамку — build_techview_svg возвращает (svg, issues); issues == [] обязателен.
"""

from __future__ import annotations

from typing import Any

from .sheet_layout import Label, stack

# ------------------------------------------------------------------ стиль

_INK = "#3f3428"           # контуры деталей
_DIM = "#5b6470"           # размерные линии/текст
_CALL = "#4a4f57"          # выноски
_MUT = "#8a8f98"
_FS_DIM = 10.0
_FS_CALL = 10.5
_FS_TITLE = 11.0
_CHAR_W = 0.58             # ширина символа в долях кегля (оценка bbox)
_LBL_COL_W = 168.0         # колонка выносок фронта
_LBL_COL_W2 = 118.0        # колонка выносок бока
_MAX_CHARS = 24            # автоперенос подписи

_FILL = {
    "facade": "url(#tvGrain)", "carcass": "#c9a878", "plinth": "#8a6f4d",
    "back": "#efe7da", "shelf": "#d3b285", "interior": "#c2a172",
}

_FACADE_T = ("door_front", "drawer_front", "facade", "front_panel")
_CARCASS_T = ("side_left", "side_right", "top", "bottom", "vertical_partition")


def _kind(p: dict[str, Any]) -> str:
    t = str(p.get("type") or "").lower()
    if t in _FACADE_T:
        return "facade"
    if t == "plinth":
        return "plinth"
    if t == "back":
        return "back"
    if t == "shelf":
        return "shelf"
    if t in _CARCASS_T:
        return "carcass"
    return "interior"


def _fmt(v: float) -> str:
    return str(int(round(v)))


def _wrap(text: str, max_chars: int = _MAX_CHARS) -> list[str]:
    """Автоперенос подписи по словам (до 2 строк, дальше — многоточие)."""
    words = str(text).split()
    lines: list[str] = []
    cur = ""
    for w in words:
        cand = (cur + " " + w).strip()
        if len(cand) <= max_chars or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > 2:
        lines = [lines[0], (lines[1][: max_chars - 1] + "…")]
    return lines


def _e(s: Any) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class _Svg:
    """Мини-построитель SVG + учёт текстовых боксов для collision-check."""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.text_boxes: list[tuple[float, float, float, float, str]] = []

    def line(self, x1, y1, x2, y2, stroke=_INK, w=1.0, dash=None):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                          f'stroke="{stroke}" stroke-width="{w}"{d}/>')

    def rect(self, x, y, w, h, fill, stroke=_INK, sw=0.9, rx=0.0, panel: str = ""):
        r = f' rx="{rx}"' if rx else ""
        s = f' stroke="{stroke}" stroke-width="{sw}"' if stroke else ' stroke="none"'
        dp = f' data-panel="{_e(panel)}"' if panel else ""   # связь 3D↔чертёж (AKD-120)
        self.parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
                          f'fill="{fill}"{s}{r}{dp}/>')

    def poly(self, pts, stroke=_CALL, w=0.8, fill="none"):
        p = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        self.parts.append(f'<polyline points="{p}" fill="{fill}" stroke="{stroke}" stroke-width="{w}"/>')

    def circle(self, cx, cy, r, fill=_CALL):
        self.parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r}" fill="{fill}"/>')

    def ellipse(self, cx, cy, rx, ry, fill, extra=""):
        self.parts.append(f'<ellipse cx="{cx:.1f}" cy="{cy:.1f}" rx="{rx:.1f}" ry="{ry:.1f}" '
                          f'fill="{fill}" {extra}/>')

    def text(self, x, y, s, size=_FS_DIM, fill=_DIM, anchor="middle", weight=""):
        wattr = f' font-weight="{weight}"' if weight else ""
        self.parts.append(f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" '
                          f'text-anchor="{anchor}" font-family="Segoe UI, Arial, sans-serif"{wattr}>{_e(s)}</text>')
        tw = len(str(s)) * size * _CHAR_W
        x1 = x - tw / 2 if anchor == "middle" else (x - tw if anchor == "end" else x)
        # bbox по cap-height: не выше межстрочного интервала многострочной подписи
        self.text_boxes.append((x1, y - size * 0.78, x1 + tw, y + size * 0.22, str(s)))


def _arrow(svg: _Svg, x, y, dx, dy, size=4.2, color=_DIM):
    import math
    ln = math.hypot(dx, dy) or 1.0
    ux, uy = dx / ln, dy / ln
    px, py = -uy, ux
    bx, by = x - ux * size, y - uy * size
    svg.parts.append(
        f'<polygon points="{x:.1f},{y:.1f} {bx + px * size * .34:.1f},{by + py * size * .34:.1f} '
        f'{bx - px * size * .34:.1f},{by - py * size * .34:.1f}" fill="{color}"/>')


def _dim_h(svg: _Svg, x1, x2, y_obj, y_dim, text):
    if x1 > x2:
        x1, x2 = x2, x1
    svg.line(x1, y_obj + 3, x1, y_dim + 3, stroke=_DIM, w=0.6)
    svg.line(x2, y_obj + 3, x2, y_dim + 3, stroke=_DIM, w=0.6)
    svg.line(x1, y_dim, x2, y_dim, stroke=_DIM, w=0.8)
    _arrow(svg, x1, y_dim, -1, 0)
    _arrow(svg, x2, y_dim, 1, 0)
    svg.text((x1 + x2) / 2, y_dim - 3.5, text)


def _dim_v(svg: _Svg, y1, y2, x_obj, x_dim, text):
    if y1 > y2:
        y1, y2 = y2, y1
    svg.line(x_obj - 3, y1, x_dim - 3, y1, stroke=_DIM, w=0.6)
    svg.line(x_obj - 3, y2, x_dim - 3, y2, stroke=_DIM, w=0.6)
    svg.line(x_dim, y1, x_dim, y2, stroke=_DIM, w=0.8)
    _arrow(svg, x_dim, y1, 0, -1)
    _arrow(svg, x_dim, y2, 0, 1)
    svg.text(x_dim - 5, (y1 + y2) / 2 + _FS_DIM * 0.35, text, anchor="end")


# ------------------------------------------------------------------ выноски

def _clean_name(name: str) -> str:
    """Имя детали для выноски: без хвостовых номеров и скобочных меток секций
    («Полка гардероб (wardrobe) 2» → «Полка гардероб»)."""
    import re
    s = str(name).rstrip(" 0123456789")               # сначала номер...
    s = re.sub(r"\s*\([^)]*\)\s*$", "", s)            # ...потом скобочную метку
    return s.rstrip(" 0123456789") or str(name)


def _pick_callouts(panels: list[dict[str, Any]], legs_h: float, legs_note: str) -> list[dict[str, Any]]:
    """Якоря по реальным деталям: по одной выноске на тип, подпись — ИМЯ детали
    (тип врёт на не-корпусной мебели: «Пьедестал», «Царга задняя» — не «Перегородка»)."""
    order = ["top", "door_front", "drawer_front", "side_right",
             "vertical_partition", "shelf", "plinth"]
    out: list[dict[str, Any]] = []
    for t in order:
        cand = [p for p in panels if p.get("type") == t and p.get("placement")]
        if not cand:
            continue
        # якорь — деталь, ближайшая к правому краю (короткая выноска)
        p = max(cand, key=lambda q: q["placement"]["x2"])
        pl = p["placement"]
        th = p.get("thickness")
        label = _clean_name(p.get("name") or t)
        txt = f"{label} {_fmt(th)} мм" if th else label
        out.append({"x": pl["x2"], "y": (pl["y1"] + pl["y2"]) / 2, "text": txt})
    if legs_h and not any("Цоколь" in c["text"] for c in out):
        out.append({"x": None, "y": legs_h / 2, "text": legs_note or f"Опоры {_fmt(legs_h)} мм"})
    return out


def _draw_callout_column(svg: _Svg, callouts, view_right, col_x, band_top, band_bottom):
    """Стек подписей в колонке col_x, лидеры от якорей. Без перекрытий (stack)."""
    labs = []
    for c in callouts:
        lines = _wrap(c["text"])
        h = len(lines) * (_FS_CALL + 2.5) + 4
        labs.append(Label(target_y=c["y"], height=h, payload=(c, lines)))
    stack(labs, band_top, band_bottom, gap=5)
    for lab in labs:
        c, lines = lab.payload
        ax = c["x"] if c["x"] is not None else view_right
        ay = lab.target_y                       # якорь — исходная точка детали
        svg.circle(ax, ay, 1.8)
        svg.poly([(ax, ay), (view_right + 14, ay), (col_x - 5, lab.y)], stroke=_CALL, w=0.75)
        ty = lab.y - (len(lines) - 1) * (_FS_CALL + 2.5) / 2 + _FS_CALL * 0.35
        for ln in lines:
            svg.text(col_x, ty, ln, size=_FS_CALL, fill=_CALL, anchor="start")
            ty += _FS_CALL + 2.5


# ------------------------------------------------------------------ построение

def build_techview_svg(project: dict[str, Any]) -> tuple[str, list[str]]:
    """SVG чертежа (фронт + бок) и список проблем self-check (должен быть пуст)."""
    panels = [p for p in project.get("panels", []) if isinstance(p.get("placement"), dict)]
    if not panels:
        return "<svg xmlns='http://www.w3.org/2000/svg'/>", ["нет панелей с placement"]

    xs1 = min(p["placement"]["x1"] for p in panels)
    xs2 = max(p["placement"]["x2"] for p in panels)
    ys2 = max(p["placement"]["y2"] for p in panels)
    zs1 = min(p["placement"]["z1"] for p in panels)
    zs2 = max(p["placement"]["z2"] for p in panels)
    W, H = xs2 - xs1, ys2            # пол y=0 — включая опоры
    D = zs2 - min(zs1, 0)

    legs = ((project.get("hardware") or {}).get("legs") or {})
    legs_h = float(legs.get("height") or 0)
    has_plinth = any(p.get("type") == "plinth" for p in panels)
    lt = str(legs.get("type") or "").strip()
    legs_note = (f"{lt[:1].upper()}{lt[1:]} {_fmt(legs_h)} мм" if lt else f"Опоры {_fmt(legs_h)} мм") if legs_h else ""

    # --- масштаб и раскладка листа ---
    sheet_h = 560.0
    m_left, m_top, m_bot = 86.0, 46.0, 74.0
    draw_h = sheet_h - m_top - m_bot
    scale = draw_h / max(H, 1)
    max_total = 1160.0
    need = W * scale + _LBL_COL_W + (D * scale) + _LBL_COL_W2 + m_left + 60
    if need > max_total:
        scale *= (max_total - _LBL_COL_W - _LBL_COL_W2 - m_left - 60) / (W * scale + D * scale)

    fx0, fy0 = m_left, m_top                      # фронт: левый-верх области
    fw, fh = W * scale, H * scale
    fy1 = fy0 + fh                                # линия пола
    sx0 = fx0 + fw + _LBL_COL_W + 26              # бок
    sw_, sh_ = D * scale, fh
    total_w = sx0 + sw_ + _LBL_COL_W2 + 30
    total_h = sheet_h

    def FX(x):
        return fx0 + (x - xs1) * scale
    def FY(y):
        return fy1 - y * scale
    def SX(z):
        return sx0 + (z - min(zs1, 0)) * scale

    svg = _Svg()
    svg.parts.append(
        '<defs>'
        '<pattern id="tvGrain" width="7" height="7" patternUnits="userSpaceOnUse">'
        '<rect width="7" height="7" fill="#d9b98c"/>'
        '<path d="M2 0 V7 M5.5 0 V7" stroke="#cfae7e" stroke-width="0.7"/></pattern>'
        '<filter id="tvSoft" x="-40%" y="-40%" width="180%" height="180%">'
        '<feGaussianBlur stdDeviation="4"/></filter>'
        '</defs>')

    # контактные тени (AKD-7)
    svg.ellipse(fx0 + fw / 2, fy1 + 5, fw * 0.52, 7, "#00000022", 'filter="url(#tvSoft)"')
    svg.ellipse(sx0 + sw_ / 2, fy1 + 5, sw_ * 0.62, 7, "#00000022", 'filter="url(#tvSoft)"')

    # --- ФРОНТ: painter по глубине (дальние первыми), фасады последними ---
    for p in sorted(panels, key=lambda q: (-q["placement"]["z2"], -q["placement"]["z1"])):
        pl = p["placement"]
        k = _kind(p)
        x, y = FX(pl["x1"]), FY(pl["y2"])
        w, h = (pl["x2"] - pl["x1"]) * scale, (pl["y2"] - pl["y1"]) * scale
        svg.rect(x, y, max(w, 1), max(h, 1), _FILL[k], stroke=_INK, sw=0.9,
                 panel=str(p.get("name", "")))
        if k == "facade" and w > 26 and h > 26:      # кромка по периметру фасада
            svg.rect(x + 2.2, y + 2.2, w - 4.4, h - 4.4, "none", stroke="#b08d5b", sw=0.8)
        # высота фасада ящика — внутри детали (читаемо, без внешних цепочек)
        if p.get("type") == "drawer_front" and h > 22:
            svg.text(x + w / 2, y + h / 2 + _FS_DIM * 0.35, f"h {_fmt(pl['y2'] - pl['y1'])}",
                     size=_FS_DIM, fill="#7a6647")

    # опоры, если нет цоколя (видимая конструкция низа — AKD-7)
    if legs_h > 0 and not has_plinth:
        for lx in (xs1 + 30, xs2 - 30):
            svg.rect(FX(lx) - 4, fy1 - legs_h * scale, 8, legs_h * scale, "#6f5a3e", stroke=_INK, sw=0.8)

    # --- БОК: силуэт (painter по X — ближняя боковина закрывает интерьер) ---
    for p in sorted(panels, key=lambda q: (q["placement"]["x1"], q["placement"]["x2"])):
        pl = p["placement"]
        k = _kind(p)
        x, y = SX(pl["z1"]), FY(pl["y2"])
        w, h = (pl["z2"] - pl["z1"]) * scale, (pl["y2"] - pl["y1"]) * scale
        svg.rect(x, y, max(w, 1), max(h, 1), _FILL["back" if k == "back" else ("facade" if k == "facade" else "carcass")],
                 stroke=_INK, sw=0.9, panel=str(p.get("name", "")))
    if legs_h > 0 and not has_plinth:
        for lz in (min(zs1, 0) + 30, zs2 - 30):
            svg.rect(SX(lz) - 4, fy1 - legs_h * scale, 8, legs_h * scale, "#6f5a3e", stroke=_INK, sw=0.8)

    # линия пола
    svg.line(fx0 - 26, fy1, sx0 + sw_ + 20, fy1, stroke=_MUT, w=0.7, dash="5,4")

    # --- размерки (AKD-8): W/H на фронте, D на боку, без дублей ---
    _dim_h(svg, fx0, fx0 + fw, fy1, fy1 + 30, _fmt(W))
    _dim_v(svg, fy0, fy1, fx0, fx0 - 40, _fmt(H))
    # глубина: обычно по корпусу (z 0..zs2, нахлёст накладного фасада не в размер);
    # при реальном свесе крышки вперёд (zs1 < −20) — полный габарит
    if zs1 < -20:
        _dim_h(svg, SX(zs1), SX(zs2), fy1, fy1 + 30, _fmt(zs2 - zs1))
    else:
        _dim_h(svg, SX(0), SX(zs2), fy1, fy1 + 30, _fmt(zs2))

    # внутренние цепочки — только при наполнении
    parts_v = sorted([p for p in panels if p.get("type") == "vertical_partition"],
                     key=lambda q: q["placement"]["x1"])
    has_sides = any(p.get("type") in ("side_left", "side_right") for p in panels)
    if parts_v and has_sides:                     # ширины секций — только у корпусной
                                                  # мебели (у стола пьедестал ≠ секции)
        side_in_l = max((p["placement"]["x2"] for p in panels if p.get("type") == "side_left"), default=xs1)
        side_in_r = min((p["placement"]["x1"] for p in panels if p.get("type") == "side_right"), default=xs2)
        bounds = [side_in_l] + [b for q in parts_v for b in
                                (q["placement"]["x1"], q["placement"]["x2"])] + [side_in_r]
        for a, b in zip(bounds[0::2], bounds[1::2]):
            if b - a > 40:
                _dim_h(svg, FX(a), FX(b), fy1, fy1 + 14, _fmt(b - a))

    shelves = [p for p in panels if p.get("type") == "shelf"]
    if shelves:                                   # уровни полок (слева, ранг 1)
        lx1 = min(p["placement"]["x1"] for p in shelves)
        col = [p for p in shelves if abs(p["placement"]["x1"] - lx1) < 2]
        lvls = sorted({round(p["placement"]["y1"], 1) for p in col})
        base = max((p["placement"]["y2"] for p in panels if p.get("type") == "bottom"), default=0)
        prev = base
        for lv in lvls:
            if lv - prev > 40:
                _dim_v(svg, FY(lv), FY(prev), fx0, fx0 - 16, _fmt(lv - prev))
            prev = lv

    # --- выноски: колонка справа от фронта + одиночная у бока ---
    calls = _pick_callouts(panels, legs_h if not has_plinth else 0, legs_note)
    sc = []
    for c in calls:
        ax = FX(c["x"]) if c["x"] is not None else FX(xs1 + 30) + 4
        sc.append({"x": ax, "y": FY(c["y"]), "text": c["text"]})
    _draw_callout_column(svg, sc, fx0 + fw, fx0 + fw + 26, fy0 + 4, fy1 - 4)

    backs = [p for p in panels if p.get("type") == "back"]
    if backs:
        pl = backs[0]["placement"]
        nm = _clean_name(backs[0].get("name") or "Задняя стенка")   # у стола это царга
        sc2 = [{"x": SX(pl["z2"]) - 1, "y": FY((pl["y1"] + pl["y2"]) / 2),
                "text": f"{nm} {_fmt(backs[0].get('thickness') or (pl['z2'] - pl['z1']))} мм"}]
        _draw_callout_column(svg, sc2, sx0 + sw_, sx0 + sw_ + 22, fy0 + 4, fy1 - 4)

    # подписи видов
    svg.text(fx0 + fw / 2, fy1 + 56, "ФРОНТ", size=_FS_TITLE, fill=_MUT, weight="600")
    svg.text(sx0 + sw_ / 2, fy1 + 56, "ВИД СБОКУ", size=_FS_TITLE, fill=_MUT, weight="600")

    # --- self-check: пересечения текстов + выход за рамку (AKD-8) ---
    issues: list[str] = []
    tb = svg.text_boxes
    for i in range(len(tb)):
        a = tb[i]
        if a[0] < 0 or a[1] < 0 or a[2] > total_w or a[3] > total_h:
            issues.append(f"текст за рамкой: «{a[4]}»")
        for j in range(i + 1, len(tb)):
            b = tb[j]
            if a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]:
                issues.append(f"пересечение подписей: «{a[4]}» × «{b[4]}»")

    body = "".join(svg.parts)
    out = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total_w:.0f} {total_h:.0f}" '
           f'font-family="Segoe UI, Arial, sans-serif">{body}</svg>')
    return out, issues
