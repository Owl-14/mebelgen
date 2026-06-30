"""Coffee / journal tables: round pedestal, fluted cylinder, rectangular/cube."""
from __future__ import annotations

import math

from .. import config
from ..draw import dimensions as dim
from ..draw.callouts import callout
from ..geometry.iso import IsoView
from ..geometry.primitives import iso_box, iso_cylinder
from ..geometry.views import View2D
from .base import Archetype, Area


class _TableBase(Archetype):
    def three_panel(self, svg, area):
        """Front (top-left), Top (top-right), Iso (bottom)."""
        top = Area(area.x, area.y, area.w, area.h * 0.42)
        front = Area(top.x, top.y, top.w * 0.5, top.h)
        topv = Area(top.x + top.w * 0.5, top.y, top.w * 0.5, top.h)
        iso = Area(area.x, area.y + area.h * 0.46, area.w, area.h * 0.54)
        self.view_label(svg, front.x + front.w / 2, top.y + 6, "ВИД СПЕРЕДИ")
        self.view_label(svg, topv.x + topv.w / 2, top.y + 6, "ВИД СВЕРХУ")
        return front, topv, iso

    def _iso_label(self, svg, iso):
        self.view_label(svg, iso.x + iso.w / 2, iso.y + 2, "3D ИЗОМЕТРИЯ")


class CoffeeRound(_TableBase):
    def draw(self, svg, area: Area):
        s = self.spec
        Dia = s.dims.diameter or 500
        H = s.dims.h or 420
        top_th = 38
        ped_d = Dia * 0.6
        front, topv, iso = self.three_panel(svg, area)
        self._front(svg, front, Dia, H, top_th, ped_d)
        self._top(svg, topv, Dia)
        if not self.draw_hero(svg, iso):
            self._iso_label(svg, iso)
            self._iso(svg, iso, Dia, H, top_th, ped_d)

    def _front(self, svg, a, Dia, H, top_th, ped_d):
        v = View2D(a.x, a.y + 14, a.w, a.h - 14, Dia, H, pad=30, align="bottom")
        P = v.px
        # pedestal
        px0, py0 = P(Dia / 2 - ped_d / 2, 0)
        px1, py1 = P(Dia / 2 + ped_d / 2, H - top_th)
        svg.rect(min(px0, px1), py1, abs(px1 - px0), py0 - py1,
                 fill=self.pal.front, stroke=self.pal.line, width=1.2)
        # top slab
        tx0, ty0 = P(0, H - top_th)
        tx1, ty1 = P(Dia, H)
        svg.rect(tx0, ty1, tx1 - tx0, ty0 - ty1, fill=self.pal.top,
                 stroke=self.pal.line, width=1.2, rx=6)
        # dims
        dim.dim_h(svg, tx0, tx1, ty1, ty1 - 18, "Ø" + _mm(Dia))
        dim.dim_v(svg, ty1, P(0, 0)[1], tx0, tx0 - 18, _mm(H))
        dim.dim_v(svg, ty0, ty1, tx1, tx1 + 14, _mm(top_th), text_side="right")
        dim.dim_h(svg, min(px0, px1), max(px0, px1), P(0, 0)[1] + 16,
                  P(0, 0)[1] + 16, "Ø~" + _mm(ped_d), ext=False)

    def _top(self, svg, a, Dia):
        v = View2D(a.x, a.y + 14, a.w, a.h - 14, Dia, Dia, pad=30)
        cx, cy = v.px(Dia / 2, Dia / 2)
        r = v.s(Dia / 2)
        svg.circle(cx, cy, r, fill=self.pal.top, stroke=self.pal.line, width=1.2)
        dim.dim_h(svg, cx - r, cx + r, cy - r, cy - r - 16, "Ø" + _mm(Dia), ext=False)

    def _iso(self, svg, a, Dia, H, top_th, ped_d):
        iso = IsoView.fit(Dia, Dia, H, (a.x, a.y + 12, a.w, a.h - 12), pad=46)
        iso_cylinder(svg, iso, Dia / 2, Dia / 2, 0, ped_d / 2, H - top_th)
        # top slab as a flat cylinder
        iso_cylinder(svg, iso, Dia / 2, Dia / 2, H - top_th, Dia / 2, top_th,
                     top=self.pal.top, side=self.pal.front)
        lines = [self.color_text(), "Покрытие: прозрачный матовый лак",
                 "Цвет по согласованию", "Подпятники: фетровые, 4 шт."]
        anchors = [iso.p(Dia / 2, 0, H), iso.p(Dia / 2, 0, H * 0.5),
                   iso.p(Dia / 2, 0, H * 0.4), iso.p(Dia / 2, 0, H * 0.05)]
        for i, text in enumerate(lines[:4]):
            ax, ay = anchors[i % len(anchors)]
            callout(svg, ax, ay, text, side="right",
                    text_x=a.x + a.w - 158, text_y=a.y + 34 + i * 30, max_chars=24)


