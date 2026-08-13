"""Offline replay and comparison for versioned Studio AI trace datasets.

The dataset stores provider outputs, never provider credentials or raw user
payloads. Replay makes no provider calls, but it does execute the production
router and request policy, validates recorded outputs with production
capability schemas, and sends accepted ParamSpec or typed operations through
the same reducer and production gate as Studio.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator
from pydantic import ValidationError

from .edit_operations import EditApplicationError, apply_edit_operations
from .production_gate import GateDecision, evaluate_production_gate
from .prompt_registry import (
    capability_schema,
    classify_intent,
    evaluate_request_policy,
    prompt_manifest,
)
from .spec_chat import spec_diff
from .studio_graph import MAX_REPAIR_ITERATIONS, spec_revision


DATASET_SCHEMA = "trace-eval-v1"
CASE_SCHEMA = "trace-eval-case-v1"
DEFAULT_DATASET = Path(__file__).resolve().parent.parent / "qa" / "trace_eval" / "v1" / "scenarios.json"
_APPROVED_VISION_ANNOTATIONS = {
    "cabinet-reference-v1": "25610da2655c0b34cb6d583496b1fb9e75ab66338d17f67f3e7509bb7ef6ebb0",
}


class TraceReplayError(ValueError):
    """The replay dataset is malformed or does not match its expectations."""


class DecisionEnvelopeError(TraceReplayError):
    """A saved decision failed policy checks and must not reach the reducer."""


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


def _capability_node(name: str) -> str | None:
    if name == "intent.classify":
        return "intent_routing"
    if name.startswith("repair."):
        return "repair"
    if name in prompt_manifest():
        return name
    return None


def _materialize_payload(name: str, output: Any, dataset_dir: Path) -> Any:
    if name == "create_paramspec" and isinstance(output, Mapping) and output.get("spec_ref"):
        return {
            "reply": output.get("reply"),
            "spec": _load_ref(dataset_dir, str(output["spec_ref"])),
        }
    return output


def _validate_capabilities(
    case: Mapping[str, Any], nodes: Mapping[str, Any], dataset_dir: Path,
) -> tuple[dict[str, Any], list[str]]:
    materialized: dict[str, Any] = {}
    errors: list[str] = []
    for name, output in nodes.items():
        payload = _materialize_payload(name, output, dataset_dir)
        materialized[name] = payload
        capability = _capability_node(name)
        if capability is None:
            continue
        for error in Draft202012Validator(capability_schema(capability)).iter_errors(payload):
            location = ".".join(str(part) for part in error.absolute_path)
            errors.append(f"capability.{name}{'.' + location if location else ''}: {error.message}")
    return materialized, errors


def _operation_types(nodes: Mapping[str, Any]) -> list[str]:
    result: list[str] = []
    for output in nodes.values():
        if not isinstance(output, Mapping):
            continue
        for operations in [output.get("operations")]:
            if not isinstance(operations, list):
                continue
            for operation in operations:
                if isinstance(operation, Mapping) and operation.get("op"):
                    result.append(str(operation["op"]))
    return sorted(set(result))


def _decision_evidence(
    case: Mapping[str, Any],
    nodes: Mapping[str, Any],
    materialized_nodes: Mapping[str, Any],
    provider_policies: Mapping[str, Any],
    production_route: str | None,
    request_policy: Mapping[str, Any],
    vision_semantics: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Validate the saved AI decision before replaying its operations.

    The envelope binds a command to a router result, current prompt manifest,
    an allowlisted offline provider/model policy, the vision boundary and the
    operation capability actually present in saved node outputs.
    """

    recorded = case.get("recorded") or {}
    envelope = recorded.get("decision") or {}
    command = str(case.get("command") or "")
    command_hash = hashlib.sha256(command.encode("utf-8")).hexdigest()
    router = nodes.get("intent.classify")
    recorded_route = router.get("intent") if isinstance(router, Mapping) else None
    prompt_node = envelope.get("prompt_node")
    manifest = prompt_manifest().get(str(prompt_node)) if prompt_node else None
    policy_name = envelope.get("provider_policy")
    policy = provider_policies.get(str(policy_name)) if policy_name else None
    provider = recorded.get("provider")
    model = recorded.get("model")
    provider_allowed = bool(
        isinstance(policy, Mapping)
        and provider in (policy.get("providers") or [])
        and any(
            str(model or "").startswith(str(prefix))
            for prefix in policy.get("model_prefixes") or []
        )
    )
    prompt_required = (
        not isinstance(policy, Mapping) or policy.get("prompt_required", True)
    )
    prompt_manifest_match = bool(
        (manifest
         and recorded.get("prompt_id") == manifest.get("prompt_id")
         and recorded.get("prompt_version") == manifest.get("prompt_version"))
        or (not prompt_required and prompt_node is None
            and recorded.get("prompt_id") is None
            and recorded.get("prompt_version") is None)
    )
    evidence = {
        "command_hash": command_hash,
        "command_class": envelope.get("command_class"),
        "route": production_route,
        "recorded_route": recorded_route,
        "prompt_node": prompt_node,
        "prompt_id": recorded.get("prompt_id"),
        "prompt_version": recorded.get("prompt_version"),
        "provider": provider,
        "model": model,
        "provider_policy": policy_name,
        "request_policy": copy.deepcopy(dict(request_policy)),
        "vision_stage_present": "vision_facts" in nodes,
        "vision_facts_digest": (
            _digest(materialized_nodes["vision_facts"])
            if "vision_facts" in materialized_nodes else None
        ),
        "create_result_digest": (
            _digest(materialized_nodes["create_paramspec"])
            if "vision_facts" in materialized_nodes
            and "create_paramspec" in materialized_nodes else None
        ),
        "vision_semantics": copy.deepcopy(dict(vision_semantics)),
        "operation_types": _operation_types(nodes),
        "checks": {
            "command_hash_match": envelope.get("command_hash") == command_hash,
            "recorded_router_match": recorded_route == production_route,
            "prompt_manifest_match": prompt_manifest_match,
            "provider_policy_match": provider_allowed,
        },
    }
    errors: list[str] = []
    expected = case.get("expected", {}).get("decision")
    if not isinstance(expected, Mapping):
        errors.append("expected.decision: missing decision contract")
    else:
        errors.extend(_subset_errors(expected, evidence, "expected.decision"))
    for name, passed in evidence["checks"].items():
        if not passed:
            errors.append(f"decision.checks.{name}: failed")
    return evidence, errors


