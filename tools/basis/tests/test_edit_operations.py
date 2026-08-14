"""MEB-143: typed AI operations, atomic reducer and no collateral edits."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.edit_operations import (
    EditApplicationError,
    apply_edit_operations,
    edit_operation_json_schema,
    parse_edit_operations,
)


ROOT = Path(__file__).resolve().parent.parent
SPEC = json.loads((ROOT / "paramspecs" / "stol_ofisny_foto.json").read_text(encoding="utf-8"))


def _equals(path: str, value):
    return [{"kind": "value_equals", "path": path, "value": value}]


def _exists(target_id: str):
    return [{"kind": "target_exists", "target_id": target_id}]


def test_edit_operation_is_discriminated_union_with_all_first_slice_variants():
    schema = edit_operation_json_schema()
    item_schema = schema["items"]
    assert item_schema["discriminator"]["propertyName"] == "op"
    tags = set(item_schema["discriminator"]["mapping"])
    assert tags == {
        "SetDimension", "SetMaterial", "ChangeArchetype", "AddSection",
        "DuplicateModel",
        "UpdateSection", "DeleteSection", "AddShelf", "AddPanel", "MovePanel", "MovePart",
        "ResizePart", "DeletePart", "QueryModel", "DiagnoseModel",
    }


def test_duplicate_model_builds_two_horizontal_composite_blocks():
    source = json.loads((ROOT / "paramspecs" / "cabinet_700x400x500.json").read_text(
        encoding="utf-8"
    ))
    original = copy.deepcopy(source)

    result = apply_edit_operations(source, [{
        "op": "DuplicateModel",
        "target_id": "model",
        "preconditions": _exists("model"),
        "direction": "right",
        "gap_mm": 0,
    }])

    duplicate = result["spec"]
    assert duplicate["archetype"] == "composite"
    assert duplicate["dimensions"]["width"] == 1400
    assert [block["origin"]["x"] for block in duplicate["blocks"]] == [0, 700]
    assert duplicate["blocks"][0]["spec"] == duplicate["blocks"][1]["spec"]
    assert source == original

    from src.studio import build_payload

    payload = build_payload(duplicate)
    assert payload["ok"], payload["issues"]
    from src.generators import generate_from_paramspec

    project = generate_from_paramspec(duplicate)
    assert max(panel["placement"]["x2"] for panel in project["panels"]) == 1400


def test_duplicate_model_can_repeat_an_existing_composite_without_nesting():
    source = json.loads((ROOT / "paramspecs" / "cabinet_700x400x500.json").read_text(
        encoding="utf-8"
    ))
    first = apply_edit_operations(source, [{
        "op": "DuplicateModel", "target_id": "model",
        "preconditions": _exists("model"), "direction": "right", "gap_mm": 50,
    }])["spec"]
    repeated = apply_edit_operations(first, [{
        "op": "DuplicateModel", "target_id": "model",
        "preconditions": _exists("model"), "direction": "left", "gap_mm": 100,
    }])["spec"]

    assert repeated["archetype"] == "composite"
    assert repeated["dimensions"]["width"] == 3000
    assert len(repeated["blocks"]) == 4
    assert all(block["spec"]["archetype"] != "composite" for block in repeated["blocks"])
    assert [block["origin"]["x"] for block in repeated["blocks"]] == [0, 750, 1550, 2300]

    from src.studio import build_payload

    payload = build_payload(repeated)
    assert payload["ok"], payload["issues"]


@pytest.mark.parametrize("missing", ["target_id", "preconditions"])
def test_target_and_preconditions_are_required_for_every_operation(missing):
    raw = {
        "op": "SetDimension", "target_id": "dimensions.width",
        "preconditions": _equals("dimensions.width", SPEC["dimensions"]["width"]),
        "dimension": "width", "value": 900,
    }
    raw.pop(missing)
    with pytest.raises(ValidationError):
        parse_edit_operations([raw])


def test_set_operations_change_only_named_fields_and_are_deterministic():
    operations = [
        {
            "op": "SetDimension", "target_id": "dimensions.width",
            "preconditions": _equals("dimensions.width", SPEC["dimensions"]["width"]),
            "dimension": "width", "value": 900,
        },
        {
            "op": "SetMaterial", "target_id": "materials.facade_color",
            "preconditions": _exists("materials"),
            "field": "facade_color", "value": "Дуб вотан",
        },
    ]
    first = apply_edit_operations(SPEC, operations)
    second = apply_edit_operations(SPEC, operations)

    assert first == second
    expected = copy.deepcopy(SPEC)
    expected["dimensions"]["width"] = 900
    expected["materials"]["facade_color"] = "Дуб вотан"
    assert first["spec"] == expected
    assert SPEC != expected


def test_batch_is_atomic_when_later_precondition_fails():
    original = copy.deepcopy(SPEC)
    operations = [
        {
            "op": "SetDimension", "target_id": "dimensions.width",
            "preconditions": _equals("dimensions.width", SPEC["dimensions"]["width"]),
            "dimension": "width", "value": 900,
        },
        {
            "op": "SetDimension", "target_id": "dimensions.depth",
            "preconditions": _equals("dimensions.depth", -1),
            "dimension": "depth", "value": 500,
        },
    ]
    with pytest.raises(EditApplicationError, match="operation 1"):
        apply_edit_operations(SPEC, operations)
    assert SPEC == original


def test_section_operations_use_stable_target_ids():
    added = apply_edit_operations(SPEC, [{
        "op": "AddSection", "target_id": "section:storage-left",
        "preconditions": [{"kind": "target_missing", "target_id": "section:storage-left"}],
        "section": {"id": "storage-left", "kind": "shelves", "shelves": 1},
    }])["spec"]
    updated = apply_edit_operations(added, [
        {
            "op": "AddShelf", "target_id": "section:storage-left",
            "preconditions": _exists("section:storage-left"), "count": 2,
        },
        {
            "op": "UpdateSection", "target_id": "section:storage-left",
            "preconditions": _equals("sections.0.shelves", 3),
            "changes": {"width_share": 0.4},
        },
    ])["spec"]
    assert updated["sections"] == [{
        "id": "storage-left", "kind": "shelves", "shelves": 3, "width_share": 0.4,
    }]
    deleted = apply_edit_operations(updated, [{
        "op": "DeleteSection", "target_id": "section:storage-left",
        "preconditions": _exists("section:storage-left"),
    }])["spec"]
    assert deleted["sections"] == []


def test_part_operations_create_only_targeted_overrides():
    context = {"panels": [{"n": "Полка 1", "t": "shelf"}]}
    result = apply_edit_operations(SPEC, [
        {
            "op": "MovePart", "target_id": "part:Полка 1",
            "preconditions": _exists("part:Полка 1"), "delta": [0, 32, 0],
        },
        {
            "op": "ResizePart", "target_id": "part:Полка 1",
            "preconditions": _exists("part:Полка 1"), "placement": {"z2": 500},
        },
        {
            "op": "DeletePart", "target_id": "part:Полка 1",
            "preconditions": _exists("part:Полка 1"),
        },
    ], context)["spec"]
    assert result["overrides"] == [
        {"panel": "Полка 1", "action": "transform", "move": [0.0, 32.0, 0.0]},
        {"panel": "Полка 1", "action": "transform", "placement": {"z2": 500.0}},
        {"panel": "Полка 1", "action": "delete"},
    ]
    unrelated = copy.deepcopy(result)
    unrelated.pop("overrides")
    assert unrelated == SPEC


def test_part_target_must_exist_even_with_unrelated_precondition():
    with pytest.raises(EditApplicationError, match="part target.*missing"):
        apply_edit_operations(SPEC, [{
            "op": "DeletePart", "target_id": "part:Несуществующая",
            "preconditions": _exists("model"),
        }], {"panels": []})


def test_part_delete_changes_target_existence_for_later_operations():
    context = {"panels": [{"n": "Полка 1"}]}
    with pytest.raises(EditApplicationError, match="operation 1.*missing"):
        apply_edit_operations(SPEC, [
            {
                "op": "DeletePart", "target_id": "part:Полка 1",
                "preconditions": _exists("part:Полка 1"),
            },
            {
                "op": "MovePart", "target_id": "part:Полка 1",
                "preconditions": _exists("part:Полка 1"), "delta": [0, 32, 0],
            },
        ], context)


def test_query_and_diagnose_are_read_only_operations():
    result = apply_edit_operations(SPEC, [
        {
            "op": "QueryModel", "target_id": "model",
            "preconditions": _exists("model"), "query": "cost",
        },
        {
            "op": "DiagnoseModel", "target_id": "model",
            "preconditions": _exists("model"), "scope": "all",
        },
    ], {"estimate_total": 2181, "check_errors": "нет — все проверки зелёные"})
    assert result["changed"] is False
    assert result["spec"] == SPEC
    assert "2 181" in result["replies"][0]
    assert "зелёные" in result["replies"][1]


def test_chat_edit_applies_provider_operations_and_returns_readable_diff(monkeypatch):
    import src.spec_chat as spec_chat

    class Provider:
        def chat(self, *_args, **_kwargs):
            return {"reply": "Ширина изменена.", "operations": [{
                "op": "SetDimension", "target_id": "dimensions.width",
                "preconditions": _equals("dimensions.width", SPEC["dimensions"]["width"]),
                "dimension": "width", "value": 900,
            }]}

    monkeypatch.setattr(spec_chat, "get_chat_provider", lambda _name=None: Provider())
    result = spec_chat.chat_edit(SPEC, "сделай ширину 900")
    assert result["spec"]["dimensions"]["width"] == 900
    assert result["changes"] == [
        f"dimensions.width: {SPEC['dimensions']['width']!r} → 900.0"
    ]
    assert result["operations"][0]["op"] == "SetDimension"


def test_legacy_full_spec_with_collateral_change_is_rejected(monkeypatch):
    import src.spec_chat as spec_chat

    class Provider:
        def chat(self, *_args, **_kwargs):
            rewritten = copy.deepcopy(SPEC)
            rewritten["dimensions"]["width"] = 900
            rewritten["project_name"] = "Незваное переименование"
            return {"reply": "Готово.", "spec": rewritten}

    monkeypatch.setattr(spec_chat, "get_chat_provider", lambda _name=None: Provider())
    result = spec_chat.chat_edit(SPEC, "сделай ширину 900")
    assert result["spec"] is None
    assert result["changes"] == []
    assert "unsupported field 'project_name'" in result["error"]


def test_target_id_must_match_typed_operation_payload():
    with pytest.raises(ValidationError, match="target_id must be"):
        parse_edit_operations([{
            "op": "SetMaterial", "target_id": "materials.color",
            "preconditions": _equals("materials.color", SPEC["materials"]["color"]),
            "field": "facade_color", "value": "Белый",
        }])
