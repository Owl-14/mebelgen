"""MEB-141: a fine-tune remains impossible until offline evidence is real."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.finetune_readiness import (  # noqa: E402
    AB_DECISION_RULE,
    evaluate_ab_result,
    evaluate_readiness,
)


def _accept(_: dict[str, Any]) -> tuple[bool, list[str]]:
    return True, []


def _pair(index: int, split: str) -> dict[str, Any]:
    return {
        "pair_id": f"pair-{index:04d}",
        "source_tz": (
            f"Проверенное клиентское техническое задание номер {index}: шкаф с уникальными "
            f"габаритами и конфигурацией секций {index}."
        ),
        "paramspec": {
            "schemaVersion": "paramspec-v1",
            "project_name": f"approved-order-{index}",
            "dimensions": {"width": 500 + index, "depth": 400, "height": 700},
        },
        "split": split,
        "leakage_group": f"order-{index}",
        "review": {
            "status": "approved",
            "reviewer_id": "test-reviewer",
            "reviewed_at": "2026-08-13T00:00:00Z",
        },
        "provenance": {
            "source_id": f"customer-order-{index}",
            "source_type": "customer_tz",
            "rights_record": {
                "record_id": f"contract-{index}",
                "source_id": f"customer-order-{index}",
                "source_type": "customer_tz",
                "basis": "customer_contract",
                "scope": "paramspec-model-development",
                "verified_by": "governance-reviewer",
                "verified_at": "2026-08-13T00:00:00Z",
                "document_sha256": f"{index:064x}",
            },
        },
    }


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _write_dataset(dataset: Path, pairs: list[dict[str, Any]]) -> None:
    dataset.mkdir()
    for index, pair in enumerate(pairs):
        _write_json(dataset / f"{index:04d}.json", pair)


def _plateau_evidence() -> dict[str, Any]:
    return {
        "prompt_rag": {
            "benchmark_frozen": True,
            "benchmark_excluded_from_training_and_rag": True,
            "runs": [
                {
                    "run_id": "offline-1",
                    "benchmark_id": "frozen-v1",
                    "benchmark_sha256": "a" * 64,
                    "prompt_version": "p1",
                    "rag_version": "r1",
                    "cases": 80,
                    "paramspec_field_accuracy": 0.800,
                },
                {
                    "run_id": "offline-2",
                    "benchmark_id": "frozen-v1",
                    "benchmark_sha256": "a" * 64,
                    "prompt_version": "p2",
                    "rag_version": "r2",
                    "cases": 80,
                    "paramspec_field_accuracy": 0.806,
                },
                {
                    "run_id": "offline-3",
                    "benchmark_id": "frozen-v1",
                    "benchmark_sha256": "a" * 64,
                    "prompt_version": "p3",
                    "rag_version": "r3",
                    "cases": 80,
                    "paramspec_field_accuracy": 0.809,
                },
            ]
        }
    }


def _passing_ab_result() -> dict[str, Any]:
    return {
        "rule_version": AB_DECISION_RULE["rule_version"],
        "design": AB_DECISION_RULE["design"],
        "paired": True,
        "holdout_frozen": True,
        "holdout_excluded_from_training_and_rag": True,
        "holdout_sha256": "b" * 64,
        "confidence_level": AB_DECISION_RULE["confidence_level"],
        "cases_per_arm": AB_DECISION_RULE["min_cases_per_arm"],
        "primary_delta_ci95": [0.021, 0.04],
        "production_gate_pass_rate_delta_ci95": [0.0, 0.02],
        "invalid_output_rate_delta_ci95": [-0.02, 0.0],
    }


def test_empty_dry_run_stays_in_backlog_and_has_exact_blockers(tmp_path: Path):
    report = evaluate_readiness(tmp_path / "missing", pair_validator=_accept)

    assert report["mode"] == "dry-run"
    assert report["decision"] == "stay_in_backlog"
    assert report["ready_for_finetune_experiment"] is False
    assert report["dataset"]["clean_unique_pairs"] == 0
    assert {item["code"] for item in report["blockers"]} >= {
        "dataset.minimum_clean_pairs_not_met",
        "dataset.split_distribution_invalid",
        "prompt_rag.evidence_missing",
        "prompt_rag.plateau_not_proven",
    }
    assert report["ab_decision_rule"]["min_cases_per_arm"] == 100


def test_two_hundred_clean_unique_pairs_and_plateau_pass_the_gate(tmp_path: Path):
    dataset = tmp_path / "dataset"
    splits = ["train"] * 160 + ["validation"] * 20 + ["test"] * 20
    _write_dataset(dataset, [_pair(index, split) for index, split in enumerate(splits)])
    evidence = tmp_path / "evidence.json"
    _write_json(evidence, _plateau_evidence())

    report = evaluate_readiness(dataset, evidence_path=evidence, pair_validator=_accept)

    assert report["ready_for_finetune_experiment"] is True
    assert report["decision"] == "eligible_for_offline_experiment"
    assert report["dataset"]["clean_unique_pairs"] == 200
    assert report["dedup"]["ok"] is True
    assert report["split"]["counts"] == {"train": 160, "validation": 20, "test": 20}
    assert report["leakage"]["ok"] is True
    assert report["prompt_rag_plateau"]["ok"] is True
    assert report["blockers"] == []


def test_quality_dedup_and_cross_split_leakage_fail_closed(tmp_path: Path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    first = _pair(1, "train")
    second = _pair(2, "test")
    second["source_tz"] = first["source_tz"]
    second["leakage_group"] = first["leakage_group"]
    second["provenance"]["rights_record"]["scope"] = "display-only"
    _write_json(dataset / "one.json", first)
    _write_json(dataset / "two.json", second)

    report = evaluate_readiness(dataset, pair_validator=_accept)
    encoded = json.dumps(report, ensure_ascii=False)

    assert report["ready_for_finetune_experiment"] is False
    assert report["dataset"]["quality_error_counts"]["pair.rights_scope_not_allowed"] == 1
    assert report["dedup"]["duplicate_sources"]
    assert report["leakage"]["groups"]
    assert first["source_tz"] not in encoded
    assert first["leakage_group"] not in encoded
    assert first["pair_id"] not in encoded


def test_plateau_without_explicit_holdout_is_not_evidence(tmp_path: Path):
    evidence_value = _plateau_evidence()
    evidence_value["prompt_rag"].pop("benchmark_excluded_from_training_and_rag")
    evidence = tmp_path / "evidence.json"
    _write_json(evidence, evidence_value)

    report = evaluate_readiness(tmp_path / "missing", evidence_path=evidence, pair_validator=_accept)

    assert report["prompt_rag_plateau"]["ok"] is False
    assert report["prompt_rag_plateau"]["benchmark_excluded_from_training_and_rag"] is False


def test_synthetic_fixture_generated_and_test_sources_never_reach_ready(tmp_path: Path):
    dataset = tmp_path / "dataset"
    splits = ["train"] * 160 + ["validation"] * 20 + ["test"] * 20
    prohibited = ("synthetic", "fixture", "generated", "test")
    pairs = []
    for index, split in enumerate(splits):
        pair = _pair(index, split)
        pair["provenance"]["source_type"] = prohibited[index % len(prohibited)]
        pair["provenance"]["rights_record"]["source_type"] = prohibited[index % len(prohibited)]
        pairs.append(pair)
    _write_dataset(dataset, pairs)
    evidence = tmp_path / "evidence.json"
    _write_json(evidence, _plateau_evidence())

    report = evaluate_readiness(dataset, evidence_path=evidence, pair_validator=_accept)

    assert report["ready_for_finetune_experiment"] is False
    assert report["decision"] == "stay_in_backlog"
    assert report["dataset"]["clean_unique_pairs"] == 0
    assert report["dataset"]["quality_error_counts"]["pair.source_type_prohibited"] == 200


def test_pair_local_approval_booleans_do_not_replace_rights_record(tmp_path: Path):
    dataset = tmp_path / "dataset"
    pair = _pair(1, "train")
    pair["provenance"].pop("rights_record")
    pair["provenance"].update({
        "training_approved": True,
        "usage_rights": "training-approved",
        "rights_record_id": "self-asserted",
    })
    _write_dataset(dataset, [pair])

    report = evaluate_readiness(dataset, pair_validator=_accept)

    assert report["ready_for_finetune_experiment"] is False
    assert report["dataset"]["quality_error_counts"]["pair.rights_record_missing"] == 1


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("source_id", "another-order", "pair.rights_source_id_mismatch"),
        ("source_type", "licensed_external_tz", "pair.rights_source_type_mismatch"),
    ],
)
def test_rights_record_must_be_bound_to_pair_source(
    tmp_path: Path, field: str, value: str, error: str
):
    dataset = tmp_path / "dataset"
    pair = _pair(1, "train")
    pair["provenance"]["rights_record"][field] = value
    _write_dataset(dataset, [pair])

    report = evaluate_readiness(dataset, pair_validator=_accept)

    assert report["ready_for_finetune_experiment"] is False
    assert report["dataset"]["quality_error_counts"][error] == 1


def test_one_source_id_across_two_splits_blocks_two_hundred_clean_pairs(tmp_path: Path):
    dataset = tmp_path / "dataset"
    splits = ["train"] * 160 + ["validation"] * 20 + ["test"] * 20
    pairs = []
    for index, split in enumerate(splits):
        pair = _pair(index, split)
        pair["provenance"]["source_id"] = "one-customer-order"
        pair["provenance"]["rights_record"]["source_id"] = "one-customer-order"
        pairs.append(pair)
    _write_dataset(dataset, pairs)
    evidence = tmp_path / "evidence.json"
    _write_json(evidence, _plateau_evidence())

    report = evaluate_readiness(dataset, evidence_path=evidence, pair_validator=_accept)

    assert report["dataset"]["clean_unique_pairs"] == 200
    assert report["split"]["ok"] is True
    assert report["ready_for_finetune_experiment"] is False
    assert any(group["kind"] == "source_id" for group in report["leakage"]["groups"])
    assert "dataset.split_leakage_detected" in {item["code"] for item in report["blockers"]}


def test_ab_rule_adopts_only_a_significant_gain_without_safety_regression():
    passing = _passing_ab_result()

    assert evaluate_ab_result(passing)["decision"] == "adopt"
    failing = dict(passing, primary_delta_ci95=[0.019, 0.05])
    decision = evaluate_ab_result(failing)
    assert decision["decision"] == "reject"
    assert "ab.primary_gain_not_significant" in decision["reasons"]


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"primary_delta_ci95": [float("nan"), 0.04]}, "ab.primary_interval_invalid"),
        ({"primary_delta_ci95": [0.04, 0.03]}, "ab.primary_interval_invalid"),
        ({"primary_delta_ci95": ["0.021", 0.04]}, "ab.primary_interval_invalid"),
        ({"production_gate_pass_rate_delta_ci95": [0.0, float("inf")]},
         "ab.production_interval_invalid"),
        ({"invalid_output_rate_delta_ci95": [0.01, -0.01]},
         "ab.invalid_output_interval_invalid"),
        ({"paired": False}, "ab.design_not_paired_frozen"),
        ({"design": "unpaired_holdout"}, "ab.design_not_paired_frozen"),
        ({"holdout_frozen": False}, "ab.holdout_not_frozen"),
        ({"holdout_excluded_from_training_and_rag": False},
         "ab.holdout_leakage_not_excluded"),
        ({"confidence_level": 0.90}, "ab.confidence_policy_mismatch"),
        ({"confidence_level": float("nan")}, "ab.confidence_policy_mismatch"),
    ],
)
def test_ab_counterexamples_never_adopt(changes: dict[str, Any], reason: str):
    result = _passing_ab_result()
    result.update(changes)

    decision = evaluate_ab_result(result)

    assert decision["adopt"] is False
    assert decision["decision"] == "reject"
    assert reason in decision["reasons"]