def _vision_semantic_evidence(
    case: Mapping[str, Any], nodes: Mapping[str, Any], dataset_dir: Path,
) -> tuple[dict[str, Any], list[str]]:
    if "vision_facts" not in nodes:
        return {"status": "not_applicable", "annotation_id": None}, []
    ref = case.get("vision_annotation_ref")
    if not ref:
        return {"status": "unverified", "annotation_id": None}, []
    annotation = _load_ref(dataset_dir, str(ref))
    annotation_id = str(annotation.get("annotation_id") or "")
    approved_digest = _APPROVED_VISION_ANNOTATIONS.get(annotation_id)
    evidence = {"status": "verified", "annotation_id": annotation_id}
    errors: list[str] = []
    if annotation.get("status") != "approved" or _digest(annotation) != approved_digest:
        errors.append("decision.vision_semantics: annotation provenance is not approved")
    expected = annotation.get("expected") or {}
    vision = nodes.get("vision_facts") or {}
    if not isinstance(expected, Mapping) or vision.get("reply") != expected.get("reply"):
        errors.append("decision.vision_semantics: recorded facts differ from approved annotation")
    provenance = annotation.get("provenance") or {}
    create = nodes.get("create_paramspec") or {}
    if (
        not isinstance(provenance, Mapping)
        or provenance.get("kind") != "human-reviewed-fixture"
        or provenance.get("source_ref") != create.get("spec_ref")
    ):
        errors.append("decision.vision_semantics: annotation is not linked to create fixture")
    return evidence, errors


def _source(case: Mapping[str, Any], dataset_dir: Path) -> dict[str, Any] | None:
    ref = case.get("source_ref")
    return _load_ref(dataset_dir, str(ref)) if ref else None


