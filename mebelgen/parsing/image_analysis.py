"""Lightweight shape analysis of a product reference photo.

Detects two cues that the textual spec often omits:
  * fluted / reeded surface ("гофра") -> vertical periodic stripes
  * round silhouette (cylinder / round top) -> curved top boundary

Uses Pillow only. Heuristic but robust enough to disambiguate e.g. a
fluted round coffee table from a plain box.
"""
from __future__ import annotations

from typing import Optional


def analyze_image(path: str) -> dict:
    res = {"fluted": False, "round": False, "ok": False}
    try:
        from PIL import Image
    except Exception:
        return res
    try:
        im = Image.open(path).convert("RGB")
    except Exception:
        return res

    # downscale for speed
    W0, H0 = im.size
    scale = 240.0 / max(W0, H0)
    if scale < 1:
        im = im.resize((max(1, int(W0 * scale)), max(1, int(H0 * scale))))
    W, H = im.size
    px = im.load()

    # background colour estimated from the four corners
    corners = [px[0, 0], px[W - 1, 0], px[0, H - 1], px[W - 1, H - 1]]
    bg = tuple(sum(c[i] for c in corners) // 4 for i in range(3))

    def dist(c):
        return abs(c[0] - bg[0]) + abs(c[1] - bg[1]) + abs(c[2] - bg[2])

    thr = 38  # foreground threshold

    # object mask + per-column top boundary
    cols_top = [None] * W
    cols_bottom = [None] * W
    any_fg = False
    for x in range(W):
        for y in range(H):
            if dist(px[x, y]) > thr:
                if cols_top[x] is None:
                    cols_top[x] = y
                cols_bottom[x] = y
                any_fg = True
    if not any_fg:
        return res
    xs = [x for x in range(W) if cols_top[x] is not None]
    x0, x1 = min(xs), max(xs)
    ys_top = [cols_top[x] for x in xs]
    y_top_min = min(ys_top)
    y_bottom_max = max(cols_bottom[x] for x in xs if cols_bottom[x] is not None)
    obj_w = x1 - x0 + 1
    obj_h = y_bottom_max - y_top_min + 1
    res["ok"] = True

    # ---- round-top detection -------------------------------------------
    # sample the top boundary at center vs sides
    def top_at(frac):
        x = int(x0 + frac * obj_w)
        x = min(max(x, x0), x1)
        return cols_top[x] if cols_top[x] is not None else y_bottom_max

    center = (top_at(0.45) + top_at(0.5) + top_at(0.55)) / 3.0
    sides = (top_at(0.12) + top_at(0.88)) / 2.0
    # round top: center boundary noticeably higher (smaller y) than sides
    if (sides - center) > obj_h * 0.06:
        res["round"] = True

    # ---- fluted detection ----------------------------------------------
    # scan several horizontal lines across the body, count vertical stripes
    peak_counts = []
    for frac in (0.45, 0.6, 0.72):
        yy = int(y_top_min + frac * obj_h)
        yy = min(max(yy, 0), H - 1)
        row = []
        for x in range(x0, x1 + 1):
            c = px[x, yy]
            row.append((c[0] + c[1] + c[2]) / 3.0)
        if len(row) < 12:
            continue
        # normalize and count local maxima with prominence
        mn, mx = min(row), max(row)
        if mx - mn < 12:
            continue
        norm = [(v - mn) / (mx - mn) for v in row]
        peaks = 0
        for i in range(2, len(norm) - 2):
            if (norm[i] > norm[i - 1] and norm[i] >= norm[i + 1]
                    and norm[i] - min(norm[i - 2], norm[i + 2]) > 0.10):
                peaks += 1
        peak_counts.append(peaks)
    if peak_counts and (sum(peak_counts) / len(peak_counts)) >= 7:
        res["fluted"] = True
        res["round"] = True  # reeded furniture here is cylindrical

    return res
