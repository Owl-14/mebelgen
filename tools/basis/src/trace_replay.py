"""Offline replay and comparison for versioned Studio AI trace datasets.

The dataset stores provider outputs, never provider credentials or raw user
payloads. Replay starts after the paid/model boundary and sends the recorded
ParamSpec or typed operations through the same reducer and production gate as
Studio.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from pydantic import ValidationError

from .edit_operations import EditApplicationError, apply_edit_operations
from .production_gate import GateDecision, evaluate_production_gate
from .spec_chat import spec_diff
from .studio_graph import spec_revision


DATASET_SCHEMA = "trace-eval-v1"
DEFAULT_DATASET = Path(__file__).resolve().parent.parent / "qa" / "trace_eval" / "v1" / "scenarios.json"


class TraceReplayError(ValueError):
    """The replay dataset is malformed or does not match its expectations."""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _load_ref(dataset_dir: Path, ref: str) -> dict[str, Any]:
    path = (dataset_dir / ref).resolve()
    if dataset_dir.resolve() not in path.parents:
        raise TraceReplayError(f"dataset reference escapes its directory: {ref}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TraceReplayError(f"dataset reference must contain an object: {ref}")
    return value


def _nodes(case: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in case.get("recorded", {}).get("nodes", []):
        name = str(item.get("name") or "")
        if not name or name in result:
            raise TraceReplayError(f"{case.get('id')}: node names must be unique and non-empty")
        result[name] = item.get("output")
    return result


def _source(case: Mapping[str, Any], dataset_dir: Path) -> dict[str, Any] | None:
    ref = case.get("source_ref")
    return _load_ref(dataset_dir, str(ref)) if ref else None


def _context_for(spec: Mapping[str, Any]) -> dict[str, Any]:
    decision = evaluate_production_gate(spec)
    project = decision.project or {}
    errors = {
        step.name: [issue.detail for issue in step.issues if issue.severity == "error"]
        for step in decision.report.checks
        if any(issue.severity == "error" for issue in step.issues)
    }
    return {
        "panels": project.get("panels") or [],
        "check_errors": errors or "нет — все проверки зелёные",
        "base_unresolved": ", ".join(
            slot for slot, value in decision.material_refs.items()
            if isinstance(value, Mapping) and not value.get("resolved")
        ) or "все позиции подобраны",
    }


def _gate_output(decision: GateDecision) -> dict[str, Any]:
    project = decision.project or {}
    drilling_count: int | None = None
    if decision.project is not None:
        from .hardware import compute_drilling

        drilling_count = len(compute_drilling(project))
    return {
        "gate": {
            "ok": decision.report.ok,
            "statuses": {step.name: step.status for step in decision.report.checks},
            "error_codes": sorted(issue.code for issue in decision.report.errors),
        },
        "part_count": len(project.get("panels") or []) if decision.project is not None else None,
        "drilling_count": drilling_count,
    }


def _subset_errors(expected: Any, actual: Any, path: str = "expected") -> list[str]:
    if isinstance(expected, Mapping):
        if not isinstance(actual, Mapping):
            return [f"{path}: expected object, got {type(actual).__name__}"]
        errors: list[str] = []
        for key, value in expected.items():
            if key not in actual:
                errors.append(f"{path}.{key}: missing")
            else:
                errors.extend(_subset_errors(value, actual[key], f"{path}.{key}"))
        return errors
    if expected != actual:
        return [f"{path}: expected {expected!r}, got {actual!r}"]
    return []


def _deep_merge(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(base))
    for key, value in overlay.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _apply(spec: dict[str, Any], operations: Any) -> dict[str, Any]:
    return apply_edit_operations(spec, operations, _context_for(spec))


def replay_case(
    case: Mapping[str, Any],
    dataset_dir: Path,
    output_profiles: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Replay one saved model boundary and return deterministic evidence."""

    case_id = str(case.get("id") or "")
    route = str(case.get("route") or "")
    nodes = _nodes(case)
    source = _source(case, dataset_dir)
    actual: dict[str, Any] = {
        "status": "rejected",
        "diff": [],
        "part_count": None,
        "drilling_count": None,
        "gate": None,
    }
    try:
        if route in {"create_paramspec", "vision_create_paramspec"}:
            output = nodes.get("create_paramspec")
            if not isinstance(output, Mapping) or not output.get("spec_ref"):
                raise TraceReplayError(f"{case_id}: create_paramspec.spec_ref is required")
            candidate = _load_ref(dataset_dir, str(output["spec_ref"]))
            decision = evaluate_production_gate(candidate)
            actual.update(_gate_output(decision))
            actual.update(
                status="accepted" if decision.report.ok else "rejected",
                diff=[f"created {candidate.get('project_name', 'ParamSpec')}"],
                spec_hash=spec_revision(candidate),
            )
        elif route in {"edit_operations", "diagnosis"}:
            if source is None:
                raise TraceReplayError(f"{case_id}: source_ref is required")
            output = nodes.get(route)
            if not isinstance(output, Mapping) or not isinstance(output.get("operations"), list):
                raise TraceReplayError(f"{case_id}: {route}.operations is required")
            reduced = _apply(source, output["operations"])
            decision = evaluate_production_gate(reduced)
            actual.update(_gate_output(decision))
            actual.update(
                status=("replied" if route == "diagnosis" else
                        "accepted" if decision.report.ok else "rejected"),
                diff=spec_diff(source, reduced["spec"]),
                replies=reduced["replies"],
                spec_hash=spec_revision(reduced["spec"]),
            )
        elif route == "refusal":
            output = nodes.get("policy")
            code = output.get("code") if isinstance(output, Mapping) else None
            if not code:
                raise TraceReplayError(f"{case_id}: policy.code is required")
            actual.update(code=code, source_unchanged=True)
        elif route == "revision_conflict":
            if source is None:
                raise TraceReplayError(f"{case_id}: source_ref is required")
            output = nodes.get("revision_guard")
            requested = output.get("expected_revision") if isinstance(output, Mapping) else None
            current = spec_revision(source)
            if requested == current:
                actual.update(status="accepted", code=None)
            else:
                actual.update(code="revision.conflict", current_revision=current)
            actual["source_unchanged"] = True
        elif route == "repair_loop":
            if source is None:
                raise TraceReplayError(f"{case_id}: source_ref is required")
            output = nodes.get("repair")
            batches = output.get("operation_batches") if isinstance(output, Mapping) else None
            if not isinstance(batches, list) or not batches:
                raise TraceReplayError(f"{case_id}: repair.operation_batches is required")
            working = copy.deepcopy(source)
            outcomes: list[bool] = []
            decision: GateDecision | None = None
            attempts = 0
            for attempts, operations in enumerate(batches[:3], start=1):
                reduced = _apply(working, operations)
                working = reduced["spec"]
                decision = evaluate_production_gate(reduced)
                outcomes.append(decision.report.ok)
                if decision.report.ok:
                    break
            assert decision is not None
            actual.update(_gate_output(decision))
            actual.update(
                status="accepted" if decision.report.ok else "rejected",
                diff=spec_diff(source, working),
                repair_attempts=attempts,
                intermediate_gate_ok=outcomes,
                spec_hash=spec_revision(working),
            )
        else:
            raise TraceReplayError(f"{case_id}: unsupported route {route!r}")
    except (EditApplicationError, ValidationError) as error:
        actual.update(code="operations.invalid_result", error_type=type(error).__name__)

    expectation = case.get("expected", {})
    expected_nodes = expectation.get("node_outputs", {})
    profile_name = expectation.get("profile")
    profile = (output_profiles or {}).get(profile_name, {}) if profile_name else {}
    if profile_name and not profile:
        raise TraceReplayError(f"{case_id}: unknown output profile {profile_name!r}")
    expected_output = _deep_merge(profile, expectation.get("output", {}))
    mismatches = _subset_errors(expected_nodes, nodes, "expected.node_outputs")
    mismatches.extend(_subset_errors(expected_output, actual, "expected.output"))
    return {
        "id": case_id,
        "tags": list(case.get("tags") or []),
        "prompt_id": case.get("recorded", {}).get("prompt_id"),
        "prompt_version": case.get("recorded", {}).get("prompt_version"),
        "model": case.get("recorded", {}).get("model"),
        "node_outputs_digest": _digest(nodes),
        "output": actual,
        "output_digest": _digest(actual),
        "ok": not mismatches,
        "mismatches": mismatches,
    }


