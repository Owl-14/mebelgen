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


def test_versioned_dataset_replays_every_scenario_offline() -> None:
    report = run_dataset()

    assert report["dataset_version"] == "2026-08-13.1"
    assert report["case_count"] == 12
    assert report["passed"] == 12
    assert report["failed"] == 0
    assert all(case["node_outputs_digest"] and case["output_digest"] for case in report["cases"])


def test_dataset_covers_editor_success_safety_and_recovery_matrix() -> None:
    tags = {tag for case in _dataset()["cases"] for tag in case["tags"]}
    assert {
        "text", "photo", "edit", "add-part", "sections", "material",
        "diagnosis", "prompt-injection", "dimensions", "concurrency", "repair",
    } <= tags

    for case in _dataset()["cases"]:
        expected = case["expected"]
        assert expected["node_outputs"]
        assert "diff" in expected["output"] or expected["profile"] == "rejected-before-gate"


def test_report_is_comparable_across_prompt_and_model_versions(tmp_path: Path) -> None:
    baseline = run_dataset()
    path = tmp_path / "baseline.json"
    write_report(baseline, path)
    restored = json.loads(path.read_text(encoding="utf-8"))
    assert compare_reports(restored, baseline)["ok"] is True

    candidate = copy.deepcopy(baseline)
    candidate["cases"][0]["model"] = "candidate/model-v2"
    candidate["profiles"] = ["furniture.create@1.0.0 / candidate/model-v2"]
    assert compare_reports(baseline, candidate)["ok"] is True

    candidate["cases"][0]["output"]["part_count"] += 1
    candidate["cases"][0]["output_digest"] = "changed"
    comparison = compare_reports(baseline, candidate)
    assert comparison["ok"] is False
    assert [change["id"] for change in comparison["changed"]] == ["create-from-text"]


def test_saved_node_output_drift_fails_the_case() -> None:
    dataset = _dataset()
    case = copy.deepcopy(dataset["cases"][2])
    case["recorded"]["nodes"][1]["output"]["operations"][0]["value"] = 1700

    result = replay_case(case, DEFAULT_DATASET.parent, dataset["output_profiles"])

    assert result["ok"] is False
    assert any("expected.node_outputs" in mismatch for mismatch in result["mismatches"])


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
        replay_case(case, tmp_path / "dataset")


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
