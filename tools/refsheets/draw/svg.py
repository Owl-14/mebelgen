"""A tiny, dependency-free SVG canvas builder."""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple


def esc(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _fmt(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".")


class SVG:
    def __init__(self, width: float, height: float, background: Optional[str] = None):
        self.w = width
        self.h = height
        self._els: List[str] = []
        self._defs: List[str] = []
        if background:
            self.rect(0, 0, width, height, fill=background, stroke="none")

    # -- raw ---------------------------------------------------------------
    def add(self, raw: str):
        self._els.append(raw)
        return self

    def add_def(self, raw: str):
        self._defs.append(raw)
        return self

    # -- primitives --------------------------------------------------------
    def line(self, x1, y1, x2, y2, stroke="#000", width=1.0, dash=None, cap="butt", opacity=None):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        o = f' stroke-opacity="{opacity}"' if opacity is not None else ""
        self._els.append(
            f'<line x1="{_fmt(x1)}" y1="{_fmt(y1)}" x2="{_fmt(x2)}" y2="{_fmt(y2)}" '
            f'stroke="{stroke}" stroke-width="{_fmt(width)}" stroke-linecap="{cap}"{d}{o}/>'
        )
        return self

    def rect(self, x, y, w, h, fill="none", stroke="#000", width=1.0, rx=0, opacity=None):
        r = f' rx="{_fmt(rx)}" ry="{_fmt(rx)}"' if rx else ""
        o = f' opacity="{opacity}"' if opacity is not None else ""
        sw = f' stroke-width="{_fmt(width)}"' if stroke != "none" else ""
        self._els.append(
            f'<rect x="{_fmt(x)}" y="{_fmt(y)}" width="{_fmt(w)}" height="{_fmt(h)}"{r} '
            f'fill="{fill}" stroke="{stroke}"{sw}{o}/>'
        )
        return self

    def polygon(self, pts: Sequence[Tuple[float, float]], fill="none", stroke="#000", width=1.0, opacity=None):
        p = " ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in pts)
        o = f' opacity="{opacity}"' if opacity is not None else ""
        sw = f' stroke-width="{_fmt(width)}"' if stroke != "none" else ""
        self._els.append(f'<polygon points="{p}" fill="{fill}" stroke="{stroke}"{sw}{o}/>')
        return self

    def polyline(self, pts: Sequence[Tuple[float, float]], stroke="#000", width=1.0, fill="none", dash=None):
        p = " ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in pts)
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self._els.append(
            f'<polyline points="{p}" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="{_fmt(width)}"{d}/>'
        )
        return self

    def ellipse(self, cx, cy, rx, ry, fill="none", stroke="#000", width=1.0, opacity=None):
        o = f' opacity="{opacity}"' if opacity is not None else ""
        sw = f' stroke-width="{_fmt(width)}"' if stroke != "none" else ""
        self._els.append(
            f'<ellipse cx="{_fmt(cx)}" cy="{_fmt(cy)}" rx="{_fmt(rx)}" ry="{_fmt(ry)}" '
            f'fill="{fill}" stroke="{stroke}"{sw}{o}/>'
        )
        return self

    def circle(self, cx, cy, r, fill="none", stroke="#000", width=1.0):
        sw = f' stroke-width="{_fmt(width)}"' if stroke != "none" else ""
        self._els.append(
            f'<circle cx="{_fmt(cx)}" cy="{_fmt(cy)}" r="{_fmt(r)}" '
            f'fill="{fill}" stroke="{stroke}"{sw}/>'
        )
        return self

    def path(self, d: str, fill="none", stroke="#000", width=1.0, opacity=None):
        o = f' opacity="{opacity}"' if opacity is not None else ""
        sw = f' stroke-width="{_fmt(width)}"' if stroke != "none" else ""
        self._els.append(f'<path d="{d}" fill="{fill}" stroke="{stroke}"{sw}{o}/>')
        return self

    def text(self, x, y, s, size=12, fill="#000", anchor="start", weight="normal",
             style="normal", family="Arial, sans-serif", spacing=None, baseline=None):
        sp = f' letter-spacing="{spacing}"' if spacing is not None else ""
        bl = f' dominant-baseline="{baseline}"' if baseline else ""
        self._els.append(
            f'<text x="{_fmt(x)}" y="{_fmt(y)}" font-family="{family}" '
            f'font-size="{_fmt(size)}" fill="{fill}" text-anchor="{anchor}" '
            f'font-weight="{weight}" font-style="{style}"{sp}{bl}>{esc(s)}</text>'
        )
        return self

    def image(self, x, y, w, h, href, preserve="xMidYMid meet", opacity=None):
        o = f' opacity="{opacity}"' if opacity is not None else ""
        self._els.append(
            f'<image x="{_fmt(x)}" y="{_fmt(y)}" width="{_fmt(w)}" height="{_fmt(h)}" '
            f'preserveAspectRatio="{preserve}" href="{href}" xlink:href="{href}"{o}/>'
        )
        return self

    def group(self, transform: Optional[str] = None, opacity: Optional[float] = None):
        return _Group(self, transform, opacity)

    # -- output ------------------------------------------------------------
    def tostring(self) -> str:
        defs = ""
        if self._defs:
            defs = "<defs>\n" + "\n".join(self._defs) + "\n</defs>\n"
        body = "\n".join(self._els)
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'width="{_fmt(self.w)}" height="{_fmt(self.h)}" '
            f'viewBox="0 0 {_fmt(self.w)} {_fmt(self.h)}">\n{defs}{body}\n</svg>\n'
        )


class _Group:
    def __init__(self, svg: SVG, transform, opacity):
        self.svg = svg
        attrs = ""
        if transform:
            attrs += f' transform="{transform}"'
        if opacity is not None:
            attrs += f' opacity="{opacity}"'
        self.attrs = attrs

    def __enter__(self):
        self.svg._els.append(f"<g{self.attrs}>")
        return self.svg

    def __exit__(self, *a):
        self.svg._els.append("</g>")
        return False
