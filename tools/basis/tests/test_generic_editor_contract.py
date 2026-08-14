"""Regression matrix for semantic AI plans across the complete sample catalog."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.edit_operations import apply_edit_operations
from src.paramspec import validate_paramspec
from src.spec_chat import _normalize_provider_operations


ROOT = Path(__file__).resolve().parent.parent
CATALOG = tuple(sorted((ROOT / "paramspecs").glob("*.json")))


@pytest.mark.parametrize("spec_path", CATALOG, ids=lambda path: path.stem)
def test_server_compiles_dimension_plan_for_every_catalog_product(spec_path: Path) -> None:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    if validate_paramspec(spec):
        pytest.skip("legacy fixture is intentionally outside the strict ParamSpec contract")
    width = float(spec["dimensions"]["width"])
    operations = _normalize_provider_operations([{
        "op": "SetDimension", "dimension": "width", "value": width + 10,
        # A provider may still echo stale low-level fields. The compiler must
        # replace them from the selected product rather than trusting them.
        "target_id": "section:1",
        "preconditions": [{
            "kind": "target_missing", "target_id": "section:1",
        }],
    }], spec)

    result = apply_edit_operations(spec, operations)

    assert result["spec"]["dimensions"]["width"] == width + 10
    assert result["operations"][0]["target_id"] == "dimensions.width"
    assert result["operations"][0]["preconditions"] == [{
        "kind": "value_equals", "path": "dimensions.width", "value": width,
    }]


@pytest.mark.parametrize("spec_path", CATALOG, ids=lambda path: path.stem)
def test_whole_model_duplicate_is_not_tied_to_one_catalog_fixture(spec_path: Path) -> None:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    if validate_paramspec(spec):
        pytest.skip("legacy fixture is intentionally outside the strict ParamSpec contract")
    width = float(spec["dimensions"]["width"])
    operations = _normalize_provider_operations([{
        "op": "DuplicateModel", "direction": "right", "gap_mm": 25,
    }], spec)

    result = apply_edit_operations(spec, operations)

    assert result["spec"]["archetype"] == "composite"
    assert result["spec"]["dimensions"]["width"] == width * 2 + 25
    assert len(result["spec"]["blocks"]) >= 2
    assert result["operations"][0]["target_id"] == "model"
    assert result["operations"][0]["preconditions"] == [
        {"kind": "target_exists", "target_id": "model"},
    ]
