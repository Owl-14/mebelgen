from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import requests

from src.cloud_cutting import CuttingClient, CuttingError, CuttingTimeouts, sha256_file
from src.cutting_ledger import MutationLedger

TEST_ORIGIN = "https://cutting-test.invalid"
FIXTURE_HASH = "a" * 64
MODEL_HASH = "b" * 64
RUN_ID = "MEB-140-TEST-RUN"


class Response:
    def __init__(self, status: int = 200, payload: Any = None, content_type: str = "application/json"):
        self.status_code = status
        self.payload = payload
        self.headers = {"content-type": content_type}

    @property
    def content(self) -> bytes:
        if self.payload is None:
            return b""
        if "json" in self.headers["content-type"]:
            return json.dumps(self.payload).encode()
        return str(self.payload).encode()

    @property
    def text(self) -> str:
        return self.content.decode()

    def json(self) -> Any:
        return json.loads(self.content)


class Session:
    def __init__(self, *results: Any):
        self.results = list(results)
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> Response:
        self.calls.append({"method": method, "url": url, **kwargs})
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def ledger(tmp_path: Path, *, max_mutations: int = 5, model_hash: str = MODEL_HASH) -> MutationLedger:
    result = MutationLedger(tmp_path / "ledger.sqlite3")
    result.register_run(
        run_id=RUN_ID,
        mode="offline_contract",
        fixture_sha256=FIXTURE_HASH,
        model_sha256=model_hash,
        transport_origin=TEST_ORIGIN,
        max_mutations=max_mutations,
        deadline_epoch=10_000_000_000,
    )
    return result


def client(session: Session, tmp_path: Path, **kwargs: Any) -> CuttingClient:
    mutation_ledger = kwargs.pop("ledger", None)
    return CuttingClient(
        base_url=TEST_ORIGIN,
        test_api_key="offline-test-key",
        test_endpoint_allowlist=[TEST_ORIGIN],
        allow_live=True,
        overall_timeout=30,
        session=session,
        timeouts=CuttingTimeouts(connect=1, read=2, mutation_read=3),
        ledger=mutation_ledger,
        run_id=RUN_ID if mutation_ledger else None,
        **kwargs,
    )


@pytest.mark.parametrize("url", [
    "http://cloud.bazissoft.ru",
    "https://cloud.bazissoft.ru.evil.example",
    "https://cloud.bazissoft.ru:443",
    "https://cloud.bazissoft.ru:444",
    "https://cloud.bazissoft.ru/path",
])
def test_production_transport_is_exact_https_origin(url: str) -> None:
    with pytest.raises(ValueError):
        CuttingClient(api_key="real-key", base_url=url)


def test_test_endpoint_requires_explicit_allowlist_and_nonproduction_key() -> None:
    with pytest.raises(ValueError, match="allowlisted"):
        CuttingClient(base_url=TEST_ORIGIN, test_api_key="test")
    with pytest.raises(ValueError, match="production api_key"):
        CuttingClient(
            api_key="must-not-leak", base_url=TEST_ORIGIN,
            test_endpoint_allowlist=[TEST_ORIGIN],
        )


def test_live_guard_and_overall_deadline_fail_before_http(tmp_path: Path) -> None:
    session = Session(Response(payload=[]))
    guarded = CuttingClient(
        base_url=TEST_ORIGIN, test_api_key="test",
        test_endpoint_allowlist=[TEST_ORIGIN], session=session,
    )
    with pytest.raises(CuttingError) as caught:
        guarded.list_orders()
    assert caught.value.code == "cutting.live_guard.required"

    no_deadline = CuttingClient(
        base_url=TEST_ORIGIN, test_api_key="test",
        test_endpoint_allowlist=[TEST_ORIGIN], session=session, allow_live=True,
    )
    with pytest.raises(CuttingError) as caught:
        no_deadline.list_orders()
    assert caught.value.code == "cutting.deadline.required"
    assert session.calls == []


def test_allowlisted_test_transport_never_attests_live(tmp_path: Path) -> None:
    c = client(Session(Response(payload=[])), tmp_path)
    assert c.live_evidence_allowed is False
    assert c.list_orders() == []
    call = c.session.calls[0]
    assert call["headers"] == {"apiKey": "offline-test-key"}
    assert call["verify"] is True
    assert call["allow_redirects"] is False


def test_injected_session_cannot_target_pinned_production_origin() -> None:
    with pytest.raises(ValueError, match="injected sessions cannot target"):
        CuttingClient(api_key="real-key", session=Session())


def test_offline_transport_cannot_claim_or_mutate_as_live(tmp_path: Path) -> None:
    mutation_ledger = MutationLedger(tmp_path / "live-ledger.sqlite3")
    mutation_ledger.register_run(
        run_id=RUN_ID,
        mode="live",
        fixture_sha256=FIXTURE_HASH,
        model_sha256=MODEL_HASH,
        transport_origin=TEST_ORIGIN,
        max_mutations=5,
        deadline_epoch=10_000_000_000,
    )
    session = Session(Response())
    c = CuttingClient(
        base_url=TEST_ORIGIN,
        test_api_key="offline-test-key",
        test_endpoint_allowlist=[TEST_ORIGIN],
        allow_live=True,
        allow_mutations=True,
        ledger=mutation_ledger,
        run_id=RUN_ID,
        overall_timeout=30,
        session=session,
    )

    assert c.live_evidence_allowed is False
    with pytest.raises(CuttingError) as caught:
        c.run_cutting(1, idempotency_key="MEB-140:injected-live")
    assert caught.value.code == "cutting.transport.attestation"
    assert session.calls == []


