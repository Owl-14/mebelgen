"""Case goods: wardrobes and cabinets with doors, shelves, rod, drawers."""
from __future__ import annotations

from .. import config
from ..draw import dimensions as dim
from ..draw.callouts import callout
from ..geometry.iso import IsoView
from ..geometry.primitives import iso_box, iso_brass_frame
from ..geometry.views import View2D
from .base import Archetype, Area


class CaseGood(Archetype):
    def draw(self, svg, area: Area):
        s = self.spec
        W = s.dims.w or 800
        D = s.dims.d or 400
        H = s.dims.h or 900
        f = s.features
        plinth_h = (f.get("thickness", {}).get("plinth")) or (80 if H >= 1500 else 60)
        doors = 2 if (W >= 600 or f.get("symmetric")) else 1
        brass = f.get("brass", False)
        lock = f.get("lock", False)
        handles = f.get("handles_profile", False)

        tall = H >= 1500 or H >= max(W, D) * 1.25

        if tall:
            # left column: front (top) + interior (bottom); right column: big ISO
            left = Area(area.x, area.y, area.w * 0.42, area.h)
            right = Area(area.x + area.w * 0.46, area.y, area.w * 0.54, area.h)
            front_a = Area(left.x, left.y, left.w, left.h * 0.50)
            inter_a = Area(left.x, left.y + left.h * 0.50, left.w, left.h * 0.50)
            self.view_label(svg, front_a.x + front_a.w / 2, front_a.y + 6, "ВИД СПЕРЕДИ")
            self.view_label(svg, inter_a.x + inter_a.w / 2, inter_a.y + 6,
                            "ВНУТРЕННЕЕ НАПОЛНЕНИЕ")
            self._front(svg, front_a, W, H, doors, plinth_h, brass, lock, handles, f)
            self._interior(svg, inter_a, W, D, H, plinth_h, doors, f)
            if not self.draw_hero(svg, right):
                self.view_label(svg, right.x + right.w / 2, right.y + 6, "3D ИЗОМЕТРИЯ")
                self._iso(svg, right, W, D, H, plinth_h, doors, brass, lock, f, callouts=False)
        else:
            top = Area(area.x, area.y, area.w, area.h * 0.50)
            bottom = Area(area.x, area.y + area.h * 0.52, area.w, area.h * 0.48)
            front_a = Area(top.x, top.y, top.w * 0.46, top.h)
            inter_a = Area(top.x + top.w * 0.52, top.y, top.w * 0.46, top.h)
            self.view_label(svg, front_a.x + front_a.w / 2, top.y + 6, "ВИД СПЕРЕДИ")
            self.view_label(svg, inter_a.x + inter_a.w / 2, top.y + 6, "ВНУТРЕННЕЕ НАПОЛНЕНИЕ")
            self._front(svg, front_a, W, H, doors, plinth_h, brass, lock, handles, f)
            self._interior(svg, inter_a, W, D, H, plinth_h, doors, f)
            if not self.draw_hero(svg, bottom):
                self.view_label(svg, bottom.x + bottom.w / 2, bottom.y + 2, "3D ИЗОМЕТРИЯ")
                self._iso(svg, bottom, W, D, H, plinth_h, doors, brass, lock, f, callouts=True)

    # -----------------------------------------------------------------
    def _front(self, svg, a, W, H, doors, plinth_h, brass, lock, handles, f):
        v = View2D(a.x, a.y + 16, a.w, a.h - 16, W, H, pad=36, align="bottom")
        P = v.px

        # plinth
        x0, yb = P(0, 0)
        x1, yp = P(W, plinth_h)
        svg.rect(min(x0, x1), yp, abs(x1 - x0), yb - yp,
                 fill=config.COL_DARK if f.get("plinth_black") else self.pal.side,
                 stroke=self.pal.line, width=1.0)
        # body
        bx0, by0 = P(0, plinth_h)
        bx1, by1 = P(W, H)
        svg.rect(bx0, by1, bx1 - bx0, by0 - by1, fill=self.pal.front,
                 stroke=self.pal.line, width=1.2)

        dw = W / doors
        for i in range(doors):
            dx0, _ = P(i * dw, plinth_h)
            dx1, _ = P((i + 1) * dw, plinth_h)
            if i > 0:
                svg.line(dx0, by1, dx0, by0, stroke=self.pal.line, width=1.0)
            if brass:
                inset = v.s(26)
                svg.rect(dx0 + inset, by1 + inset, (dx1 - dx0) - 2 * inset,
                         (by0 - by1) - 2 * inset, fill="none",
                         stroke=config.COL_BRASS, width=2.0)
            if handles:
                hx = dx1 - v.s(22) if i == 0 else dx0 + v.s(22)
                svg.line(hx, by1 + v.s(70), hx, by0 - v.s(70),
                         stroke=config.COL_BLACK, width=2.6)
        if lock:
            kx, ky = P(W - dw / 2, H * 0.5)
            svg.circle(kx, ky, 3.0, fill="none", stroke=config.COL_BLACK, width=1.0)
            svg.line(kx, ky, kx, ky + 5, stroke=config.COL_BLACK, width=1.0)

        # dims
        ytop = by1 - 24
        dim.dim_h(svg, bx0, bx1, by1, ytop, _mm(W))
        if doors > 1:
            yt2 = by1 - 8
            mid, _ = P(W / 2, H)
            dim.dim_h(svg, bx0, mid, by1, yt2, _mm(W / doors), ext=False)
            dim.dim_h(svg, mid, bx1, by1, yt2, _mm(W / doors), ext=False)
        dim.dim_v(svg, by1, yb, bx0, bx0 - 24, _mm(H))
        dim.dim_v(svg, yp, yb, bx1, bx1 + 16, _mm(plinth_h), text_side="right")

    # -----------------------------------------------------------------
    def _interior(self, svg, a, W, D, H, plinth_h, doors, f):
        v = View2D(a.x, a.y + 16, a.w, a.h - 16, W, H, pad=36, align="bottom")
        P = v.px

        bx0, by0 = P(0, plinth_h)
        bx1, by1 = P(W, H)
        svg.rect(bx0, by1, bx1 - bx0, by0 - by1, fill=self.pal.top,
                 stroke=self.pal.line, width=1.2)
        # plinth
        _, pyb = P(0, 0)
        svg.rect(bx0, by0, bx1 - bx0, pyb - by0,
                 fill=config.COL_DARK if f.get("plinth_black") else self.pal.side,
                 stroke=self.pal.line, width=1.0)

        two_sections = bool(f.get("symmetric") and f.get("adjustable_shelves") and not f.get("rod"))
        if two_sections:
            mx, _ = P(W / 2, plinth_h)
            svg.line(mx, by1, mx, by0, stroke=self.pal.line, width=1.1)

        inner_b, inner_t = plinth_h, H
        sec_w = W / (2 if two_sections else 1)

        if f.get("rod"):
            hat_y = inner_t - 300
            self._hline(svg, v, 0, W, hat_y)
            rod_y = hat_y - 70
            rx0, ry = P(W * 0.14, rod_y)
            rx1, _ = P(W * 0.86, rod_y)
            svg.line(rx0, ry, rx1, ry, stroke=config.COL_DARK, width=2.4)
            svg.line(rx0, ry - 5, rx0, ry + 5, stroke=config.COL_DARK, width=1.5)
            svg.line(rx1, ry - 5, rx1, ry + 5, stroke=config.COL_DARK, width=1.5)
            shoe_y = inner_b + 330
            self._hline(svg, v, 0, W, shoe_y)
            self._tag(svg, P(W * 0.5, hat_y), bx1, "полка для шапок")
            self._tag(svg, ((rx0 + rx1) / 2, ry), bx1, "штанга")
            self._tag(svg, P(W * 0.5, shoe_y), bx1, "полка для обуви")
        else:
            n = f.get("shelves") or (4 if H >= 1500 else 1)
            for sx in range(2 if two_sections else 1):
                ox = sx * sec_w
                gap = (inner_t - inner_b) / (n + 1)
                for i in range(1, n + 1):
                    self._hline(svg, v, ox, ox + sec_w, inner_b + gap * i)
            if n >= 1:
                gap = (inner_t - inner_b) / (n + 1)
                dim.dim_v(svg, P(0, inner_b + gap)[1], P(0, inner_b + 2 * gap)[1],
                          bx1, bx1 + 16, "~" + _mm(gap), text_side="right")

    def _hline(self, svg, v, x_from, x_to, y):
        x0, yy = v.px(x_from, y)
        x1, _ = v.px(x_to, y)
        ins = (x1 - x0) * 0.04
        svg.line(x0 + ins, yy, x1 - ins, yy, stroke=self.pal.line, width=1.0)

    def _tag(self, svg, pt, x_right, text):
        x, y = pt
        svg.circle(x, y, 1.6, fill=config.COL_INK_SOFT, stroke="none")
        svg.text(x, y - 4, text, size=9.5, fill=config.COL_INK_SOFT,
                 anchor="middle", family=config.FONT_SANS)

    # -----------------------------------------------------------------
    def _iso(self, svg, a, W, D, H, plinth_h, doors, brass, lock, f, callouts=True):
        iso = IsoView.fit(W, D, H, (a.x, a.y + 14, a.w, a.h - 14), pad=44)
        # plinth flush with body footprint (front/side faces only -> no top slab)
        iso_box(svg, iso, 0, 0, 0, W, D, plinth_h,
                front=config.COL_DARK if f.get("plinth_black") else self.pal.side,
                side="#1f1f1f" if f.get("plinth_black") else self.pal.side,
                line=config.COL_DARK, faces=("side", "front"))
        # body
        iso_box(svg, iso, 0, 0, plinth_h, W, D, H - plinth_h)
        # front face is the y=D plane in this projection
        if doors > 1:
            sx0, sy0 = iso.p(W / 2, D, plinth_h)
            sx1, sy1 = iso.p(W / 2, D, H)
            svg.line(sx0, sy0, sx1, sy1, stroke=self.pal.line, width=1.0)
        if brass:
            iso_brass_frame(svg, iso, 0, D, plinth_h, W, H - plinth_h, inset=W * 0.04)
        if lock:
            lx, ly = iso.p(W * 0.72, D, (H + plinth_h) / 2)
            svg.circle(lx, ly, 2.0, fill="none", stroke=config.COL_BLACK, width=1.0)

        if not callouts:
            return
        rx_text = a.x + a.w - 6
        items = [self.color_text()]
        if brass:
            items.append("Латунный профиль по периметру")
        if f.get("push_open"):
            items.append("Push-to-open, без ручек")
        if lock:
            items.append("Замок на правой двери, чёрный")
        items.append(("Цоколь " + ("чёрный" if f.get("plinth_black") else "в цвет корпуса")
                      + f", {int(plinth_h)} мм"))
        anchors = [iso.p(W, D * 0.5, H * 0.85), iso.p(W, D * 0.5, H * 0.62),
                   iso.p(W, D * 0.45, H * 0.45), iso.p(W * 0.72, D, (H + plinth_h) / 2),
                   iso.p(W, D * 0.5, plinth_h * 0.5)]
        for i, text in enumerate(items[:5]):
            ax, ay = anchors[i % len(anchors)]
            callout(svg, ax, ay, text, side="right",
                    text_x=a.x + a.w - 150, text_y=a.y + 34 + i * 30, max_chars=24)


def _mm(v):
    if abs(v - round(v)) < 1e-6:
        return str(int(round(v)))
    return f"{v:g}"
