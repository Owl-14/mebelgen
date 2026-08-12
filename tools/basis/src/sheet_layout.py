"""Раскладка меток/выносок без пересечений (AKD-8).

Аллокатор вертикальных слотов: метки тянутся к своим target_y, но раздвигаются,
чтобы не накладываться, и удерживаются в полосе [top, bottom]. Используется
актуальным чертежом листа согласования (techview).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Label:
    target_y: float
    height: float
    payload: Any = None
    y: float = field(default=0.0)        # назначенный центр после раскладки


def stack(labels: list[Label], top: float, bottom: float, gap: float = 4.0) -> list[Label]:
    """Назначить y каждой метке: близко к target_y, без перекрытий, в полосе.

    Жадно снизу-вверх по target_y; затем общий сдвиг вверх, если вышли за bottom.
    Порядок по target_y сохраняется → выноски не перекрещиваются.
    """
    if not labels:
        return labels
    order = sorted(labels, key=lambda l: l.target_y)
    cursor = top
    for lab in order:
        half = lab.height / 2
        y = max(lab.target_y, cursor + half)
        lab.y = y
        cursor = y + half + gap
    overshoot = (order[-1].y + order[-1].height / 2) - bottom
    if overshoot > 0:
        for lab in order:
            lab.y -= overshoot
        first_top = order[0].y - order[0].height / 2
        if first_top < top:
            for lab in order:
                lab.y += (top - first_top)
    return labels


def overlaps(labels: list[Label], gap: float = 0.0) -> int:
    """Сколько пар меток перекрываются по Y (самопроверка раскладки)."""
    ys = sorted(labels, key=lambda l: l.y)
    n = 0
    for a, b in zip(ys, ys[1:]):
        if (a.y + a.height / 2 + gap) > (b.y - b.height / 2):
            n += 1
    return n
