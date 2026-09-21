"""Пустые списки/объекты в ParamSpec не должны ронять генерацию (ТЗ от нейросети)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.generators.registry import generate_from_paramspec   # noqa: E402
from src.materials_policy import apply_material_policy        # noqa: E402

# Так выглядит ответ GLM на реальный чертёж «Шкаф для документов 1000×400×1800».
CANDIDATE = {
    "schemaVersion": "paramspec-v1",
    "project_name": "Шкаф для документов",
    "archetype": "cabinet",
    "furniture_type": "cabinet",
    "dimensions": {"width": 1000, "depth": 400, "height": 1800, "tolerance": 5},
    "materials": {"board_thickness": 16, "edge_band_thickness": 0.4,
                  "board_material": "ДСП", "color": "черный"},
    "sections": [{"kind": "door", "id": "section_1", "door": 2, "shelves": 4}],
    "warnings": None,
    "hardware": None,
    "estimated_values": None,
}


def test_null_warnings_and_hardware_do_not_crash_the_policy():
    result = apply_material_policy(CANDIDATE)
    assert isinstance(result["warnings"], list) and isinstance(result["hardware"], dict)
    assert result["materials"]["back_material"] == "ДВП"


def test_null_materials_are_replaced_by_defaults():
    result = apply_material_policy({**CANDIDATE, "materials": None})
    assert result["materials"]["board_material"] == "ЛДСП"


def test_generation_survives_the_candidate():
    project = generate_from_paramspec(CANDIDATE)
    assert project["panels"], "изделие должно собраться, а не упасть на служебных полях"
