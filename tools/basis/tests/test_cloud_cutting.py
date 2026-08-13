from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import requests

from src.cloud_cutting import CuttingClient, CuttingError, CuttingTimeouts


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


def client(session: Session, **kwargs: Any) -> CuttingClient:
    return CuttingClient(
        api_key="do-not-log-this-key",
        base_url="https://offline.invalid",
        allow_live=True,
        session=session,
        timeouts=CuttingTimeouts(connect=1, read=2, mutation_read=3),
        **kwargs,
    )


def test_live_access_is_disabled_by_default() -> None:
    session = Session(Response(payload=[]))
    c = CuttingClient(api_key="key", session=session)

    with pytest.raises(CuttingError) as caught:
        c.list_orders()

    assert caught.value.code == "cutting.live_guard.required"
    assert session.calls == []


def test_mutation_needs_budget_and_idempotency_key() -> None:
    session = Session(Response(payload=123))
    c = client(session)

    with pytest.raises(CuttingError) as caught:
        c.create_order({}, idempotency_key="one")
    assert caught.value.code == "cutting.cost_guard.required"

    guarded = client(Session(Response(payload=123)), allow_mutations=True, max_mutations=1)
    with pytest.raises(CuttingError) as caught:
        guarded.create_order({}, idempotency_key="")
    assert caught.value.code == "cutting.idempotency.required"


def test_idempotent_replay_does_not_repeat_even_for_empty_response() -> None:
    session = Session(Response())
    trace: list[dict[str, Any]] = []
    c = client(
        session,
        allow_mutations=True,
        max_mutations=1,
        trace_sink=trace.append,
    )

    assert c.run_cutting(42, idempotency_key="same-key") is None
    assert c.run_cutting(42, idempotency_key="same-key") is None

    assert len(session.calls) == 1
    assert c.mutation_count == 1
    assert trace[-1]["outcome"] == "idempotent_replay"


def test_idempotency_collision_is_rejected_before_http() -> None:
    session = Session(Response(), Response())
    c = client(session, allow_mutations=True, max_mutations=2)
    c.run_cutting(42, idempotency_key="shared")

    with pytest.raises(CuttingError) as caught:
        c.run_cutting(43, idempotency_key="shared")

    assert caught.value.code == "cutting.idempotency.collision"
    assert len(session.calls) == 1


def test_timed_out_mutation_is_ambiguous_and_cannot_be_retried() -> None:
    session = Session(requests.Timeout("private upstream text"), Response())
    c = client(session, allow_mutations=True, max_mutations=2)

    with pytest.raises(CuttingError) as caught:
        c.run_production_files(42, idempotency_key="production-42")
    assert caught.value.code == "cutting.transport.ambiguous"
    assert not caught.value.retryable
    assert "private upstream text" not in str(caught.value)

    with pytest.raises(CuttingError) as replay:
        c.run_production_files(42, idempotency_key="production-42")
    assert replay.value.code == "cutting.idempotency.ambiguous"
    assert len(session.calls) == 1


@pytest.mark.parametrize(
    ("status", "code"),
    [(400, "cutting.http.validation"), (401, "cutting.http.auth"),
     (404, "cutting.http.not_found"), (409, "cutting.http.conflict"),
     (429, "cutting.http.rate_limited"), (503, "cutting.http.server")],
)
def test_http_error_taxonomy_is_stable_and_sanitized(status: int, code: str) -> None:
    secret = "customer material and signed URL"
    session = Session(Response(status, {"detail": secret}))
    trace: list[dict[str, Any]] = []
    c = client(session, trace_sink=trace.append)

    with pytest.raises(CuttingError) as caught:
        c.get_order(17)

    assert caught.value.code == code
    assert secret not in str(caught.value)
    assert secret not in json.dumps(trace)
    assert all(event["route"] == "/orders/{id}" for event in trace)
    assert "do-not-log-this-key" not in json.dumps(trace)


def test_upload_uses_openapi_models_field_and_bounded_timeout(tmp_path: Path) -> None:
    model = tmp_path / "fixture.b3d"
    model.write_bytes(b"B3D fixture")
    session = Session(Response(payload=[{"id": 9}]))
    c = client(session, allow_mutations=True, max_mutations=1)

    result = c.upload_cad_model(7, model, idempotency_key="upload-7")

    assert result == [{"id": 9}]
    call = session.calls[0]
    assert set(call["files"]) == {"models"}
    assert call["params"] == {"orderId": 7, "count": 1, "cutModels": True}
    assert call["timeout"] == (1, 3)


def test_upload_rejects_missing_empty_large_and_wrong_extension(tmp_path: Path) -> None:
    c = client(Session(), allow_mutations=True, max_mutations=4, max_upload_bytes=2)
    empty = tmp_path / "empty.b3d"
    empty.write_bytes(b"")
    large = tmp_path / "large.b3d"
    large.write_bytes(b"123")
    wrong = tmp_path / "wrong.exe"
    wrong.write_bytes(b"1")

    with pytest.raises(FileNotFoundError):
        c.upload_cad_model(1, tmp_path / "missing.b3d", idempotency_key="missing")
    with pytest.raises(ValueError, match="empty"):
        c.upload_cad_model(1, empty, idempotency_key="empty")
    with pytest.raises(ValueError, match="max_upload_bytes"):
        c.upload_cad_model(1, large, idempotency_key="large")
    with pytest.raises(ValueError, match="extension"):
        c.upload_cad_model(1, wrong, idempotency_key="wrong")


def test_material_links_require_all_contract_fields() -> None:
    c = client(Session(), allow_mutations=True, max_mutations=1)
    with pytest.raises(ValueError, match="exactly"):
        c.set_link_materials(
            3,
            [{"originalMaterialFullName": "source", "materialType": 0}],
            idempotency_key="links",
        )
    with pytest.raises(ValueError, match="0..5"):
        c.set_link_materials(
            3,
            [{
                "originalMaterialFullName": "source",
                "materialType": 9,
                "linkedMaterialFullName": "target",
            }],
            idempotency_key="links-2",
        )


def test_mutation_budget_is_exact() -> None:
    session = Session(Response(), Response())
    c = client(session, allow_mutations=True, max_mutations=1)
    c.run_cutting(1, idempotency_key="one")

    with pytest.raises(CuttingError) as caught:
        c.run_production_files(1, idempotency_key="two")

    assert caught.value.code == "cutting.cost_guard.exhausted"
    assert len(session.calls) == 1


def test_material_response_contract_is_checked() -> None:
    trace: list[dict[str, Any]] = []
    c = client(Session(Response(payload={"material": "wrong"})), trace_sink=trace.append)
    with pytest.raises(CuttingError) as caught:
        c.cad_model_materials(3)
    assert caught.value.code == "cutting.response.contract"
    assert trace[-1]["error_code"] == "cutting.response.contract"


def test_poll_long_task_has_bounded_timeout_without_hidden_http_retry() -> None:
    now = [0.0]

    def clock() -> float:
        return now[0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    session = Session(Response(payload={"state": "running"}), Response(payload={"state": "running"}))
    c = CuttingClient(
        api_key="key",
        base_url="https://offline.invalid",
        allow_live=True,
        session=session,
        clock=clock,
        sleeper=sleep,
    )

    with pytest.raises(CuttingError) as caught:
        c.poll_long_task(5, timeout=2, interval=1)

    assert caught.value.code == "cutting.long_task.timeout"
    assert caught.value.retryable
    assert len(session.calls) == 2
