"""MEB-151: versioned offline trace replay and eval scenarios."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from src.trace_replay import (
    DEFAULT_DATASET,
    TraceReplayError,
    compare_reports,
    replay_case,
    run_dataset,
    write_report,
)


def _dataset() -> dict:
    return json.loads(DEFAULT_DATASET.read_text(encoding="utf-8"))


def _replay(case: dict) -> dict:
    dataset = _dataset()
    return replay_case(
        case,
        DEFAULT_DATASET.parent,
        dataset["output_profiles"],
        dataset["provider_policies"],
    )


def test_versioned_dataset_replays_every_scenario_offline() -> None:
    report = run_dataset()

    assert report["dataset_version"] == "2026-08-13.2"
    assert report["case_count"] == 12
    assert report["passed"] == 12
    assert report["failed"] == 0
    assert all(
        case["decision_digest"] and case["node_outputs_digest"]
        and case["output_digest"] and case["verdict_digest"]
        for case in report["cases"]
    )


def test_dataset_covers_editor_success_safety_and_recovery_matrix() -> None:
    tags = {tag for case in _dataset()["cases"] for tag in case["tags"]}
    assert {
        "text", "photo", "edit", "add-part", "sections", "material",
        "diagnosis", "prompt-injection", "dimensions", "concurrency", "repair",
    } <= tags

    for case in _dataset()["cases"]:
        expected = case["expected"]
        assert expected["decision"]
        assert expected["node_outputs"]
        assert "diff" in expected["output"] or expected["profile"] == "rejected-before-gate"


def test_report_is_comparable_across_prompt_and_model_versions(tmp_path: Path) -> None:
    baseline = run_dataset()
    path = tmp_path / "baseline.json"
    write_report(baseline, path)
    restored = json.loads(path.read_text(encoding="utf-8"))
    assert compare_reports(restored, baseline)["ok"] is True

    candidate = copy.deepcopy(baseline)
    candidate["cases"][0]["output"]["part_count"] += 1
    candidate["cases"][0]["verdict_digest"] = "changed"
    comparison = compare_reports(baseline, candidate)
    assert comparison["ok"] is False
    assert [change["id"] for change in comparison["changed"]] == ["create-from-text"]


def test_saved_node_output_drift_fails_the_case() -> None:
    dataset = _dataset()
    case = copy.deepcopy(dataset["cases"][2])
    case["recorded"]["nodes"][1]["output"]["operations"][0]["value"] = 1700

    result = _replay(case)

    assert result["ok"] is False
    assert any("expected.node_outputs" in mismatch for mismatch in result["mismatches"])


@pytest.mark.parametrize(
    ("name", "case_index", "mutate"),
    [
        ("command", 2, lambda case: case.__setitem__("command", "другая команда")),
        ("command_hash", 2, lambda case: case["recorded"]["decision"].__setitem__("command_hash", "0" * 64)),
        ("command_class", 2, lambda case: case["recorded"]["decision"].__setitem__("command_class", "query")),
        ("route", 2, lambda case: case["recorded"]["nodes"][0]["output"].__setitem__("route", "answer_query")),
        ("prompt_id", 2, lambda case: case["recorded"].__setitem__("prompt_id", "furniture.wrong")),
        ("prompt_version", 2, lambda case: case["recorded"].__setitem__("prompt_version", "999.0.0")),
        ("provider", 2, lambda case: case["recorded"].__setitem__("provider", "live-forbidden")),
        ("model", 2, lambda case: case["recorded"].__setitem__("model", "live/model")),
        ("provider_policy", 2, lambda case: case["recorded"]["decision"].__setitem__("provider_policy", "deterministic")),
        ("vision_stage", 1, lambda case: case["recorded"]["nodes"].pop(1)),
        ("operation_types", 2, lambda case: case["recorded"]["nodes"][1]["output"]["operations"][0].__setitem__("op", "SetMaterial")),
    ],
)
def test_decision_envelope_tampering_rejects_before_operation_replay(
    name: str, case_index: int, mutate,
) -> None:
    case = copy.deepcopy(_dataset()["cases"][case_index])
    mutate(case)

    result = _replay(case)

    assert result["ok"] is False, name
    assert result["output"]["code"] == "decision.invalid_envelope", name
    assert result["output"]["part_count"] is None, name
    assert result["output"]["drilling_count"] is None, name
    assert any("decision" in mismatch for mismatch in result["mismatches"]), name


def test_dataset_references_cannot_escape_version_directory(tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    case = {
        "id": "escape",
        "route": "edit_operations",
        "source_ref": "../outside.json",
        "recorded": {"nodes": []},
        "expected": {"node_outputs": {}, "output": {}},
    }
    with pytest.raises(TraceReplayError, match="escapes"):
        replay_case(case, tmp_path / "dataset", {}, {"offline": {}})


def test_recorded_traces_are_privacy_safe_references_not_raw_payloads() -> None:
    dataset = _dataset()
    recorded = json.dumps(
        [case["recorded"] for case in dataset["cases"]],
        ensure_ascii=False,
    ).lower()
    for forbidden in (
        "schemaversion", "system_prompt", "raw_prompt", "image_base64",
        "data:image", "authorization", "api_key", "secret",
    ):
        assert forbidden not in recorded
