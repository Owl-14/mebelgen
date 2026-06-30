"""Тест аллокатора меток (AKD-8): раскладка снимает пересечения."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from refsheets.draw.layout import Label, overlaps, stack  # noqa: E402


def test_resolves_overlaps():
    labels = [Label(target_y=300, height=22) for _ in range(6)]
    stack(labels, top=120, bottom=520, gap=4)
    assert overlaps(labels) == 0


def test_stays_in_band():
    labels = [Label(target_y=t, height=30) for t in (150, 160, 170, 400, 405, 410, 415)]
    stack(labels, 120, 520, gap=5)
    assert overlaps(labels) == 0
    for l in labels:
        assert 120 - 1 <= l.y - l.height / 2 and l.y + l.height / 2 <= 520 + 1


if __name__ == "__main__":
    test_resolves_overlaps()
    test_stays_in_band()
    print("OK: layout-аллокатор снимает пересечения и держит полосу")
