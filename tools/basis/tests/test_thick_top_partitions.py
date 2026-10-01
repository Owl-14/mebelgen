"""Толстая столешница: перегородки не должны входить в крышку (#143)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.consistency_check import check_consistency          # noqa: E402
from src.generators.registry import generate_from_paramspec  # noqa: E402

# ТЗ «Тумба для документов 1100×450×750, столешница 50 мм, три ящика, двери накладные».
SPEC = {
    "schemaVersion": "paramspec-v1",
    "project_name": "Тумба для документов",
    "archetype": "cabinet",
    "furniture_type": "cabinet",
    "dimensions": {"width": 1100, "depth": 450, "height": 750, "tolerance": 5},
    "materials": {"board_thickness": 25, "board_material": "МДФ", "color": "RAL 8019",
                  "edge_band_thickness": 2, "top_thickness": 50},
    "top_thickness": 50,
    "sections": [{"kind": "drawers", "drawers": 3}, {"kind": "door", "door": 1, "shelves": 2}],
}


def _panel(project: dict, name: str) -> dict:
    return next(item for item in project["panels"] if item["name"].startswith(name))


def test_partition_stops_under_a_thick_top():
    project = generate_from_paramspec(SPEC)
    top = _panel(project, "Крышка")
    partition = _panel(project, "Перегородка")

    assert top["placement"]["y1"] == 700, "столешница 50 мм начинается на 700"
    assert partition["placement"]["y2"] == 700, "перегородка обязана остановиться под ней"
    assert not [issue for issue in check_consistency(project)
                if issue.code == "panel_overlap"]


def test_ordinary_top_thickness_is_unchanged():
    spec = json.loads(json.dumps(SPEC))
    spec.pop("top_thickness")
    spec["materials"].pop("top_thickness")

    project = generate_from_paramspec(spec)

    assert _panel(project, "Перегородка")["placement"]["y2"] == 725
    assert not [issue for issue in check_consistency(project)
                if issue.code == "panel_overlap"]
