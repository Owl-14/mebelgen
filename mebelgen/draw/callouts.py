"""Leader callouts: a small dot on a feature + a leader line + a text label."""
from __future__ import annotations

from .. import config


def callout(svg, target_x, target_y, text, side="right", text_x=None, text_y=None,
            color=config.COL_INK_SOFT, size=config.FS_CALLOUT, max_chars=22):
    """Draw a leader from (target_x,target_y) to a text label.

    side: 'right' or 'left' - which way the label sits. If text_x/text_y are
    given they override the computed label anchor position.
    """
    dot_r = 1.7
    if side == "right":
        elbow_x = target_x + 26
        tx = text_x if text_x is not None else elbow_x + 6
        anchor = "start"
    else:
        elbow_x = target_x - 26
        tx = text_x if text_x is not None else elbow_x - 6
        anchor = "end"
    ty = text_y if text_y is not None else target_y

    svg.circle(target_x, target_y, dot_r, fill=color, stroke="none")
    svg.polyline([(target_x, target_y), (elbow_x, target_y), (tx, ty)]
                 if abs(ty - target_y) > 2 else
                 [(target_x, target_y), (tx, ty)],
                 stroke=color, width=0.7)

    lines = _wrap(text, max_chars)
    for i, ln in enumerate(lines):
        svg.text(tx + (3 if anchor == "start" else -3),
                 ty + (i - (len(lines) - 1) / 2) * (size + 2) + size * 0.35,
                 ln, size=size, fill=config.COL_INK, anchor=anchor,
                 family=config.FONT_SANS)


def _wrap(text, max_chars):
    words = text.split()
    lines, cur = [], ""
    for w in words:
        if len(cur) + len(w) + 1 <= max_chars:
            cur = (cur + " " + w).strip()
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [text]