@pytest.mark.parametrize("value", [0, -1, False])
def test_overall_timeout_must_be_positive(value: Any) -> None:
    with pytest.raises(ValueError, match="overall_timeout must be positive"):
        CuttingClient(
            base_url=TEST_ORIGIN,
            test_api_key="test",
            test_endpoint_allowlist=[TEST_ORIGIN],
            allow_live=True,
            overall_timeout=value,
            session=Session(),
        )


def test_mutation_requires_registered_durable_ledger(tmp_path: Path) -> None:
    c = client(Session(Response()), tmp_path, allow_mutations=True)
    with pytest.raises(CuttingError) as caught:
        c.run_cutting(1, idempotency_key="MEB-140:no-ledger")
    assert caught.value.code == "cutting.ledger.required"
    assert c.session.calls == []


def test_successful_mutation_transitions_durable_state(tmp_path: Path) -> None:
    mutation_ledger = ledger(tmp_path)
    session = Session(Response())
    c = client(session, tmp_path, ledger=mutation_ledger, allow_mutations=True)

    assert c.run_cutting(42, idempotency_key="MEB-140:cutting:1") is None

    assert mutation_ledger.operation("MEB-140:cutting:1")["state"] == "success"
    assert mutation_ledger.run_operation_count(RUN_ID) == 1
    assert session.calls[0]["timeout"] == (1, 3)


def test_cross_client_duplicate_key_is_blocked(tmp_path: Path) -> None:
    mutation_ledger = ledger(tmp_path)
    first_session = Session(Response())
    first = client(first_session, tmp_path, ledger=mutation_ledger, allow_mutations=True)
    first.run_cutting(42, idempotency_key="MEB-140:duplicate")

    second_session = Session(Response())
    reopened_ledger = MutationLedger(mutation_ledger.path)
    second = client(second_session, tmp_path, ledger=reopened_ledger, allow_mutations=True)
    with pytest.raises(CuttingError) as caught:
        second.run_cutting(42, idempotency_key="MEB-140:duplicate")

    assert caught.value.code == "cutting.idempotency.duplicate"
    assert second_session.calls == []


def test_ambiguous_mutation_requires_remote_reconciliation(tmp_path: Path) -> None:
    mutation_ledger = ledger(tmp_path)
    first = client(
        Session(requests.Timeout("secret upstream text")), tmp_path,
        ledger=mutation_ledger, allow_mutations=True,
    )
    with pytest.raises(CuttingError) as caught:
        first.run_production_files(42, idempotency_key="MEB-140:ambiguous")
    assert caught.value.code == "cutting.transport.ambiguous"
    assert "secret upstream text" not in str(caught.value)
    assert mutation_ledger.operation("MEB-140:ambiguous")["state"] == "ambiguous"

    second_session = Session(Response())
    second = client(second_session, tmp_path, ledger=mutation_ledger, allow_mutations=True)
    with pytest.raises(CuttingError) as duplicate:
        second.run_production_files(42, idempotency_key="MEB-140:ambiguous")
    assert duplicate.value.code == "cutting.idempotency.ambiguous"
    assert second_session.calls == []

    mutation_ledger.record_reconciliation(
        "MEB-140:ambiguous", remote_outcome="not_applied", evidence_sha256="c" * 64,
    )
    # Reconciliation is durable, but retry still requires a fresh key.
    with pytest.raises(CuttingError) as still_blocked:
        second.run_production_files(42, idempotency_key="MEB-140:ambiguous")
    assert still_blocked.value.code == "cutting.idempotency.ambiguous"


def test_durable_run_limit_applies_across_clients(tmp_path: Path) -> None:
    mutation_ledger = ledger(tmp_path, max_mutations=1)
    first = client(Session(Response()), tmp_path, ledger=mutation_ledger, allow_mutations=True)
    first.run_cutting(1, idempotency_key="MEB-140:budget:one")
    second_session = Session(Response())
    reopened_ledger = MutationLedger(mutation_ledger.path)
    second = client(second_session, tmp_path, ledger=reopened_ledger, allow_mutations=True)

    with pytest.raises(CuttingError) as caught:
        second.run_production_files(1, idempotency_key="MEB-140:budget:two")
    assert caught.value.code == "cutting.cost_guard.exhausted"
    assert second_session.calls == []


