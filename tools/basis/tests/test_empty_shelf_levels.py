"""Полки по ТЗ: пустой shelf_levels и лишние секции у однокулонных изделий (#146)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.completeness_check import check_completeness         # noqa: E402
from src.generators.registry import generate_from_paramspec   # noqa: E402
from src.paramspec_normalize import normalize_candidate       # noqa: E402

# Так GLM описывает ТЗ «Тумба подкатная 400×450×580»: ящик и секция с полками.
CANDIDATE = {
    "schemaVersion": "paramspec-v1",
    "project_name": "Тумба подкатная",
    "archetype": "drawer_unit",
    "furniture_type": "drawer_unit",
    "dimensions": {"width": 400, "depth": 450, "height": 580, "tolerance": 5},
    "materials": {"board_thickness": 16, "board_material": "ЛДСП", "color": "U708 ST",
                  "edge_band_thickness": 2},
    "sections": [
        {"kind": "drawers", "drawers": 1, "shelf_levels": []},
        {"kind": "open", "shelves": 2, "shelf_levels": []},
    ],
}


def _shelves(project: dict) -> list[str]:
    return [panel["name"] for panel in project["panels"] if panel.get("type") == "shelf"]


def test_empty_shelf_levels_do_not_cancel_declared_shelves():
    """Пустой список уровней — «не задано», а не «полок нет»: движок и проверка сходятся."""
    spec = json.loads(json.dumps(CANDIDATE))
    spec["archetype"] = spec["furniture_type"] = "cabinet"

    project = generate_from_paramspec(spec)

    assert len(_shelves(project)) >= 2
    assert not [error for error in check_completeness(project, spec) if "полки" in error]


def test_explicit_levels_still_win():
    spec = json.loads(json.dumps(CANDIDATE))
    spec["archetype"] = spec["furniture_type"] = "cabinet"
    spec["sections"][1]["shelf_levels"] = [200]

    project = generate_from_paramspec(spec)

    assert [panel["placement"]["y1"] for panel in project["panels"]
            if panel.get("type") == "shelf"] == [200]


def test_pedestal_keeps_one_column_and_says_what_was_dropped():
    """Тумба строится одной колонкой: лишняя секция не теряется молча."""
    result = normalize_candidate(CANDIDATE)

    assert len(result.spec["sections"]) == 1
    assert any("одной колонкой" in note for note in result.notes)
    assert any("достроить в Studio" in warning for warning in result.spec["warnings"])

    project = generate_from_paramspec(result.spec)
    assert not [error for error in check_completeness(project, result.spec)
                if "полки" in error]
