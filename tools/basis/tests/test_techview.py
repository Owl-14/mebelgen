"""Чертёж листа (AKD-7/AKD-8): раскладка без пересечений на ВСЕХ ParamSpec.

Критерии из задач: ни одна подпись/размерка не пересекается; подписи не выходят
за рамку листа; размерные цепочки присутствуют (габарит всегда, внутренние — при
наполнении); глубина не дублируется на фронте.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.generators import generate_from_paramspec        # noqa: E402
from src.sheet_layout import Label, overlaps, stack        # noqa: E402
from src.techview import build_techview_svg                # noqa: E402

PARAMSPECS = sorted(f for f in (ROOT / "paramspecs").glob("*.json")
                    if not f.name.endswith((".project.json", ".versions.json")))


def _projects():
    for f in PARAMSPECS:
        s = json.loads(f.read_text(encoding="utf-8"))
        if s.get("schemaVersion") != "paramspec-v1":
            continue
        yield f.name, generate_from_paramspec(s)


def test_layout_allocator_resolves_and_keeps_band():
    labels = [Label(target_y=300, height=22) for _ in range(6)]
    stack(labels, top=120, bottom=520, gap=4)
    assert overlaps(labels) == 0
    labels = [Label(target_y=t, height=30) for t in (150, 160, 170, 400, 405, 410, 415)]
    stack(labels, 120, 520, gap=5)
    assert overlaps(labels) == 0
    for l in labels:
        assert 119 <= l.y - l.height / 2 and l.y + l.height / 2 <= 521


def test_techview_clean_for_all_paramspecs():
    bad = {}
    for name, pr in _projects():
        svg, issues = build_techview_svg(pr)
        if issues:
            bad[name] = issues[:3]
        if not svg.startswith("<svg"):
            bad[name] = ["не SVG"]
    assert not bad, json.dumps(bad, ensure_ascii=False, indent=2)


def test_techview_dimensions_present_and_no_depth_dup():
    for name, pr in _projects():
        if "komi_46" not in name:
            continue
        svg, _ = build_techview_svg(pr)
        od = pr["overall_dimensions"]
        assert f">{od['width']}<" in svg          # габарит W на фронте
        assert f">{od['height']}<" in svg         # габарит H
        assert f">{od['depth']}<" in svg          # глубина на боку
        assert "ФРОНТ" in svg and "ВИД СБОКУ" in svg
        assert svg.count(f">{od['depth']}<") == 1  # глубина не дублируется
        assert "Полка" in svg                     # выноска по реальной детали
