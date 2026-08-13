"""Fail-closed, offline readiness gate for a possible ParamSpec fine-tune.

The gate never trains a model and never calls an LLM.  It only inspects local,
explicitly approved ``TZ -> ParamSpec`` pairs and offline evaluation evidence.
Reports contain hashes and counters, not source TZ text or ParamSpec payloads.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


REPORT_SCHEMA_VERSION = "finetune-data-readiness-v1"
MIN_CLEAN_PAIRS = 200
MIN_SOURCE_CHARS = 40
ALLOWED_SPLITS = ("train", "validation", "test")
SPLIT_RATIO_LIMITS = {
    "train": (0.70, 0.90),
    "validation": (0.05, 0.20),
    "test": (0.05, 0.20),
}
PLATEAU_MIN_RUNS = 3
PLATEAU_MIN_CASES = 50
PLATEAU_MAX_STEP_DELTA = 0.01
ALLOWED_SOURCE_TYPES = frozenset({
    "customer_tz",
    "internal_production_tz",
    "licensed_external_tz",
})
PROHIBITED_SOURCE_TYPES = frozenset({
    "synthetic",
    "fixture",
    "generated",
    "test",
    "mock",
    "demo",
})
ALLOWED_RIGHTS_BASES = frozenset({
    "customer_contract",
    "explicit_consent",
    "internal_ownership",
    "dataset_license",
})
TRAINING_RIGHTS_SCOPE = "paramspec-model-development"

AB_DECISION_RULE = {
    "rule_version": "paramspec-ab-v1",
    "design": "paired_frozen_holdout",
    "min_cases_per_arm": 100,
    "confidence_level": 0.95,
    "primary_metric": "paramspec_field_accuracy",
    "min_primary_delta": 0.02,
    "safety_metrics": {
        "production_gate_pass_rate_min_delta": 0.0,
        "invalid_output_rate_max_delta": 0.0,
    },
    "adopt_when": (
        "rule_version=paramspec-ab-v1 AND paired frozen holdout excluded from "
        "training/RAG AND confidence_level=0.95 AND cases_per_arm >= 100 AND "
        "all intervals are finite and ordered AND primary_delta_ci95.lower >= 0.02 AND "
        "production_gate_pass_rate_delta_ci95.lower >= 0.0 AND "
        "invalid_output_rate_delta_ci95.upper <= 0.0"
    ),
}


@dataclass(frozen=True)
class PairInspection:
    path: str
    pair_id: str
    split: str
    leakage_group: str
    source_id: str
    source_hash: str
    paramspec_hash: str
    pair_hash: str
    errors: tuple[str, ...]

    @property
    def quality_ok(self) -> bool:
        return not self.errors


PairValidator = Callable[[Mapping[str, Any]], tuple[bool, Iterable[str]]]


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _hash_json(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return _hash_bytes(encoded)


def _normalise_source(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


def _default_pair_validator(paramspec: Mapping[str, Any]) -> tuple[bool, Iterable[str]]:
    from .production_gate import evaluate_production_gate

    decision = evaluate_production_gate(paramspec)
    return decision.report.ok, (issue.code for issue in decision.report.errors)


def _inspect_pair(
    path: Path,
    root: Path,
    *,
    pair_validator: PairValidator,
) -> PairInspection:
    errors: list[str] = []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return PairInspection(
            path=path.relative_to(root).as_posix(),
            pair_id="",
            split="",
            leakage_group="",
            source_id="",
            source_hash="",
            paramspec_hash="",
            pair_hash="",
            errors=("pair.invalid_json",),
        )

    if not isinstance(payload, dict):
        payload = {}
        errors.append("pair.not_object")

    pair_id = str(payload.get("pair_id") or "").strip()
    if not pair_id:
        errors.append("pair.pair_id_missing")

    source_tz = payload.get("source_tz")
    if not isinstance(source_tz, str) or len(_normalise_source(source_tz)) < MIN_SOURCE_CHARS:
        errors.append("pair.source_tz_missing_or_too_short")
        source_normalised = ""
    else:
        source_normalised = _normalise_source(source_tz)

    paramspec = payload.get("paramspec")
    if not isinstance(paramspec, dict):
        errors.append("pair.paramspec_missing")
        paramspec = {}
    else:
        try:
            ok, validation_errors = pair_validator(paramspec)
        except Exception:
            ok, validation_errors = False, ("pair.paramspec_gate_exception",)
        if not ok:
            errors.append("pair.paramspec_not_production_ready")
            errors.extend(f"paramspec.{code}" for code in validation_errors)

    split = str(payload.get("split") or "").strip()
    if split not in ALLOWED_SPLITS:
        errors.append("pair.split_invalid")

    leakage_group_value = str(payload.get("leakage_group") or "").strip()
    if not leakage_group_value:
        errors.append("pair.leakage_group_missing")
    leakage_group = (
        _hash_bytes(leakage_group_value.casefold().encode("utf-8"))
        if leakage_group_value else ""
    )

    review = payload.get("review")
    if not isinstance(review, dict) or review.get("status") != "approved":
        errors.append("pair.review_not_approved")
    else:
        if not str(review.get("reviewer_id") or "").strip():
            errors.append("pair.reviewer_id_missing")
        if not str(review.get("reviewed_at") or "").strip():
            errors.append("pair.reviewed_at_missing")

    provenance = payload.get("provenance")
    source_id = ""
    if not isinstance(provenance, dict):
        errors.append("pair.provenance_missing")
    else:
        source_id = str(provenance.get("source_id") or "").strip().casefold()
        if not source_id:
            errors.append("pair.source_id_missing")
        source_type = str(provenance.get("source_type") or "").strip().casefold()
        if source_type in PROHIBITED_SOURCE_TYPES:
            errors.append("pair.source_type_prohibited")
        elif source_type not in ALLOWED_SOURCE_TYPES:
            errors.append("pair.source_type_not_allowed")

        rights_record = provenance.get("rights_record")
        if not isinstance(rights_record, dict):
            errors.append("pair.rights_record_missing")
        else:
            if not str(rights_record.get("record_id") or "").strip():
                errors.append("pair.rights_record_id_missing")
            if str(rights_record.get("source_id") or "").strip().casefold() != source_id:
                errors.append("pair.rights_source_id_mismatch")
            if str(rights_record.get("source_type") or "").strip().casefold() != source_type:
                errors.append("pair.rights_source_type_mismatch")
            if rights_record.get("basis") not in ALLOWED_RIGHTS_BASES:
                errors.append("pair.rights_basis_not_allowed")
            if rights_record.get("scope") != TRAINING_RIGHTS_SCOPE:
                errors.append("pair.rights_scope_not_allowed")
            if not str(rights_record.get("verified_by") or "").strip():
                errors.append("pair.rights_verifier_missing")
            if not str(rights_record.get("verified_at") or "").strip():
                errors.append("pair.rights_verified_at_missing")
            document_sha256 = str(rights_record.get("document_sha256") or "")
            if re.fullmatch(r"[0-9a-f]{64}", document_sha256.casefold()) is None:
                errors.append("pair.rights_document_hash_invalid")

    source_hash = _hash_bytes(source_normalised.encode("utf-8")) if source_normalised else ""
    paramspec_hash = _hash_json(paramspec) if paramspec else ""
    pair_hash = _hash_json([source_hash, paramspec_hash]) if source_hash and paramspec_hash else ""
    return PairInspection(
        path=path.relative_to(root).as_posix(),
        pair_id=pair_id,
        split=split,
        leakage_group=leakage_group,
        source_id=source_id,
        source_hash=source_hash,
        paramspec_hash=paramspec_hash,
        pair_hash=pair_hash,
        errors=tuple(dict.fromkeys(errors)),
    )


def _duplicate_groups(
    inspections: Iterable[PairInspection], attribute: str
) -> list[dict[str, Any]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for item in inspections:
        value = getattr(item, attribute)
        if value:
            groups[value].append(item.pair_id or item.path)
    return [
        {
            "fingerprint": value,
            "count": len(members),
            "member_fingerprints": sorted(
                _hash_bytes(member.encode("utf-8")) for member in members
            ),
        }
        for value, members in sorted(groups.items())
        if len(members) > 1
    ]


def _duplicate_member_keys(
    inspections: Iterable[PairInspection], attribute: str
) -> set[str]:
    groups: dict[str, list[str]] = defaultdict(list)
    for item in inspections:
        value = getattr(item, attribute)
        if value:
            groups[value].append(item.pair_id or item.path)
    return {
        member
        for members in groups.values()
        for member in sorted(members)[1:]
    }


def _split_leakage(inspections: Iterable[PairInspection]) -> list[dict[str, Any]]:
    leaks: list[dict[str, Any]] = []
    for attribute in (
        "source_id", "source_hash", "paramspec_hash", "pair_hash", "leakage_group"
    ):
        groups: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
        for item in inspections:
            value = getattr(item, attribute)
            if value and item.split in ALLOWED_SPLITS:
                groups[value][item.split].append(item.pair_id or item.path)
        for value, by_split in sorted(groups.items()):
            if len(by_split) > 1:
                leaks.append({
                    "kind": attribute,
                    "fingerprint": (
                        _hash_bytes(value.encode("utf-8"))
                        if attribute == "source_id" else value
                    ),
                    "splits": {
                        split: {
                            "count": len(members),
                            "member_fingerprints": sorted(
                                _hash_bytes(member.encode("utf-8")) for member in members
                            ),
                        }
                        for split, members in sorted(by_split.items())
                    },
                })
    return leaks


def _plateau_report(evidence: Mapping[str, Any] | None) -> dict[str, Any]:
    prompt_rag = evidence.get("prompt_rag") if isinstance(evidence, Mapping) else None
    runs = prompt_rag.get("runs") if isinstance(prompt_rag, Mapping) else None
    benchmark_frozen = bool(
        isinstance(prompt_rag, Mapping) and prompt_rag.get("benchmark_frozen") is True
    )
    benchmark_excluded = bool(
        isinstance(prompt_rag, Mapping)
        and prompt_rag.get("benchmark_excluded_from_training_and_rag") is True
    )
    if not isinstance(runs, list):
        runs = []
    safe_runs: list[dict[str, Any]] = []
    for run in runs:
        if not isinstance(run, Mapping):
            continue
        try:
            cases = int(run.get("cases", 0))
            metric = float(run.get("paramspec_field_accuracy"))
        except (TypeError, ValueError):
            continue
        if not 0.0 <= metric <= 1.0:
            continue
        safe_runs.append({
            "run_id": str(run.get("run_id") or ""),
            "benchmark_id": str(run.get("benchmark_id") or ""),
            "benchmark_sha256": str(run.get("benchmark_sha256") or ""),
            "prompt_version": str(run.get("prompt_version") or ""),
            "rag_version": str(run.get("rag_version") or ""),
            "cases": cases,
            "paramspec_field_accuracy": metric,
        })

    window = safe_runs[-PLATEAU_MIN_RUNS:]
    same_frozen_benchmark = bool(window) and len({
        (run["benchmark_id"], run["benchmark_sha256"]) for run in window
    }) == 1 and all(run["benchmark_id"] and run["benchmark_sha256"] for run in window)
    enough_cases = bool(window) and all(run["cases"] >= PLATEAU_MIN_CASES for run in window)
    complete_run_identity = bool(window) and all(
        run["run_id"] and run["prompt_version"] and run["rag_version"] for run in window
    ) and len({run["run_id"] for run in window}) == len(window)
    deltas = [
        round(window[index]["paramspec_field_accuracy"]
              - window[index - 1]["paramspec_field_accuracy"], 8)
        for index in range(1, len(window))
    ]
    plateau = (
        len(window) == PLATEAU_MIN_RUNS
        and same_frozen_benchmark
        and benchmark_frozen
        and benchmark_excluded
        and enough_cases
        and complete_run_identity
        and all(abs(delta) <= PLATEAU_MAX_STEP_DELTA for delta in deltas)
    )
    return {
        "ok": plateau,
        "required_runs": PLATEAU_MIN_RUNS,
        "minimum_cases_per_run": PLATEAU_MIN_CASES,
        "maximum_absolute_step_delta": PLATEAU_MAX_STEP_DELTA,
        "eligible_runs": len(safe_runs),
        "window": window,
        "step_deltas": deltas,
        "same_frozen_benchmark": same_frozen_benchmark,
        "benchmark_frozen": benchmark_frozen,
        "benchmark_excluded_from_training_and_rag": benchmark_excluded,
        "complete_run_identity": complete_run_identity,
    }


def evaluate_ab_result(result: Mapping[str, Any] | None) -> dict[str, Any]:
    """Apply the pre-registered A/B rule; missing evidence always rejects."""
    reasons: list[str] = []
    if not isinstance(result, Mapping):
        reasons.append("ab.result_missing")
        return {"decision": "not_run", "adopt": False, "reasons": reasons}

    def interval(name: str) -> tuple[float, float] | None:
        value = result.get(name)
        if not isinstance(value, list) or len(value) != 2:
            return None
        if any(
            isinstance(item, bool) or not isinstance(item, (int, float))
            for item in value
        ):
            return None
        lower, upper = float(value[0]), float(value[1])
        if not math.isfinite(lower) or not math.isfinite(upper) or lower > upper:
            return None
        return lower, upper

    cases_value = result.get("cases_per_arm")
    cases = cases_value if isinstance(cases_value, int) and not isinstance(cases_value, bool) else 0
    primary = interval("primary_delta_ci95")
    production = interval("production_gate_pass_rate_delta_ci95")
    invalid = interval("invalid_output_rate_delta_ci95")
    if result.get("rule_version") != AB_DECISION_RULE["rule_version"]:
        reasons.append("ab.rule_version_mismatch")
    if result.get("design") != AB_DECISION_RULE["design"] or result.get("paired") is not True:
        reasons.append("ab.design_not_paired_frozen")
    if result.get("holdout_frozen") is not True:
        reasons.append("ab.holdout_not_frozen")
    if result.get("holdout_excluded_from_training_and_rag") is not True:
        reasons.append("ab.holdout_leakage_not_excluded")
    confidence = result.get("confidence_level")
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not math.isfinite(float(confidence))
        or float(confidence) != AB_DECISION_RULE["confidence_level"]
    ):
        reasons.append("ab.confidence_policy_mismatch")
    holdout_sha256 = str(result.get("holdout_sha256") or "")
    if re.fullmatch(r"[0-9a-f]{64}", holdout_sha256.casefold()) is None:
        reasons.append("ab.holdout_hash_invalid")
    if cases < AB_DECISION_RULE["min_cases_per_arm"]:
        reasons.append("ab.insufficient_cases")
    if primary is None:
        reasons.append("ab.primary_interval_invalid")
    elif primary[0] < AB_DECISION_RULE["min_primary_delta"]:
        reasons.append("ab.primary_gain_not_significant")
    if production is None:
        reasons.append("ab.production_interval_invalid")
    elif production[0] < 0.0:
        reasons.append("ab.production_gate_regression")
    if invalid is None:
        reasons.append("ab.invalid_output_interval_invalid")
    elif invalid[1] > 0.0:
        reasons.append("ab.invalid_output_regression")
    return {
        "decision": "adopt" if not reasons else "reject",
        "adopt": not reasons,
        "reasons": reasons,
    }


def _read_evidence(path: Path | None) -> tuple[dict[str, Any] | None, list[str]]:
    if path is None or not path.is_file():
        return None, ["prompt_rag.evidence_missing"]
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, ["prompt_rag.evidence_invalid_json"]
    if not isinstance(value, dict):
        return None, ["prompt_rag.evidence_not_object"]
    return value, []


def evaluate_readiness(
    dataset_dir: Path,
    *,
    evidence_path: Path | None = None,
    pair_validator: PairValidator = _default_pair_validator,
) -> dict[str, Any]:
    """Return a privacy-safe, machine-readable readiness decision."""
    files = sorted(dataset_dir.glob("*.json")) if dataset_dir.is_dir() else []
    inspections = [
        _inspect_pair(path, dataset_dir, pair_validator=pair_validator) for path in files
    ]
    quality_ok = [item for item in inspections if item.quality_ok]
    error_counts = Counter(error for item in inspections for error in item.errors)

    duplicate_pair_ids = _duplicate_groups(inspections, "pair_id")
    duplicate_sources = _duplicate_groups(inspections, "source_hash")
    duplicate_paramspecs = _duplicate_groups(inspections, "paramspec_hash")
    duplicate_pairs = _duplicate_groups(inspections, "pair_hash")
    duplicate_members = set().union(
        _duplicate_member_keys(quality_ok, "pair_hash"),
        _duplicate_member_keys(quality_ok, "source_hash"),
        _duplicate_member_keys(quality_ok, "paramspec_hash"),
    )
    clean_unique = [
        item for item in quality_ok if (item.pair_id or item.path) not in duplicate_members
    ]

    split_counts = Counter(item.split for item in clean_unique if item.split in ALLOWED_SPLITS)
    split_ratios = {
        split: (split_counts[split] / len(clean_unique) if clean_unique else 0.0)
        for split in ALLOWED_SPLITS
    }
    split_ratio_ok = bool(clean_unique) and all(
        low <= split_ratios[split] <= high
        for split, (low, high) in SPLIT_RATIO_LIMITS.items()
    )
    leakage = _split_leakage(inspections)

    evidence, evidence_errors = _read_evidence(evidence_path)
    plateau = _plateau_report(evidence)
    ab_result = evaluate_ab_result(evidence.get("ab_result") if evidence else None)

    blockers: list[dict[str, Any]] = []
    if len(clean_unique) < MIN_CLEAN_PAIRS:
        blockers.append({
            "code": "dataset.minimum_clean_pairs_not_met",
            "required": MIN_CLEAN_PAIRS,
            "actual": len(clean_unique),
            "missing": MIN_CLEAN_PAIRS - len(clean_unique),
        })
    if error_counts:
        blockers.append({"code": "dataset.quality_failures", "counts": dict(sorted(error_counts.items()))})
    if duplicate_pair_ids or duplicate_sources or duplicate_paramspecs or duplicate_pairs:
        blockers.append({"code": "dataset.duplicates_present"})
    if not split_ratio_ok:
        blockers.append({"code": "dataset.split_distribution_invalid"})
    if leakage:
        blockers.append({"code": "dataset.split_leakage_detected", "groups": len(leakage)})
    for code in evidence_errors:
        blockers.append({"code": code})
    if not plateau["ok"]:
        blockers.append({"code": "prompt_rag.plateau_not_proven"})

    ready = not blockers
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "mode": "dry-run",
        "ready_for_finetune_experiment": ready,
        "decision": "eligible_for_offline_experiment" if ready else "stay_in_backlog",
        "dataset": {
            "path": str(dataset_dir),
            "files_seen": len(files),
            "quality_passed": len(quality_ok),
            "clean_unique_pairs": len(clean_unique),
            "minimum_clean_pairs": MIN_CLEAN_PAIRS,
            "quality_error_counts": dict(sorted(error_counts.items())),
        },
        "provenance_policy": {
            "allowed_source_types": sorted(ALLOWED_SOURCE_TYPES),
            "prohibited_source_types": sorted(PROHIBITED_SOURCE_TYPES),
            "allowed_rights_bases": sorted(ALLOWED_RIGHTS_BASES),
            "required_scope": TRAINING_RIGHTS_SCOPE,
            "requires_structured_rights_record": True,
            "trust_limit": (
                "metadata_consistency_only; legal authenticity and reviewer authority "
                "require independent governance audit"
            ),
        },
        "dedup": {
            "ok": not (duplicate_pair_ids or duplicate_sources or duplicate_paramspecs or duplicate_pairs),
            "duplicate_pair_ids": duplicate_pair_ids,
            "duplicate_sources": duplicate_sources,
            "duplicate_paramspecs": duplicate_paramspecs,
            "duplicate_pairs": duplicate_pairs,
        },
        "split": {
            "ok": split_ratio_ok,
            "allowed": list(ALLOWED_SPLITS),
            "ratio_limits": {key: list(value) for key, value in SPLIT_RATIO_LIMITS.items()},
            "counts": {split: split_counts[split] for split in ALLOWED_SPLITS},
            "ratios": split_ratios,
        },
        "leakage": {"ok": not leakage, "groups": leakage},
        "prompt_rag_plateau": plateau,
        "ab_decision_rule": AB_DECISION_RULE,
        "ab_result": ab_result,
        "blockers": blockers,
    }


def write_report(report: Mapping[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