class CoffeeFluted(_TableBase):
    def draw(self, svg, area: Area):
        s = self.spec
        Dia = s.dims.diameter or 400
        H = s.dims.h or 420
        front, topv, iso = self.three_panel(svg, area)
        self._front(svg, front, Dia, H)
        self._top(svg, topv, Dia)
        if not self.draw_hero(svg, iso):
            self._iso_label(svg, iso)
            self._iso(svg, iso, Dia, H)

    def _front(self, svg, a, Dia, H):
        v = View2D(a.x, a.y + 14, a.w, a.h - 14, Dia, H, pad=30, align="bottom")
        P = v.px
        x0, y0 = P(0, 0)
        x1, y1 = P(Dia, H)
        svg.rect(min(x0, x1), y1, abs(x1 - x0), y0 - y1, fill=self.pal.front,
                 stroke=self.pal.line, width=1.2, rx=4)
        # flutes
        n = max(12, int(Dia / 20))
        for i in range(1, n):
            fx, _ = P(Dia * i / n, 0)
            svg.line(fx, y1 + 2, fx, y0 - 2, stroke=self.pal.line_soft, width=0.6)
        dim.dim_h(svg, min(x0, x1), max(x0, x1), y1, y1 - 18, "Ø" + _mm(Dia))
        dim.dim_v(svg, y1, y0, min(x0, x1), min(x0, x1) - 18, _mm(H))

    def _top(self, svg, a, Dia):
        v = View2D(a.x, a.y + 14, a.w, a.h - 14, Dia, Dia, pad=30)
        cx, cy = v.px(Dia / 2, Dia / 2)
        r = v.s(Dia / 2)
        svg.circle(cx, cy, r, fill=self.pal.top, stroke=self.pal.line, width=1.2)
        dim.dim_h(svg, cx - r, cx + r, cy - r, cy - r - 16, "Ø" + _mm(Dia), ext=False)

    def _iso(self, svg, a, Dia, H):
        iso = IsoView.fit(Dia, Dia, H, (a.x, a.y + 12, a.w, a.h - 12), pad=46)
        iso_cylinder(svg, iso, Dia / 2, Dia / 2, 0, Dia / 2, H, fluted=True)
        lines = [self.color_text(), "Рифлёная поверхность (флютинг)",
                 "Цвет по согласованию", "Подпятники: фетровые, 4 шт."]
        anchors = [iso.p(Dia / 2, 0, H), iso.p(Dia * 0.5, 0, H * 0.5),
                   iso.p(Dia / 2, 0, H * 0.35), iso.p(Dia / 2, 0, H * 0.08)]
        for i, text in enumerate(lines[:4]):
            ax, ay = anchors[i % len(anchors)]
            callout(svg, ax, ay, text, side="right",
                    text_x=a.x + a.w - 158, text_y=a.y + 34 + i * 30, max_chars=24)


