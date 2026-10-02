"""Ящики в несколько колонок: «комод, 3 ящика слева и 3 справа».

Случай со studio.akeda.ru 29.09: комод 1600 из двух модулей по 3 ящика.
Нейросеть выдала drawer_unit с двумя секциями (генератор брал только первую),
а cabinet с двумя колонками ящиков давал одинаковые имена «Фасад ящик 1…» в
обеих колонках — консистентность блокировала экспорт.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ["SPEC_CHAT_PROVIDER"] = "mock"   # тесты всегда офлайн

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest                                                # noqa: E402

from src.production_gate import evaluate_production_gate     # noqa: E402


def _komod(archetype: str, sections: list[dict]) -> dict:
    return {
        "schemaVersion": "paramspec-v1",
        "project_name": "Комод в спальню",
        "archetype": archetype,
        "dimensions": {"width": 1600, "depth": 400, "height": 800},
        "materials": {"board_thickness": 16},
        "sections": sections,
    }


TWO_BY_THREE = [{"kind": "drawers", "drawers": 3}, {"kind": "drawers", "drawers": 3}]


def _fronts(project: dict) -> list[dict]:
    return [p for p in project["panels"] if p.get("type") == "drawer_front"]


@pytest.mark.parametrize("archetype", ["cabinet", "drawer_unit"])
def test_two_drawer_columns_pass_the_gate(archetype):
    decision = evaluate_production_gate(_komod(archetype, TWO_BY_THREE))
    assert decision.report.ok, [issue.detail for issue in decision.report.errors]
    fronts = _fronts(decision.project)
    assert len(fronts) == 6
    # по три фасада в каждой половине, колонки разделены перегородкой
    left = [p for p in fronts if p["placement"]["x2"] <= 800]
    right = [p for p in fronts if p["placement"]["x1"] >= 800]
    assert len(left) == 3 and len(right) == 3
    assert any(p.get("type") == "vertical_partition" for p in decision.project["panels"])


def test_drawer_columns_get_unique_names():
    decision = evaluate_production_gate(_komod("cabinet", [
        {"kind": "drawers", "drawers": 3},
        {"kind": "door", "door": 1, "shelves": 2},
        {"kind": "drawers", "drawers": 4},
    ]))
    names = [p["name"] for p in decision.project["panels"]]
    assert len(names) == len(set(names))
    assert "Секция 1 Фасад ящик 1" in names and "Секция 3 Фасад ящик 4" in names


def test_single_drawer_column_keeps_plain_names():
    decision = evaluate_production_gate(_komod("cabinet", [
        {"kind": "drawers", "drawers": 3}, {"kind": "shelves", "shelves": 2},
    ]))
    names = {p["name"] for p in decision.project["panels"]}
    assert "Фасад ящик 1" in names


def test_explicit_prefix_wins():
    sections = [{**TWO_BY_THREE[0], "prefix": "Левый "}, {**TWO_BY_THREE[1], "prefix": "Правый "}]
    names = {p["name"] for p in evaluate_production_gate(_komod("cabinet", sections)).project["panels"]}
    assert {"Левый Фасад ящик 1", "Правый Фасад ящик 1"} <= names


def test_drawer_unit_with_several_sections_is_built_as_cabinet_with_warning():
    project = evaluate_production_gate(_komod("drawer_unit", TWO_BY_THREE)).project
    assert any("собрано как cabinet" in w for w in project.get("warnings") or [])


def test_single_section_drawer_unit_is_untouched():
    project = evaluate_production_gate(_komod("drawer_unit", [{"kind": "drawers", "drawers": 6}])).project
    assert not any("собрано как cabinet" in w for w in project.get("warnings") or [])
    assert len(_fronts(project)) == 6
    assert "Фасад ящик 1" in {p["name"] for p in project["panels"]}


@pytest.mark.parametrize("archetype,sections", [
    ("drawer_unit", [{"kind": "drawers", "drawers": 3, "drawer_heights": [193, 193, 193]}]),
    ("cabinet", [{"kind": "drawers", "drawers": 3, "drawer_heights": [193, 193, 193]},
                 {"kind": "drawers", "drawers": 3, "drawer_heights": [193, 193, 193]}]),
])
def test_drawer_heights_from_the_model_are_fitted_into_the_carcass(archetype, sections):
    """ТЗ 72 из PDF: модель поделила 580 на 3 (193) без зазоров — фасад вылезал на 4 мм."""
    spec = {**_komod(archetype, sections), "dimensions": {"width": 800, "depth": 450, "height": 580}}
    decision = evaluate_production_gate(spec)
    assert decision.report.ok, [issue.detail for issue in decision.report.errors]
    assert max(p["placement"]["y2"] for p in _fronts(decision.project)) <= 580
    assert any("уменьшены пропорционально" in w for w in decision.project.get("warnings") or [])
