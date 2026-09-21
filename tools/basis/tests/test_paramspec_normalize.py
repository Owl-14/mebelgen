"""Нормализация кандидата ParamSpec: реальные промахи нейросетей на ТЗ (MEB-AI)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.paramspec import validate_paramspec                 # noqa: E402
from src.paramspec_normalize import normalize_candidate      # noqa: E402

BASE = json.loads((ROOT / "paramspecs" / "tz_shkaf_dokumenty.json").read_text(encoding="utf-8"))


def test_extra_fields_invented_by_llm_are_dropped():
    """GLM на чертеже шкафа придумал materials.facade_thickness и edge_band_material."""
    candidate = json.loads(json.dumps(BASE))
    candidate["materials"]["facade_thickness"] = 18
    candidate["materials"]["edge_band_material"] = "ПВХ"
    candidate["legs"]["furniture_encoded"] = True

    result = normalize_candidate(candidate)

    assert validate_paramspec(result.spec) == []
    assert "facade_thickness" not in result.spec["materials"]
    assert "edge_band_material" not in result.spec["materials"]
    assert "furniture_encoded" not in result.spec["legs"]
    assert len(result.notes) == 3 and all("не из контракта" in note for note in result.notes)


def test_section_kind_synonyms_are_mapped():
    """GigaChat на текстовом ТЗ вернул секцию kind=doors вместо door."""
    candidate = json.loads(json.dumps(BASE))
    candidate["sections"] = [{"kind": "doors", "door": 2, "shelves": 4}]

    result = normalize_candidate(candidate)

    assert result.spec["sections"][0]["kind"] == "door"
    assert validate_paramspec(result.spec) == []
    assert any("«doors» → door" in note for note in result.notes)


def test_dimensions_written_as_text_become_numbers():
    candidate = json.loads(json.dumps(BASE))
    candidate["dimensions"] = {"width": "1000 мм", "depth": "400", "height": "1800,0",
                               "tolerance": 5}

    result = normalize_candidate(candidate)

    assert result.spec["dimensions"] == {"width": 1000, "depth": 400, "height": 1800,
                                         "tolerance": 5}
    assert validate_paramspec(result.spec) == []


def test_russian_archetype_is_mapped():
    candidate = json.loads(json.dumps(BASE))
    candidate["archetype"] = "Шкаф"
    candidate["furniture_type"] = "Шкаф"

    result = normalize_candidate(candidate)

    assert result.spec["archetype"] == "cabinet" and result.spec["furniture_type"] == "cabinet"


def test_valid_spec_is_untouched():
    result = normalize_candidate(json.loads(json.dumps(BASE)))
    assert result.notes == [] and result.spec == BASE and not result.changed


def test_null_fields_are_dropped_so_contract_defaults_apply():
    """GLM на чертеже шкафа пишет «legs»: null и «facade_reveal»: null — движок падал."""
    from src.generators.registry import generate_from_paramspec

    candidate = json.loads(json.dumps(BASE))
    candidate["legs"] = None
    candidate["facade_reveal"] = None
    candidate["sections"] = [{"kind": "door", "id": "1", "door": 2,
                              "shelf_levels": [None, "300"]}]

    result = normalize_candidate(candidate)

    assert "legs" not in result.spec and "facade_reveal" not in result.spec
    assert result.spec["sections"][0]["shelf_levels"] == [300]
    assert validate_paramspec(result.spec) == []
    assert generate_from_paramspec(result.spec)["panels"]
    assert any("пустые поля убраны" in note for note in result.notes)


def test_shelves_declared_inside_a_drawer_column_are_dropped():
    """ТЗ «тумба подкатная»: модель приняла «внутренние полки 25 мм» за две полки."""
    from src.completeness_check import check_completeness
    from src.generators.registry import generate_from_paramspec

    candidate = json.loads(json.dumps(BASE))
    candidate["archetype"] = candidate["furniture_type"] = "cabinet"
    candidate["dimensions"] = {"width": 400, "depth": 450, "height": 580, "tolerance": 5}
    candidate["sections"] = [{"kind": "drawers", "drawers": 1, "shelves": 2}]

    result = normalize_candidate(candidate)

    assert "shelves" not in result.spec["sections"][0]
    assert any("в колонке ящиков" in note for note in result.notes)
    project = generate_from_paramspec(result.spec)
    assert not [error for error in check_completeness(project, result.spec)
                if "полки" in error]


def test_shelf_levels_in_a_drawer_column_are_kept():
    """Явные уровни полок — осознанное решение проектировщика, их не трогаем."""
    candidate = json.loads(json.dumps(BASE))
    candidate["sections"] = [{"kind": "drawers", "drawers": 2, "shelves": 1,
                              "shelf_levels": [900]}]

    result = normalize_candidate(candidate)

    assert result.spec["sections"][0]["shelf_levels"] == [900]
    assert result.spec["sections"][0]["shelves"] == 1


def test_tz_intake_keeps_unresolved_materials_as_a_warning():
    """Приёмка ТЗ: артикул из базы подбирает проектировщик, а не нейросеть."""
    from src.production_gate import evaluate_production_gate

    candidate = json.loads(json.dumps(BASE))
    candidate["materials"] = {**candidate["materials"], "board_article": "",
                              "facade_article": "", "color": "неизвестный декор",
                              "color_code": ""}

    strict = evaluate_production_gate(candidate)
    intake = evaluate_production_gate(candidate, unresolved_materials_are_errors=False)

    assert [i for i in strict.report.errors if i.code == "materials.unresolved"], \
        "строгий гейт обязан требовать позицию из производственной базы"
    assert not [i for i in intake.report.errors if i.code == "materials.unresolved"]
    assert [i for i in intake.report.warnings if i.code == "materials.unresolved"]


def test_garbage_input_is_returned_as_is():
    assert normalize_candidate("не спека").spec == "не спека"
    assert normalize_candidate(None).notes == []