def _policy_denial_evidence(
    case: Mapping[str, Any], request_policy: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    recorded = case.get("recorded") or {}
    envelope = recorded.get("decision") or {}
    command = str(case.get("command") or "")
    command_hash = hashlib.sha256(command.encode("utf-8")).hexdigest()
    evidence = {
        "command_hash": command_hash,
        "command_class": envelope.get("command_class"),
        "route": None,
        "recorded_route": None,
        "prompt_node": None,
        "prompt_id": None,
        "prompt_version": None,
        "provider": None,
        "model": None,
        "provider_policy": None,
        "request_policy": copy.deepcopy(dict(request_policy)),
        "vision_stage_present": False,
        "vision_facts_digest": None,
        "create_result_digest": None,
        "vision_semantics": {"status": "not_applicable", "annotation_id": None},
        "operation_types": [],
        "checks": {"command_hash_match": envelope.get("command_hash") == command_hash},
    }
    errors: list[str] = []
    expected = case.get("expected", {}).get("decision")
    if not isinstance(expected, Mapping):
        errors.append("expected.decision: missing decision contract")
    else:
        errors.extend(_subset_errors(expected, evidence, "expected.decision"))
    if not evidence["checks"]["command_hash_match"]:
        errors.append("decision.checks.command_hash_match: failed")
    return evidence, errors


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
    provider_policies: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Replay one saved model boundary and return deterministic evidence."""

    case_id = str(case.get("id") or "")
    route = str(case.get("route") or "")
    command = str(case.get("command") or "")
    # This is intentionally the first production decision. A denied request
    # never reaches router, provider metadata validation or saved replay.
    request_policy = evaluate_request_policy(command)
    if not request_policy["allowed"]:
        nodes: dict[str, Any] = {}
        source = None
        materialized_nodes: dict[str, Any] = {}
        has_images = False
        decision_evidence, decision_mismatches = _policy_denial_evidence(
            case, request_policy
        )
    else:
        nodes = _nodes(case)
        source = _source(case, dataset_dir)
        materialized_nodes, capability_errors = _validate_capabilities(case, nodes, dataset_dir)
        has_images = "vision_facts" in nodes
        production_route = classify_intent(command, source, {}, has_images=has_images)
        vision_semantics, semantic_errors = _vision_semantic_evidence(
            case, nodes, dataset_dir
        )
        decision_evidence, decision_mismatches = _decision_evidence(
            case, nodes, materialized_nodes, provider_policies or {}, production_route,
            request_policy, vision_semantics,
        )
        decision_mismatches.extend(capability_errors)
        decision_mismatches.extend(semantic_errors)
    if has_images:
        links = case.get("recorded", {}).get("links") or []
        expected_link = {
            "from": "vision_facts",
            "to": "create_paramspec",
            "payload_digest": _digest(materialized_nodes.get("vision_facts")),
            "result_digest": _digest(materialized_nodes.get("create_paramspec")),
        }
        if expected_link not in links:
            decision_mismatches.append("decision.vision_create_link: missing or stale")
    actual: dict[str, Any] = {
        "status": "rejected",
        "diff": [],
        "part_count": None,
        "drilling_count": None,
        "gate": None,
    }
    try:
        if not request_policy["allowed"]:
            actual.update(code=request_policy["code"], source_unchanged=True)
            raise DecisionEnvelopeError(f"{case_id}: request rejected by production policy")
        if decision_mismatches:
            actual.update(code="decision.invalid_envelope")
            raise DecisionEnvelopeError(f"{case_id}: decision envelope rejected")
        if route in {"create_paramspec", "vision_create_paramspec"}:
            output = materialized_nodes.get("create_paramspec")
            if not isinstance(output, Mapping) or not isinstance(output.get("spec"), Mapping):
                raise TraceReplayError(f"{case_id}: create_paramspec.spec is required")
            candidate = copy.deepcopy(dict(output["spec"]))
            decision = evaluate_production_gate(candidate)
            actual.update(_gate_output(decision))
            actual.update(
                status="accepted" if decision.report.ok else "rejected",
                diff=[f"created {candidate.get('project_name', 'ParamSpec')}"],
                spec_hash=spec_revision(candidate),
            )
        elif route == "edit_operations":
            if source is None:
                raise TraceReplayError(f"{case_id}: source_ref is required")
            output = materialized_nodes.get(route)
            if not isinstance(output, Mapping) or not isinstance(output.get("operations"), list):
                raise TraceReplayError(f"{case_id}: {route}.operations is required")
            reduced = _apply(source, output["operations"])
            decision = evaluate_production_gate(reduced)
            actual.update(_gate_output(decision))
            actual.update(
                status="accepted" if decision.report.ok else "rejected",
                diff=spec_diff(source, reduced["spec"]),
                replies=reduced["replies"],
                spec_hash=spec_revision(reduced["spec"]),
            )
        elif route == "diagnosis":
            if source is None:
                raise TraceReplayError(f"{case_id}: source_ref is required")
            output = materialized_nodes.get("diagnosis")
            if not isinstance(output, Mapping):
                raise TraceReplayError(f"{case_id}: diagnosis payload is required")
            decision = evaluate_production_gate(source)
            actual.update(_gate_output(decision))
            actual.update(
                status="replied", diff=[], replies=[str(output.get("reply") or "")],
                spec_hash=spec_revision(source),
            )
        elif route == "refusal":
            raise TraceReplayError(f"{case_id}: refusal route requires a denied production policy")
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
            batches = [
                materialized_nodes[name].get("operations")
                for name in sorted(materialized_nodes)
                if name.startswith("repair.") and isinstance(materialized_nodes[name], Mapping)
            ]
            if not batches:
                raise TraceReplayError(f"{case_id}: repair attempts are required")
            working = copy.deepcopy(source)
            outcomes: list[bool] = []
            decision: GateDecision | None = None
            attempts = 0
            for attempts, operations in enumerate(batches[:MAX_REPAIR_ITERATIONS], start=1):
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
    except DecisionEnvelopeError:
        pass

    expectation = case.get("expected", {})
    expected_nodes = expectation.get("node_outputs", {})
    profile_name = expectation.get("profile")
    profile = (output_profiles or {}).get(profile_name, {}) if profile_name else {}
    if profile_name and not profile:
        raise TraceReplayError(f"{case_id}: unknown output profile {profile_name!r}")
    expected_output = _deep_merge(profile, expectation.get("output", {}))
    expected_status = str(expected_output.get("status") or "")
    candidate_status = str(actual.get("status") or "")
    evaluation_label = {
        "source": "MEB-151",
        "expected_status": expected_status,
        "candidate_status": candidate_status,
        "false_rejection": (
            expected_status in {"accepted", "replied"}
            and candidate_status == "rejected"
        ),
    }
    mismatches = list(decision_mismatches)
    mismatches.extend(_subset_errors(expected_nodes, nodes, "expected.node_outputs"))
    mismatches.extend(_subset_errors(expected_output, actual, "expected.output"))
    ok = not mismatches
    decision_digest = _digest(decision_evidence)
    node_outputs_digest = _digest(nodes)
    output_digest = _digest(actual)
    return {
        "schema_version": CASE_SCHEMA,
        "id": case_id,
        "tags": list(case.get("tags") or []),
        "prompt_id": case.get("recorded", {}).get("prompt_id"),
        "prompt_version": case.get("recorded", {}).get("prompt_version"),
        "model": case.get("recorded", {}).get("model"),
        "provider": case.get("recorded", {}).get("provider"),
        "decision": decision_evidence,
        "decision_digest": decision_digest,
        "node_outputs_digest": node_outputs_digest,
        "output": actual,
        "output_digest": output_digest,
        "evaluation_label": evaluation_label,
        "verdict_digest": _digest({
            "schema_version": CASE_SCHEMA,
            "decision": decision_digest,
            "node_outputs": node_outputs_digest,
            "output": output_digest,
            "evaluation_label": evaluation_label,
            "ok": ok,
        }),
        "ok": ok,
        "mismatches": mismatches,
    }


_PRIVATE_KEYS = {
    "authorization", "api_key", "apikey", "access_token", "refresh_token",
    "password", "secret", "system_prompt", "developer_prompt", "raw_prompt",
    "image_base64", "base64", "email", "phone",
}
_IDENTITY_KEYS = {
    "author", "responsible", "customer", "customer_name", "client",
    "client_name", "contact", "contact_name", "created_by", "approved_by",
    "reviewed_by", "full_name", "fio", "фио",
}
_RUSSIAN_FULL_NAME = re.compile(
    r"(?<![А-ЯЁа-яё-])[А-ЯЁ][а-яё]{2,}(?:-[А-ЯЁ][а-яё]{2,})?\s+"
    r"[А-ЯЁ][а-яё]{2,}(?:-[А-ЯЁ][а-яё]{2,})?(?![А-ЯЁа-яё-])"
)
_EMAIL = re.compile(r"(?<![\w.-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_PHONE = re.compile(r"(?<!\d)(?:\+?\d[\s().-]*){10,15}(?!\d)")
_SECRET = re.compile(r"(?:bearer\s+[A-Za-z0-9._~+/=-]{12,}|\bsk-[A-Za-z0-9_-]{12,})", re.I)
_BASE64 = re.compile(r"^[A-Za-z0-9+/]{40,}={0,2}$")


def _privacy_errors(value: Any, path: str = "dataset") -> list[str]:
    errors: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            child = f"{path}.{key}"
            lowered = str(key).lower()
            if lowered in _PRIVATE_KEYS:
                errors.append(f"{child}: private field is forbidden")
            if isinstance(item, str) and lowered in _IDENTITY_KEYS and item.strip():
                errors.append(f"{child}: identity field is forbidden")
            if (
                isinstance(item, str) and lowered == "project_name"
                and _RUSSIAN_FULL_NAME.search(item)
            ):
                errors.append(f"{child}: probable full name is forbidden")
            errors.extend(_privacy_errors(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            errors.extend(_privacy_errors(item, f"{path}[{index}]"))
    elif isinstance(value, str):
        compact = "".join(value.split())
        if re.fullmatch(r"[0-9a-fA-F]{40,}", compact):
            return errors
        if value.lower().startswith("data:") or ";base64," in value.lower():
            errors.append(f"{path}: inline data/base64 is forbidden")
        elif _EMAIL.search(value) or _PHONE.search(value):
            errors.append(f"{path}: PII is forbidden")
        elif _SECRET.search(value):
            errors.append(f"{path}: secret is forbidden")
        elif _BASE64.fullmatch(compact) and not re.fullmatch(r"[0-9a-fA-F]{40,}", compact):
            errors.append(f"{path}: encoded payload is forbidden")
    return errors


def _reachable_dataset(dataset: Mapping[str, Any], dataset_dir: Path) -> list[tuple[str, Any]]:
    reachable: list[tuple[str, Any]] = [("dataset", dataset)]
    seen: set[Path] = set()

    def visit(value: Any, location: str) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if str(key).endswith("_ref") and isinstance(item, str):
                    target = (dataset_dir / item).resolve()
                    if dataset_dir.resolve() not in target.parents:
                        raise TraceReplayError(f"dataset reference escapes its directory: {item}")
                    if target in seen:
                        continue
                    seen.add(target)
                    try:
                        loaded = json.loads(target.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError) as error:
                        raise TraceReplayError(f"unreadable dataset reference: {item}") from error
                    reachable.append((f"ref:{item}", loaded))
                    visit(loaded, f"ref:{item}")
                else:
                    visit(item, f"{location}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f"{location}[{index}]")

    visit(dataset, "dataset")
    return reachable


def _enforce_privacy(dataset: Mapping[str, Any], dataset_dir: Path) -> None:
    errors: list[str] = []
    for location, value in _reachable_dataset(dataset, dataset_dir):
        errors.extend(_privacy_errors(value, location))
    if errors:
        raise TraceReplayError("privacy scan failed: " + "; ".join(errors[:10]))


def run_dataset(path: Path | str = DEFAULT_DATASET) -> dict[str, Any]:
    dataset_path = Path(path).resolve()
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    _enforce_privacy(dataset, dataset_path.parent)
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
    policies = dataset.get("provider_policies") or {}
    if not isinstance(policies, Mapping) or not policies:
        raise TraceReplayError("dataset provider_policies must be a non-empty object")
    results = [replay_case(case, dataset_path.parent, profiles, policies) for case in cases]
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
        if before is None or after is None or before.get("verdict_digest") != after.get("verdict_digest"):
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