def run_dataset(path: Path | str = DEFAULT_DATASET) -> dict[str, Any]:
    dataset_path = Path(path).resolve()
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    if dataset.get("schema_version") != DATASET_SCHEMA:
        raise TraceReplayError(f"unsupported dataset schema: {dataset.get('schema_version')!r}")
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases:
        raise TraceReplayError("dataset cases must be a non-empty list")
    ids = [case.get("id") for case in cases]
    if any(not value for value in ids) or len(ids) != len(set(ids)):
        raise TraceReplayError("dataset case ids must be unique and non-empty")
    profiles = dataset.get("output_profiles") or {}
    if not isinstance(profiles, Mapping):
        raise TraceReplayError("dataset output_profiles must be an object")
    results = [replay_case(case, dataset_path.parent, profiles) for case in cases]
    return {
        "schema_version": DATASET_SCHEMA,
        "dataset_version": dataset.get("dataset_version"),
        "dataset_digest": _digest(dataset),
        "case_count": len(results),
        "passed": sum(case["ok"] for case in results),
        "failed": sum(not case["ok"] for case in results),
        "profiles": sorted({
            f"{case['prompt_id']}@{case['prompt_version']} / {case['model']}"
            for case in results
        }),
        "cases": results,
    }


def compare_reports(baseline: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Compare deterministic case outputs across prompt/model report profiles."""

    left = {case["id"]: case for case in baseline.get("cases", [])}
    right = {case["id"]: case for case in candidate.get("cases", [])}
    changes: list[dict[str, Any]] = []
    for case_id in sorted(set(left) | set(right)):
        before = left.get(case_id)
        after = right.get(case_id)
        if before is None or after is None or before.get("output_digest") != after.get("output_digest"):
            changes.append({
                "id": case_id,
                "baseline": before.get("output") if before else None,
                "candidate": after.get("output") if after else None,
            })
    return {
        "baseline_profiles": list(baseline.get("profiles") or []),
        "candidate_profiles": list(candidate.get("profiles") or []),
        "comparable_cases": len(set(left) & set(right)),
        "changed": changes,
        "ok": not changes and set(left) == set(right),
    }


def write_report(report: Mapping[str, Any], path: Path | str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
