"""MEB-141: a fine-tune remains impossible until offline evidence is real."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

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
            f"Проверенное техническое задание номер {index}: шкаф с уникальными "
            f"габаритами и конфигурацией секций {index}."
        ),
        "paramspec": {
            "schemaVersion": "paramspec-v1",
            "project_name": f"fixture-{index}",
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
            "source_id": f"test-source-{index}",
            "usage_rights": "training-approved",
            "rights_record_id": f"test-rights-{index}",
        },
    }


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


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
    dataset.mkdir()
    splits = ["train"] * 160 + ["validation"] * 20 + ["test"] * 20
    for index, split in enumerate(splits):
        _write_json(dataset / f"{index:04d}.json", _pair(index, split))
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
    second["provenance"]["usage_rights"] = "unknown"
    _write_json(dataset / "one.json", first)
    _write_json(dataset / "two.json", second)

    report = evaluate_readiness(dataset, pair_validator=_accept)
    encoded = json.dumps(report, ensure_ascii=False)

    assert report["ready_for_finetune_experiment"] is False
    assert report["dataset"]["quality_error_counts"]["pair.training_rights_missing"] == 1
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


def test_ab_rule_adopts_only_a_significant_gain_without_safety_regression():
    passing = {
        "cases_per_arm": AB_DECISION_RULE["min_cases_per_arm"],
        "primary_delta_ci95": [0.021, 0.04],
        "production_gate_pass_rate_delta_ci95": [0.0, 0.02],
        "invalid_output_rate_delta_ci95": [-0.02, 0.0],
    }

    assert evaluate_ab_result(passing)["decision"] == "adopt"
    failing = dict(passing, primary_delta_ci95=[0.019, 0.05])
    decision = evaluate_ab_result(failing)
    assert decision["decision"] == "reject"
    assert "ab.primary_gain_not_significant" in decision["reasons"]
