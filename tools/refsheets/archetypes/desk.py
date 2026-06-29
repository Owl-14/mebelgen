"""Desks: panel-leg work desk and a drawer pedestal desk (tumba)."""
from __future__ import annotations

from .. import config
from ..draw import dimensions as dim
from ..draw.callouts import callout
from ..geometry.iso import IsoView
from ..geometry.primitives import iso_box
from ..geometry.views import View2D
from .base import Archetype, Area


class DeskPanel(Archetype):
    def draw(self, svg, area: Area):
        s = self.spec
        W = s.dims.w or 1800
        D = s.dims.d or 800
        H = s.dims.h or 750
        f = s.features
        top_th = f.get("thickness", {}).get("worktop", 50)
        leg_w = 50
        screen_h = f.get("thickness", {}).get("screen", 300)
        overhang = 25
        brass = f.get("brass", True)

        top = Area(area.x, area.y, area.w, area.h * 0.46)
        bottom = Area(area.x, area.y + area.h * 0.50, area.w, area.h * 0.50)
        self.view_label(svg, top.x + top.w / 2, top.y + 6, "ВИД СПЕРЕДИ")

        self._front(svg, top, W, H, top_th, leg_w, screen_h, overhang, brass, f)
        if not self.draw_hero(svg, bottom):
            self.view_label(svg, bottom.x + bottom.w / 2, bottom.y + 2, "3D ИЗОМЕТРИЯ")
            self._iso(svg, bottom, W, D, H, top_th, leg_w, screen_h, brass, f)

    def _front(self, svg, a, W, H, top_th, leg_w, screen_h, overhang, brass, f):
        v = View2D(a.x, a.y + 16, a.w, a.h - 16, W + 2 * overhang, H, pad=40, align="bottom")

        def P(x, y):  # x measured from left of worktop incl. overhang
            return v.px(x, y)

        # worktop
        wx0, wy0 = P(0, H - top_th)
        wx1, wy1 = P(W + 2 * overhang, H)
        svg.rect(wx0, wy1, wx1 - wx0, wy0 - wy1, fill=self.pal.front,
                 stroke=self.pal.line, width=1.2)
        # legs
        for lx in (overhang, overhang + W - leg_w):
            lx0, ly0 = P(lx, 0)
            lx1, ly1 = P(lx + leg_w, H - top_th)
            svg.rect(min(lx0, lx1), ly1, abs(lx1 - lx0), ly0 - ly1,
                     fill=self.pal.side, stroke=self.pal.line, width=1.1)
            if brass:  # brass vertical insert
                bx, _ = P(lx + leg_w / 2, 0)
                svg.line(bx, ly0 - 4, bx, ly1 + 4, stroke=config.COL_BRASS, width=2.2)
        # front screen between legs
        sx0, sy0 = P(overhang + leg_w, H - top_th - screen_h)
        sx1, sy1 = P(overhang + W - leg_w, H - top_th)
        svg.rect(min(sx0, sx1), sy1, abs(sx1 - sx0), sy0 - sy1,
                 fill=self.pal.top, stroke=self.pal.line, width=1.0)
        # cable cutout hint
        cx, cy = P(W / 2 + overhang, H - top_th - 6)
        svg.rect(cx - 26, cy - 4, 52, 6, fill=config.COL_BLACK, stroke="none")

        # dims
        dim.dim_h(svg, wx0, wx1, wy1, wy1 - 22, _mm(W))
        dim.dim_v(svg, wy1, P(0, 0)[1], wx0, wx0 - 22, _mm(H))
        dim.dim_v(svg, sy0, sy1, sx0, sx0 - 14, _mm(screen_h), text_side="left")
        # top thickness
        dim.dim_v(svg, wy1, wy0, wx1, wx1 + 16, _mm(top_th), text_side="right")
        # отрыв столешницы от опоры 25 мм (справа)
        leg_out_x = P(overhang + W, 0)[0]
        dim.dim_h(svg, leg_out_x, wx1, wy0, wy0 + 12, _mm(overhang), ext=False)

        # дополнительные элементы (за корпусом -> показаны пунктиром + подпись)
        floor_y = P(0, 0)[1]
        if f.get("pc_holder"):
            # у правой опоры, под столешницей
            hx = P(overhang + W - leg_w - 60, 0)[0]
            top_u = wy0 + 4
            svg.polyline([(hx - 9, top_u), (hx + 9, top_u), (hx + 9, top_u + 30),
                          (hx - 9, top_u + 30), (hx - 9, top_u)],
                         stroke=config.COL_BLACK, width=1.0, dash="3 2")
            svg.text(hx + 14, top_u + 16, "подвес сист. блока", size=8.5,
                     fill=config.COL_INK_SOFT, family=config.FONT_SANS)
        if f.get("cable_channel"):
            ccx = P(W * 0.5 + overhang, 0)[0]
            svg.line(ccx, wy0 + 3, ccx, floor_y, stroke=config.COL_BLACK,
                     width=1.4, dash="4 3")
            svg.text(ccx + 6, (wy0 + floor_y) / 2, "гибкий кабель-канал",
                     size=8.5, fill=config.COL_INK_SOFT, family=config.FONT_SANS)

    def _iso(self, svg, a, W, D, H, top_th, leg_w, screen_h, brass, f):
        iso = IsoView.fit(W, D, H, (a.x, a.y + 12, a.w, a.h - 12), pad=46)
        # legs
        for lx in (0, W - leg_w):
            iso_box(svg, iso, lx, 0, 0, leg_w, D, H - top_th)
        # screen between legs (set back a bit)
        iso_box(svg, iso, leg_w, D - 40, H - top_th - screen_h, W - 2 * leg_w, 25, screen_h,
                top=self.pal.top, front=self.pal.top, side=self.pal.front)
        # worktop on top with overhang
        oh = 25
        iso_box(svg, iso, -oh, -oh, H - top_th, W + 2 * oh, D + 2 * oh, top_th)
        # brass tips on legs (front face y=D)
        if brass:
            for lx in (0, W - leg_w):
                bx0, by0 = iso.p(lx + leg_w / 2, D, 0)
                bx1, by1 = iso.p(lx + leg_w / 2, D, H - top_th)
                svg.line(bx0, by0, bx1, by1, stroke=config.COL_BRASS, width=2.2)

        # callouts
        lines = [self.color_text(), "Столешница 50 мм, опоры 50 мм"]
        if f.get("screen_front"):
            lines.append("Передний экран, h=300 мм")
        if brass:
            lines.append("Вставки — латунь")
        if f.get("pc_holder"):
            lines.append("Подвес для системного блока")
        if f.get("cable_channel"):
            lines.append("Кабель-канал в цвет + гибкий чёрный")
        anchors = [iso.p(W / 2, D, H), iso.p(W, D / 2, (H - top_th) / 2),
                   iso.p(W / 2, D, H - top_th), iso.p(W - leg_w / 2, D, H * 0.3),
                   iso.p(W * 0.7, D, H * 0.45), iso.p(W * 0.5, D, H * 0.55)]
        for i, text in enumerate(lines[:6]):
            ax, ay = anchors[i % len(anchors)]
            callout(svg, ax, ay, text, side="right",
                    text_x=a.x + a.w - 168, text_y=a.y + 26 + i * 28, max_chars=26)