def test_conflicting_key_fingerprint_is_blocked(tmp_path: Path) -> None:
    mutation_ledger = ledger(tmp_path)
    first = client(Session(Response()), tmp_path, ledger=mutation_ledger, allow_mutations=True)
    first.run_cutting(1, idempotency_key="MEB-140:collision")
    reopened_ledger = MutationLedger(mutation_ledger.path)
    second = client(Session(Response()), tmp_path, ledger=reopened_ledger, allow_mutations=True)
    with pytest.raises(CuttingError) as caught:
        second.run_cutting(2, idempotency_key="MEB-140:collision")
    assert caught.value.code == "cutting.idempotency.collision"


def test_request_timeouts_use_positive_remaining_overall_deadline(tmp_path: Path) -> None:
    now = [10.0]

    def clock() -> float:
        return now[0]

    session = Session(Response(payload=[]), Response(payload=[]))
    c = CuttingClient(
        base_url=TEST_ORIGIN, test_api_key="test", test_endpoint_allowlist=[TEST_ORIGIN],
        allow_live=True, overall_timeout=5, session=session, clock=clock,
        timeouts=CuttingTimeouts(connect=10, read=20, mutation_read=30),
    )
    c.list_orders()
    assert session.calls[0]["timeout"] == (5.0, 5.0)
    now[0] = 14.5
    c.list_orders()
    assert session.calls[1]["timeout"] == (0.5, 0.5)
    now[0] = 15.0
    with pytest.raises(CuttingError) as caught:
        c.list_orders()
    assert caught.value.code == "cutting.deadline.exceeded"


@pytest.mark.parametrize(
    ("status", "code"),
    [(302, "cutting.http.redirect_blocked"), (400, "cutting.http.validation"),
     (401, "cutting.http.auth"), (404, "cutting.http.not_found"),
     (409, "cutting.http.conflict"), (429, "cutting.http.rate_limited"),
     (503, "cutting.http.server")],
)
def test_http_error_taxonomy_is_stable_and_sanitized(
    status: int, code: str, tmp_path: Path,
) -> None:
    secret = "customer material and signed URL"
    session = Session(Response(status, {"detail": secret}))
    trace: list[dict[str, Any]] = []
    c = client(session, tmp_path, trace_sink=trace.append)
    with pytest.raises(CuttingError) as caught:
        c.get_order(17)
    assert caught.value.code == code
    serialized = json.dumps(trace)
    assert secret not in str(caught.value)
    assert secret not in serialized
    assert all(event["route"] == "/orders/{id}" for event in trace)
    assert "offline-test-key" not in serialized


def test_trace_has_internal_bounded_id_and_approved_hashes(tmp_path: Path) -> None:
    mutation_ledger = ledger(tmp_path)
    trace: list[dict[str, Any]] = []
    c = client(
        Session(Response()), tmp_path, ledger=mutation_ledger,
        allow_mutations=True, trace_sink=trace.append,
    )
    c.run_cutting(1, idempotency_key="MEB-140:trace:key")
    assert len(c.trace_id) == 32 and int(c.trace_id, 16) >= 0
    event = trace[0]
    assert event["run_id"] == RUN_ID
    assert event["fixture_sha256"] == FIXTURE_HASH
    assert event["model_sha256"] == MODEL_HASH
    assert event["timestamp"].endswith("Z")
    assert event["transport"] == "allowlisted_test"


def test_upload_requires_ledger_approved_model_hash(tmp_path: Path) -> None:
    model = tmp_path / "fixture.b3d"
    model.write_bytes(b"B3D fixture")
    mutation_ledger = ledger(tmp_path, model_hash=sha256_file(model))
    session = Session(Response(payload=[{"id": 9}]))
    c = client(session, tmp_path, ledger=mutation_ledger, allow_mutations=True)
    assert c.upload_cad_model(7, model, idempotency_key="MEB-140:upload:key") == [{"id": 9}]
    assert set(session.calls[0]["files"]) == {"models"}

    changed = tmp_path / "changed.b3d"
    changed.write_bytes(b"changed")
    with pytest.raises(CuttingError) as caught:
        c.upload_cad_model(7, changed, idempotency_key="MEB-140:upload:changed")
    assert caught.value.code == "cutting.fixture.model_hash"
    assert len(session.calls) == 1


def test_material_link_conflicts_fail_before_ledger_or_http(tmp_path: Path) -> None:
    mutation_ledger = ledger(tmp_path)
    session = Session(Response())
    c = client(session, tmp_path, ledger=mutation_ledger, allow_mutations=True)
    with pytest.raises(ValueError, match="conflicting duplicate"):
        c.set_link_materials(3, [
            {"originalMaterialFullName": " Board ", "materialType": 0,
             "linkedMaterialFullName": "Target A"},
            {"originalMaterialFullName": "board", "materialType": 0,
             "linkedMaterialFullName": "Target B"},
        ], idempotency_key="MEB-140:links:key")
    assert session.calls == []
    assert mutation_ledger.run_operation_count(RUN_ID) == 0


def test_result_evidence_endpoints_use_safe_routes(tmp_path: Path) -> None:
    session = Session(Response(payload=[]), Response(payload=[]))
    c = client(session, tmp_path)
    assert c.cutting_materials(23) == []
    assert c.cutted_materials(23) == []
    assert session.calls[0]["params"] == {"orderId": 23}
    assert session.calls[1]["params"] is None
