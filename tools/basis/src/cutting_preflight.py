"""Shared Cutting test-order workflow used by offline and operator entrypoints."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .cloud_cutting import CuttingClient, CuttingError, sha256_file
from .material_link_contract import audit_sheet_link_result, plan_sheet_links

CONTRACT_VERSION = "cutting-public-openapi-2026-06-30"
_FIXTURE_KEYS = {
    "contractVersion", "scope", "approvedForLive", "order", "model", "cfrn",
    "cadModelMaterials", "confirmations", "cuttingMaterials", "cuttedMaterials",
    "expectedOrder",
}


@dataclass(frozen=True)
class LoadedCuttingFixture:
    path: Path
    data: Mapping[str, Any]
    fixture_sha256: str
    model_path: Path
    model_sha256: str


def _contains_synthetic_sentinel(value: Any) -> bool:
    if isinstance(value, str):
        folded = value.casefold()
        return "__fixture_" in folded or "synthetic" in folded or "offline-contract" in folded
    if isinstance(value, Mapping):
        return any(_contains_synthetic_sentinel(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_synthetic_sentinel(item) for item in value)
    return False


def load_cutting_fixture(
    path: str | Path,
    *,
    require_live_approval: bool,
    approved_fixture_sha256: str | None = None,
) -> LoadedCuttingFixture:
    fixture_path = Path(path).expanduser().resolve()
    raw = fixture_path.read_bytes()
    fixture_hash = hashlib.sha256(raw).hexdigest()
    if approved_fixture_sha256 is not None and fixture_hash != approved_fixture_sha256:
        raise ValueError("cutting.fixture.hash: fixture differs from the separately approved SHA-256")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, Mapping) or set(data) != _FIXTURE_KEYS:
        raise ValueError("Cutting fixture must contain exactly the approved contract fields")
    if data["contractVersion"] != CONTRACT_VERSION:
        raise ValueError("unsupported Cutting fixture contractVersion")
    model = data["model"]
    if not isinstance(model, Mapping) or set(model) != {"path", "sha256", "count", "cutModels"}:
        raise ValueError("fixture.model must contain path, sha256, count and cutModels")
    model_path = (fixture_path.parent / str(model["path"])).resolve()
    if model_path.parent != fixture_path.parent:
        raise ValueError("fixture model must be in the same approved fixture directory")
    model_hash = sha256_file(model_path)
    if model.get("sha256") != model_hash:
        raise ValueError("cutting.fixture.model_hash: model differs from fixture SHA-256")

    if require_live_approval:
        if approved_fixture_sha256 is None:
            raise ValueError("live operator requires --approved-fixture-sha256")
        if data.get("scope") != "approved-live-cutting-smoke" or data.get("approvedForLive") is not True:
            raise ValueError("live operator rejects non-approved or synthetic fixtures")
        if _contains_synthetic_sentinel(data):
            raise ValueError("live operator rejects synthetic fixture sentinels")
    else:
        if data.get("scope") != "synthetic-offline-contract-only" \
                or data.get("approvedForLive") is not False:
            raise ValueError("offline harness requires an explicitly synthetic fixture")

    # Build the strict MEB-139 plan before any external mutation is possible.
    plan = plan_sheet_links(data["cadModelMaterials"], data["cfrn"], data["confirmations"])
    if plan.get("ready") is not True or not plan.get("payload"):
        raise ValueError("cutting.materials.plan: strict article/sheet link plan is not ready")
    return LoadedCuttingFixture(
        path=fixture_path,
        data=data,
        fixture_sha256=fixture_hash,
        model_path=model_path,
        model_sha256=model_hash,
    )


def _extract_id(value: Any, kind: str) -> int:
    candidate = value[0] if isinstance(value, list) and value else value
    if isinstance(candidate, Mapping):
        for field in ("id", f"{kind}Id"):
            if field in candidate:
                candidate = candidate[field]
                break
    if isinstance(candidate, str) and candidate.isdigit():
        candidate = int(candidate)
    if isinstance(candidate, bool) or not isinstance(candidate, int) or candidate <= 0:
        raise ValueError(f"{kind} response does not contain a positive id")
    return candidate


def _opaque_ref(run_id: str, value: int) -> str:
    return hashlib.sha256(f"{run_id}:{value}".encode("utf-8")).hexdigest()[:16]


def poll_production_url(client: CuttingClient, order_id: int, *, interval: float) -> str:
    if isinstance(interval, bool) or not isinstance(interval, (int, float)) or interval <= 0:
        raise ValueError("production poll interval must be positive")
    while True:
        try:
            value = client.production_files_url(order_id)
            if not isinstance(value, str) or not value.strip():
                raise ValueError("production files URL is missing")
            return value
        except CuttingError as exc:
            if exc.code != "cutting.http.not_found":
                raise
            remaining = client.remaining_seconds()
            sleep_for = min(float(interval), remaining)
            if sleep_for <= 0:
                raise
            client.sleeper(sleep_for)


def run_cutting_flow(
    client: CuttingClient,
    fixture: LoadedCuttingFixture,
    *,
    production_interval: float = 5.0,
    require_live_evidence: bool = False,
) -> dict[str, Any]:
    if not client.run_id:
        raise ValueError("registered run_id is required")
    if fixture.fixture_sha256 != client.fixture_sha256 \
            or fixture.model_sha256 != client.model_sha256:
        raise ValueError("fixture/model hashes differ from the durable ledger approval")
    if require_live_evidence and not client.live_evidence_allowed:
        raise ValueError("live evidence requires internally confirmed pinned HTTPS transport")
    data = fixture.data
    plan = plan_sheet_links(data["cadModelMaterials"], data["cfrn"], data["confirmations"])
    prefix = client.run_id
    order_payload = dict(data["order"])
    if order_payload.get("factoryOrderId") == "${RUN_ID}":
        order_payload["factoryOrderId"] = prefix

    order_id = _extract_id(
        client.create_order(order_payload, idempotency_key=f"{prefix}:create"), "order",
    )
    model_id = _extract_id(
        client.upload_cad_model(
            order_id, fixture.model_path, count=data["model"]["count"],
            cut_models=data["model"]["cutModels"],
            idempotency_key=f"{prefix}:upload",
        ),
        "model",
    )
    actual_sources = client.cad_model_materials(model_id)
    if actual_sources != data["cadModelMaterials"]:
        raise ValueError("cutting.materials.unverified: remote source list differs from approved fixture")
    # Re-plan using the remote list; exact article/sheet confirmations remain mandatory.
    plan = plan_sheet_links(actual_sources, data["cfrn"], data["confirmations"])
    if plan.get("ready") is not True:
        raise ValueError("cutting.materials.plan: remote material plan is not fully ready")
    client.set_link_materials(
        model_id, plan["payload"], idempotency_key=f"{prefix}:materials",
    )
    linked_evidence = client.cutting_materials(order_id)
    client.run_cutting(order_id, idempotency_key=f"{prefix}:cutting")
    client.run_production_files(order_id, idempotency_key=f"{prefix}:production")
    production_url = poll_production_url(client, order_id, interval=production_interval)
    cut_evidence = client.cutted_materials(order_id)
    audit = audit_sheet_link_result(plan, linked_evidence, cut_evidence)
    if audit.get("offline_contract_passed") is not True:
        raise ValueError("cutting.materials.post_audit: article/sheet/cutting evidence failed")

    completed_at = client._timestamp(client.wall_clock())
    mode = "live" if client.live_evidence_allowed else "offline_contract"
    evidence = {
        "mode": mode,
        "live_transport_confirmed": client.live_evidence_allowed,
        "contract_version": data["contractVersion"],
        "run_id": client.run_id,
        "trace_id": client.trace_id,
        "started_at": client.started_at,
        "completed_at": completed_at,
        "fixture_sha256": fixture.fixture_sha256,
        "model_sha256": fixture.model_sha256,
        "order_ref": _opaque_ref(client.run_id, order_id),
        "model_ref": _opaque_ref(client.run_id, model_id),
        "material_count": len(actual_sources),
        "strict_material_audit": True,
        "production_files_response_sha256": hashlib.sha256(
            production_url.encode("utf-8")
        ).hexdigest(),
        "test_order": list(data["expectedOrder"]),
    }
    if require_live_evidence and mode != "live":
        raise ValueError("offline or injected transport cannot claim live evidence")
    return evidence