class DrawerUnit(Archetype):
    def draw(self, svg, area: Area):
        s = self.spec
        W = s.dims.w or 1100
        D = s.dims.d or 450
        H = s.dims.h or 750
        f = s.features
        top_th = f.get("thickness", {}).get("worktop", 50)
        plinth_h = 25
        drawers = f.get("drawers", 3)
        ped_w = W * 0.45  # drawer pedestal width on the right

        top = Area(area.x, area.y, area.w, area.h * 0.50)
        bottom = Area(area.x, area.y + area.h * 0.52, area.w, area.h * 0.48)
        self.view_label(svg, top.x + top.w / 2, top.y + 6, "ВИД СПЕРЕДИ")

        self._front(svg, top, W, H, top_th, plinth_h, drawers, ped_w, f)
        if not self.draw_hero(svg, bottom):
            self.view_label(svg, bottom.x + bottom.w / 2, bottom.y + 2, "3D ИЗОМЕТРИЯ")
            self._iso(svg, bottom, W, D, H, top_th, plinth_h, drawers, ped_w, f)

    def _front(self, svg, a, W, H, top_th, plinth_h, drawers, ped_w, f):
        oh = 20
        v = View2D(a.x, a.y + 16, a.w, a.h - 16, W + 2 * oh, H, pad=40, align="bottom")

        def P(x, y):
            return v.px(x, y)

        # worktop
        wx0, wy0 = P(0, H - top_th)
        wx1, wy1 = P(W + 2 * oh, H)
        svg.rect(wx0, wy1, wx1 - wx0, wy0 - wy1, fill=self.pal.front,
                 stroke=self.pal.line, width=1.2)
        # left leg/side panel
        leg_w = 25
        lx0, ly0 = P(oh, 0)
        lx1, ly1 = P(oh + leg_w, H - top_th)
        svg.rect(min(lx0, lx1), ly1, abs(lx1 - lx0), ly0 - ly1,
                 fill=self.pal.side, stroke=self.pal.line, width=1.1)
        # right drawer pedestal
        px0 = W + oh - ped_w
        pedestal_x0, pedestal_y0 = P(px0, plinth_h)
        pedestal_x1, pedestal_y1 = P(W + oh, H - top_th)
        svg.rect(pedestal_x0, pedestal_y1, pedestal_x1 - pedestal_x0,
                 pedestal_y0 - pedestal_y1, fill=self.pal.front,
                 stroke=self.pal.line, width=1.2)
        # drawers
        avail = (H - top_th) - plinth_h
        dh = avail / drawers
        for i in range(drawers):
            dy = plinth_h + i * dh
            dx0, dyy0 = P(px0, dy)
            dx1, dyy1 = P(W + oh, dy + dh)
            svg.rect(dx0, dyy1, dx1 - dx0, dyy0 - dyy1, fill=self.pal.front,
                     stroke=self.pal.line, width=1.0)
            # central lock on middle drawer
            if f.get("lock") and i == drawers // 2:
                kx, ky = P((px0 + W + oh) / 2, dy + dh / 2)
                svg.circle(kx, ky, 3.0, fill="none", stroke=config.COL_BLACK, width=1.0)
            # drawer height dim on right
            dim.dim_v(svg, dyy1, dyy0, dx1, pedestal_x1 + 16, _mm(dh),
                      text_side="right", ext=(i == 0))
        # plinth band under pedestal
        plx0, ply0 = P(px0, 0)
        _, ply1 = P(px0, plinth_h)
        svg.rect(plx0, ply1, pedestal_x1 - plx0, ply0 - ply1,
                 fill=self.pal.side, stroke=self.pal.line, width=1.0)

        # dims
        dim.dim_h(svg, wx0, wx1, wy1, wy1 - 22, _mm(W))
        mid, _ = P(W / 2 + oh, H)
        dim.dim_h(svg, wx0, mid, wy1, wy1 - 6, _mm(W / 2), ext=False)
        dim.dim_h(svg, mid, wx1, wy1, wy1 - 6, _mm(W / 2), ext=False)
        dim.dim_v(svg, wy1, P(0, 0)[1], wx0, wx0 - 22, _mm(H))

    def _iso(self, svg, a, W, D, H, top_th, plinth_h, drawers, ped_w, f):
        iso = IsoView.fit(W, D, H, (a.x, a.y + 12, a.w, a.h - 12), pad=46)
        leg_w = 25
        # left side panel
        iso_box(svg, iso, 0, 0, 0, leg_w, D, H - top_th)
        # right pedestal
        px0 = W - ped_w
        iso_box(svg, iso, px0, 0, plinth_h, ped_w, D, H - top_th - plinth_h)
        # drawer lines on front of pedestal (front face y=D)
        avail = (H - top_th) - plinth_h
        dh = avail / drawers
        for i in range(1, drawers):
            yz = plinth_h + i * dh
            x0, y0 = iso.p(px0, D, yz)
            x1, y1 = iso.p(W, D, yz)
            svg.line(x0, y0, x1, y1, stroke=self.pal.line, width=0.9)
        # worktop
        oh = 20
        iso_box(svg, iso, -oh, -oh, H - top_th, W + 2 * oh, D + 2 * oh, top_th)

        lines = [self.color_text(), "Столешница 50 мм, бок 25 мм",
                 f"Ящики: {drawers} шт., накладные, push"]
        if f.get("lock"):
            lines.append("Замок центральный")
        if f.get("plinth"):
            lines.append("Цоколь 25 мм, в цвет корпуса")
        anchors = [iso.p(W / 2, D, H), iso.p(W, D / 2, H * 0.55),
                   iso.p(W - ped_w / 2, D, H * 0.5), iso.p(W * 0.85, D, H * 0.4),
                   iso.p(W * 0.8, D, plinth_h)]
        for i, text in enumerate(lines[:5]):
            ax, ay = anchors[i % len(anchors)]
            callout(svg, ax, ay, text, side="right",
                    text_x=a.x + a.w - 168, text_y=a.y + 26 + i * 28, max_chars=26)


def _mm(v):
    if abs(v - round(v)) < 1e-6:
        return str(int(round(v)))
    return f"{v:g}"
