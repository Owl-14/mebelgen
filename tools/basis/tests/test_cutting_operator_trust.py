from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import requests

from src import cutting_operator_trust as trust
from src.cutting_preflight import run_cutting_flow

ROOT = Path(__file__).resolve().parent.parent
OFFLINE_FIXTURE = ROOT / "qa" / "fixtures" / "cutting_test_order.json"


def _replace_synthetic(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace("__FIXTURE_", "APPROVED_").replace(
            "synthetic-offline-contract-only", "approved-live-cutting-smoke"
        ).replace("synthetic", "approved").replace("offline-contract", "approved-live")
    if isinstance(value, list):
        return [_replace_synthetic(item) for item in value]
    if isinstance(value, dict):
        return {key: _replace_synthetic(item) for key, item in value.items()}
    return value


def _canonical_approval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data = _replace_synthetic(json.loads(OFFLINE_FIXTURE.read_text(encoding="utf-8")))
    data["scope"] = "approved-live-cutting-smoke"
    data["approvedForLive"] = True
    source_model = OFFLINE_FIXTURE.parent / data["model"]["path"]
    fixture_dir = tmp_path / "approved-fixture"
    fixture_dir.mkdir()
    model = fixture_dir / source_model.name
    model.write_bytes(source_model.read_bytes())
    data["model"]["path"] = model.name
    data["model"]["sha256"] = hashlib.sha256(model.read_bytes()).hexdigest()
    fixture = fixture_dir / "approved.json"
    fixture.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    fixture_hash = hashlib.sha256(fixture.read_bytes()).hexdigest()

    config = tmp_path / "machine-trust" / "approved-live.json"
    config.parent.mkdir()
    config.write_text(json.dumps({
        "contractVersion": "cutting-operator-approval-v1",
        "authorizationScope": "approved-live-cutting-smoke",
        "approvedForLive": True,
        "fixturePath": str(fixture.resolve()),
        "fixtureSha256": fixture_hash,
        "maxMutations": 5,
        "overallTimeout": 600,
        "productionInterval": 5,
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(trust, "CANONICAL_APPROVAL_PATH", config)
    return trust.load_canonical_operator_approval(), fixture


def test_canonical_config_digest_derives_only_ledger_and_opaque_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    approval, _fixture = _canonical_approval(tmp_path, monkeypatch)
    assert approval.ledger_path == (
        trust.CANONICAL_APPROVAL_PATH.parent / "ledgers" /
        f"{approval.approval_digest}.sqlite3"
    ).resolve()
    client, ledger, run = trust.build_operator_client(
        approval, api_key="not-a-real-key",
    )
    assert client.live_evidence_allowed is True
    assert run.run_id.startswith("run_") and len(run.run_id) == 36
    assert ledger.path == approval.ledger_path

    # Reopening the same machine approval cannot mint a fresh run or budget.
    _client2, _ledger2, run2 = trust.build_operator_client(
        approval, api_key="not-a-real-key",
    )
    assert run2.run_id == run.run_id

    forged = replace(approval, ledger_path=tmp_path / "fresh.sqlite3")
    with pytest.raises(ValueError, match="not the current canonical"):
        trust.build_operator_client(forged, api_key="not-a-real-key")


def test_preconstruction_session_request_patch_cannot_forge_live_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    approval, _fixture = _canonical_approval(tmp_path, monkeypatch)
    scripted_calls: list[dict[str, Any]] = []

    def scripted_request(*args: Any, **kwargs: Any) -> Any:
        scripted_calls.append({"args": args, "kwargs": kwargs})
        raise AssertionError("scripted transport must never be called")

    with patch.object(requests.Session, "request", scripted_request):
        with pytest.raises(RuntimeError, match="transport attestation failed"):
            trust.build_operator_client(
                approval, api_key="not-a-real-key",
            )

    assert scripted_calls == []


def test_run_flow_revalidates_approved_fixture_before_first_live_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    approval, fixture_path = _canonical_approval(tmp_path, monkeypatch)
    client, _ledger, _run = trust.build_operator_client(
        approval, api_key="not-a-real-key",
    )
    fixture_path.write_bytes(fixture_path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="differs from the separately approved"):
        run_cutting_flow(
            client, approval.fixture, production_interval=5, require_live_evidence=True,
        )


def test_run_flow_revalidates_canonical_config_digest_before_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    approval, _fixture_path = _canonical_approval(tmp_path, monkeypatch)
    client, _ledger, _run = trust.build_operator_client(
        approval, api_key="not-a-real-key",
    )
    config = trust.CANONICAL_APPROVAL_PATH
    config.write_bytes(config.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="canonical machine approval changed"):
        run_cutting_flow(
            client, approval.fixture, production_interval=5, require_live_evidence=True,
        )


def test_canonical_config_rejects_synthetic_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / "approved-live.json"
    raw = OFFLINE_FIXTURE.read_bytes()
    config.write_text(json.dumps({
        "contractVersion": "cutting-operator-approval-v1",
        "authorizationScope": "approved-live-cutting-smoke",
        "approvedForLive": True,
        "fixturePath": str(OFFLINE_FIXTURE.resolve()),
        "fixtureSha256": hashlib.sha256(raw).hexdigest(),
        "maxMutations": 5,
        "overallTimeout": 600,
        "productionInterval": 5,
    }), encoding="utf-8")
    monkeypatch.setattr(trust, "CANONICAL_APPROVAL_PATH", config)
    with pytest.raises(ValueError, match="rejects non-approved or synthetic"):
        trust.load_canonical_operator_approval()