class CoffeeRect(_TableBase):
    def draw(self, svg, area: Area):
        s = self.spec
        W = s.dims.w or 1000
        D = s.dims.d or 450
        H = s.dims.h or 450
        top_th = s.features.get("thickness", {}).get("worktop", 50)
        legged = W >= 800
        front, topv, iso = self.three_panel(svg, area)
        self._front(svg, front, W, H, top_th, legged)
        self._top(svg, topv, W, D)
        if not self.draw_hero(svg, iso):
            self._iso_label(svg, iso)
            self._iso(svg, iso, W, D, H, top_th, legged)

    def _front(self, svg, a, W, H, top_th, legged):
        v = View2D(a.x, a.y + 14, a.w, a.h - 14, W, H, pad=30, align="bottom")
        P = v.px
        if legged:
            leg_w = W * 0.22
            leg_h = H - top_th
            for cxmm in (W * 0.25, W * 0.75):
                lx0, ly0 = P(cxmm - leg_w / 2, 0)
                lx1, ly1 = P(cxmm + leg_w / 2, leg_h)
                svg.rect(min(lx0, lx1), ly1, abs(lx1 - lx0), ly0 - ly1,
                         fill=self.pal.front, stroke=self.pal.line, width=1.1)
            tx0, ty0 = P(0, H - top_th)
            tx1, ty1 = P(W, H)
            svg.rect(tx0, ty1, tx1 - tx0, ty0 - ty1, fill=self.pal.top,
                     stroke=self.pal.line, width=1.2, rx=8)
            dim.dim_v(svg, ty0, ty1, tx1, tx1 + 14, _mm(top_th), text_side="right")
            dim.dim_h(svg, P(W * 0.25 - leg_w / 2, 0)[0], P(W * 0.25 + leg_w / 2, 0)[0],
                      P(0, 0)[1] + 14, P(0, 0)[1] + 14, "~" + _mm(leg_w), ext=False)
        else:  # cube / box
            x0, y0 = P(0, 0)
            x1, y1 = P(W, H)
            svg.rect(min(x0, x1), y1, abs(x1 - x0), y0 - y1, fill=self.pal.front,
                     stroke=self.pal.line, width=1.2, rx=4)
        tx0, _ = P(0, H)
        tx1, ty1 = P(W, H)
        dim.dim_h(svg, tx0, tx1, ty1, ty1 - 18, _mm(W))
        dim.dim_v(svg, ty1, P(0, 0)[1], tx0, tx0 - 18, _mm(H))

    def _top(self, svg, a, W, D):
        v = View2D(a.x, a.y + 14, a.w, a.h - 14, W, D, pad=30)
        x0, y0 = v.px(0, 0)
        x1, y1 = v.px(W, D)
        svg.rect(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0),
                 fill=self.pal.top, stroke=self.pal.line, width=1.2, rx=10)
        dim.dim_h(svg, min(x0, x1), max(x0, x1), min(y0, y1), min(y0, y1) - 16,
                  _mm(W), ext=False)
        dim.dim_v(svg, min(y0, y1), max(y0, y1), max(x0, x1), max(x0, x1) + 14,
                  _mm(D), text_side="right")

    def _iso(self, svg, a, W, D, H, top_th, legged):
        iso = IsoView.fit(W, D, H, (a.x, a.y + 12, a.w, a.h - 12), pad=46)
        if legged:
            leg_w = W * 0.18
            leg_d = D * 0.5
            for cxmm in (W * 0.25, W * 0.75):
                iso_cylinder(svg, iso, cxmm, D / 2, 0, leg_w / 2, H - top_th,
                             top=self.pal.front, side=self.pal.side)
            iso_box(svg, iso, 0, 0, H - top_th, W, D, top_th)
        else:
            iso_box(svg, iso, 0, 0, 0, W, D, H)
        lines = [self.color_text(), "Столешница 50 мм, скруглённые углы R50",
                 "Все торцы в цвет корпуса"]
        anchors = [iso.p(W / 2, 0, H), iso.p(W * 0.5, 0, H * 0.5), iso.p(W, D * 0.5, H * 0.6)]
        for i, text in enumerate(lines[:3]):
            ax, ay = anchors[i % len(anchors)]
            callout(svg, ax, ay, text, side="right",
                    text_x=a.x + a.w - 158, text_y=a.y + 34 + i * 30, max_chars=24)


def _mm(v):
    if abs(v - round(v)) < 1e-6:
        return str(int(round(v)))
    return f"{v:g}"
