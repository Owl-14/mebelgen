from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from jsonschema import Draft202012Validator

from src.generators import generate_from_paramspec
from src.generators.registry import _normalize_spec
from src.paramspec import (
    SCHEMA_PATH,
    ParamSpec,
    paramspec_json_schema,
    parse_paramspec,
    validate_paramspec,
)

ROOT = Path(__file__).resolve().parent.parent


def _paramspecs():
    for path in sorted((ROOT / "paramspecs").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("schemaVersion") == "paramspec-v1":
            yield path, data


def test_all_paramspec_v1_files_round_trip_without_geometry_change():
    checked = 0
    for path, raw in _paramspecs():
        typed = parse_paramspec(raw)
        dumped = typed.to_generator_dict()
        assert dumped == raw, path.name
        assert generate_from_paramspec(dumped)["panels"] == generate_from_paramspec(raw)["panels"], path.name
        checked += 1
    assert checked == 38


@pytest.mark.parametrize(
    ("mutation", "path"),
    [
        (lambda spec: spec.update(unknown_ai_field=True), "unknown_ai_field"),
        (lambda spec: spec["dimensions"].update(typo_width=10), "dimensions.typo_width"),
        (lambda spec: spec["sections"][0].update(typo_drawers=3), "sections.0.typo_drawers"),
    ],
)
def test_unknown_fields_are_rejected_with_studio_path(mutation, path):
    _, spec = next(_paramspecs())
    spec = json.loads(json.dumps(spec))
    mutation(spec)
    errors = validate_paramspec(spec)
    assert errors
    assert any(path in error and "extra_forbidden" in error for error in errors)


def test_typed_model_provides_dict_and_json_output():
    _, raw = next(_paramspecs())
    model = ParamSpec.model_validate(raw)
    assert model.to_generator_dict() == raw
    assert json.loads(model.model_dump_json(by_alias=True, exclude_none=True, exclude_unset=True)) == raw


def test_section_and_archetype_are_discriminated_unions():
    schema = paramspec_json_schema()
    assert schema["discriminator"]["propertyName"] == "archetype"
    section_schema = schema["$defs"]["ParamSpecFields"]["properties"]["sections"]["anyOf"][0]["items"]
    assert section_schema["discriminator"]["propertyName"] == "kind"


def test_checked_in_json_schema_is_generated_from_models():
    assert json.loads(SCHEMA_PATH.read_text(encoding="utf-8")) == paramspec_json_schema()


def test_generated_schema_accepts_legacy_specs_and_rejects_unknown_fields():
    validator = Draft202012Validator(paramspec_json_schema())
    for path, raw in _paramspecs():
        assert not list(validator.iter_errors(raw)), path.name
    _, raw = next(_paramspecs())
    raw["unknown_ai_field"] = True
    assert list(validator.iter_errors(raw))


def test_wrong_section_discriminator_is_localized():
    _, raw = next(_paramspecs())
    raw = json.loads(json.dumps(raw))
    raw["sections"] = [{"kind": "drawer"}]
    with pytest.raises(ValidationError):
        parse_paramspec(raw)
    assert any("sections.0" in error and "union_tag_invalid" in error for error in validate_paramspec(raw))


def test_legacy_minimal_draft_round_trips_without_inventing_archetype():
    raw = {
        "schemaVersion": "paramspec-v1",
        "project_name": "Новое изделие",
        "draft": True,
    }

    typed = parse_paramspec(raw)

    assert typed.to_generator_dict() == raw
    assert not validate_paramspec(raw)
    assert not list(Draft202012Validator(paramspec_json_schema()).iter_errors(raw))


def test_missing_archetype_is_only_allowed_for_minimal_draft():
    raw = {
        "schemaVersion": "paramspec-v1",
        "project_name": "Не черновик",
        "draft": False,
    }

    assert any("union_tag_not_found" in error for error in validate_paramspec(raw))


def test_persisted_creation_marker_and_hinge_options_are_typed():
    _, raw = next(_paramspecs())
    raw = json.loads(json.dumps(raw))
    raw["created"] = True
    raw.setdefault("hardware", {})["hinges"] = {
        "type": "накладные",
        "color": "чёрный",
        "adjustable": True,
    }

    typed = parse_paramspec(raw)

    assert typed.to_generator_dict() == raw
    assert not validate_paramspec(raw)
    assert not list(Draft202012Validator(paramspec_json_schema()).iter_errors(raw))


def test_catalog_identity_is_typed_but_unknown_catalog_fields_stay_forbidden():
    _, raw = next(_paramspecs())
    raw = json.loads(json.dumps(raw))
    raw["catalog"] = {
        "creator_user_id": "user-1",
        "responsible_user_id": "user-2",
        "author": "Автор",
        "responsible": "Ответственный",
    }

    assert parse_paramspec(raw).to_generator_dict() == raw
    assert not validate_paramspec(raw)

    raw["catalog"]["unexpected"] = True
    assert any("catalog.unexpected" in error for error in validate_paramspec(raw))


def test_legacy_drawer_closer_is_accepted_and_normalized_without_mutating_input():
    _, raw = next(_paramspecs())
    raw = json.loads(json.dumps(raw))
    guides = raw.setdefault("hardware", {}).setdefault("drawer_guides", {})
    guides.pop("soft_close", None)
    guides["with_closer"] = True
    before = json.loads(json.dumps(raw))

    assert not validate_paramspec(raw)
    normalized = _normalize_spec(raw)

    assert raw == before
    assert "with_closer" not in normalized["hardware"]["drawer_guides"]
    assert normalized["hardware"]["drawer_guides"]["soft_close"] is True
