"""Canonical live-Cutting approval boundary used only by the operator entrypoint."""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .cloud_cutting import (
    PINNED_ORIGIN,
    CuttingClient,
    TraceSink,
    _OPERATOR_TRUST_TOKEN,
)
from .cutting_ledger import LedgerRun, MutationLedger
from .cutting_preflight import LoadedCuttingFixture, load_cutting_fixture

CANONICAL_APPROVAL_PATH = (
    Path("C:/ProgramData/Akeda/Cutting/approved-live.json")
    if os.name == "nt" else Path("/etc/akeda/cutting/approved-live.json")
)
_CONFIG_KEYS = {
    "contractVersion", "authorizationScope", "approvedForLive", "fixturePath",
    "fixtureSha256", "maxMutations", "overallTimeout", "productionInterval",
}


@dataclass(frozen=True)
class OperatorApproval:
    approval_digest: str
    ledger_identity: str
    ledger_path: Path
    fixture: LoadedCuttingFixture
    max_mutations: int
    overall_timeout: float
    production_interval: float


def _positive_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{field} must be positive")
    return float(value)


def load_canonical_operator_approval() -> OperatorApproval:
    """Read the sole machine-approved config; no caller path is accepted."""
    canonical = CANONICAL_APPROVAL_PATH
    if canonical.is_symlink():
        raise ValueError("canonical Cutting approval config must not be a symlink")
    raw = canonical.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, Mapping) or set(data) != _CONFIG_KEYS:
        raise ValueError("canonical Cutting approval config has unexpected fields")
    if data["contractVersion"] != "cutting-operator-approval-v1" \
            or data["authorizationScope"] != "approved-live-cutting-smoke" \
            or data["approvedForLive"] is not True:
        raise ValueError("canonical Cutting approval config is not approved for live smoke")
    fixture_path = Path(str(data["fixturePath"]))
    if not fixture_path.is_absolute():
        raise ValueError("approved fixturePath must be absolute")
    fixture = load_cutting_fixture(
        fixture_path,
        require_live_approval=True,
        approved_fixture_sha256=str(data["fixtureSha256"]),
    )
    max_mutations = data["maxMutations"]
    if isinstance(max_mutations, bool) or not isinstance(max_mutations, int) \
            or max_mutations != 5:
        raise ValueError("canonical Cutting smoke approval requires exactly five mutations")
    overall_timeout = _positive_number(data["overallTimeout"], "overallTimeout")
    production_interval = _positive_number(data["productionInterval"], "productionInterval")
    ledger_path = canonical.parent / "ledgers" / f"{digest}.sqlite3"
    identity = hashlib.sha256(
        f"cutting-ledger-v1\0{canonical.resolve()}\0{digest}".encode("utf-8")
    ).hexdigest()
    return OperatorApproval(
        approval_digest=digest,
        ledger_identity=identity,
        ledger_path=ledger_path.resolve(),
        fixture=fixture,
        max_mutations=max_mutations,
        overall_timeout=overall_timeout,
        production_interval=production_interval,
    )


def build_operator_client(
    approval: OperatorApproval,
    *,
    api_key: str,
    trace_sink: TraceSink | None = None,
) -> tuple[CuttingClient, MutationLedger, LedgerRun]:
    """Create the only client capable of live evidence inside this boundary."""
    # Never trust an OperatorApproval object supplied by a caller. Re-read the
    # canonical machine config and require byte-derived identity equality.
    if load_canonical_operator_approval() != approval:
        raise ValueError("operator approval is not the current canonical machine approval")
    ledger = MutationLedger(
        approval.ledger_path,
        canonical_path=(CANONICAL_APPROVAL_PATH.parent / "ledgers" /
                        f"{approval.approval_digest}.sqlite3"),
        ledger_identity=approval.ledger_identity,
    )
    now = time.time()
    run = ledger.approve_run(
        approval_digest=approval.approval_digest,
        mode="live",
        fixture_sha256=approval.fixture.fixture_sha256,
        model_sha256=approval.fixture.model_sha256,
        transport_origin=PINNED_ORIGIN,
        max_mutations=approval.max_mutations,
        deadline_epoch=now + approval.overall_timeout,
        now_epoch=now,
    )
    remaining = run.deadline_epoch - time.time()
    if remaining <= 0:
        raise ValueError("canonical approved Cutting run deadline has expired")
    def revalidate() -> None:
        if load_canonical_operator_approval() != approval:
            raise ValueError("canonical machine approval changed before Cutting mutation")

    client = CuttingClient(
        api_key=api_key,
        allow_live=True,
        allow_mutations=True,
        ledger=ledger,
        ledger_run=run,
        overall_timeout=min(approval.overall_timeout, remaining),
        trace_sink=trace_sink,
        _operator_trust=_OPERATOR_TRUST_TOKEN,
        _approval_revalidator=revalidate,
    )
    if not client.live_evidence_allowed:
        raise RuntimeError("canonical pinned operator transport attestation failed")
    return client, ledger, run
