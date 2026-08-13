"""MEB-147: safe, comparable traces for AI and deterministic engineering."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.spec_chat import chat_edit  # noqa: E402
from src.studio import PAGE, _trace_engineering_result, build_payload, make_handler  # noqa: E402
from src.telemetry import (  # noqa: E402
    configure,
    current_trace_id,
    force_flush,
    safe_attributes,
    span,
    traced_http_request,
)

SPEC = json.loads(
    (ROOT / "paramspecs" / "komi_72_tumba_podkatnaya.json").read_text(encoding="utf-8")
)


@pytest.fixture(autouse=True)
def _reset_telemetry(monkeypatch: pytest.MonkeyPatch):
    yield
    monkeypatch.setenv("AKEDA_TELEMETRY_BACKEND", "none")
    configure(force=True)


def _file_backend(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    target = tmp_path / "traces.jsonl"
    monkeypatch.setenv("AKEDA_TELEMETRY_BACKEND", "file")
    monkeypatch.setenv("AKEDA_TELEMETRY_SAMPLE_RATE", "1")
    monkeypatch.setenv("AKEDA_TELEMETRY_FILE", str(target))
    configure(force=True)
    return target


def _rows(path: Path) -> list[dict]:
    assert force_flush()
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_attribute_policy_drops_prompts_specs_images_and_secrets() -> None:
    attrs = safe_attributes({
        "model": "mock",
        "prompt": "full technical brief",
        "paramspec": {"private": True},
        "image": "base64",
        "authorization": "Bearer secret",
        "token": "secret",
        "gen_ai.usage.total_tokens": 123,
        "gen_ai.usage.cost_usd": 0.01234567,
        "operation.types": ["dimensions", "sections"],
    })
    assert attrs == {
        "model": "mock",
        "gen_ai.usage.total_tokens": 123,
        "gen_ai.usage.cost_usd": 0.01234567,
        "operation.types": ["dimensions", "sections"],
    }


def test_ai_geometry_drilling_and_quality_nodes_share_one_trace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    target = _file_backend(monkeypatch, tmp_path)
    with span("chat.request", {"http.route": "/api/chat"}):
        trace_id = current_trace_id()
        edited = chat_edit(SPEC, "сделай ширину 410", provider="mock")
        assert edited["spec"] is not None
        _trace_engineering_result(edited["spec"])

    rows = _rows(target)
    names = {row["name"] for row in rows}
    assert {
        "chat.request", "intent.classify", "operations.plan", "operations.validate",
        "paramspec.apply", "paramspec.validate", "geometry.generate",
        "drilling.compute", "quality-check.schema", "quality-check.consistency",
        "quality-check.geometry", "quality-check.cfrn", "quality-check.holes",
        "quality-check.drilling", "response.summarize",
    } <= names
    assert {row["trace_id"] for row in rows} == {trace_id}
    rendered = target.read_text(encoding="utf-8")
    assert "сделай ширину" not in rendered
    assert SPEC["project_name"] not in rendered
    assert all("latency_ms" in row["attributes"] for row in rows)


def test_identical_operations_are_comparable_by_nodes_timing_and_outcome(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    target = _file_backend(monkeypatch, tmp_path)
    trace_ids: list[str] = []
    for _ in range(2):
        with span("chat.request", {"http.route": "/api/chat"}):
            trace_ids.append(current_trace_id())
            assert build_payload(SPEC)["ok"]

    rows = _rows(target)
    by_trace = {
        trace_id: [row for row in rows if row["trace_id"] == trace_id]
        for trace_id in trace_ids
    }
    assert trace_ids[0] != trace_ids[1]
    assert {row["name"] for row in by_trace[trace_ids[0]]} == {
        row["name"] for row in by_trace[trace_ids[1]]
    }
    for trace_rows in by_trace.values():
        assert all(row["end_time_unix_nano"] >= row["start_time_unix_nano"] for row in trace_rows)
        checks = [row for row in trace_rows if row["name"].startswith("quality-check.")]
        assert checks and all(row["attributes"]["check.outcome"] == "pass" for row in checks)


def test_studio_history_ui_renders_full_trace_id() -> None:
    assert "trace_id: ${traceId}" in PAGE
    assert "traceId:item.trace_id||''" in PAGE


def test_studio_json_response_exposes_active_trace_id() -> None:
    handler_type = make_handler(SimpleNamespace(require_auth=False, cookie_secure=False))
    handler = object.__new__(handler_type)
    captured: dict[str, object] = {}
    handler._send = lambda code, body, ctype="application/json; charset=utf-8", **kwargs: captured.update(  # type: ignore[method-assign]
        code=code, body=body
    )
    with span("chat.request"):
        trace_id = current_trace_id()
        handler._json({"ok": True})
    payload = json.loads(captured["body"])
    assert payload == {"ok": True, "trace_id": trace_id}


@pytest.mark.parametrize(("route", "span_name"), [
    ("/api/chat", "chat.request"),
    ("/api/save", "revision.persist"),
    ("/review/public-token/decision", "approval"),
])
def test_http_mutation_roots_return_trace_ids(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    route: str,
    span_name: str,
) -> None:
    target = _file_backend(monkeypatch, tmp_path)

    class Handler:
        path = route

    @traced_http_request
    def operation(_handler: Handler) -> str:
        return current_trace_id()

    trace_id = operation(Handler())
    rows = _rows(target)
    assert len(trace_id) == 32
    assert rows[-1]["name"] == span_name
    assert rows[-1]["trace_id"] == trace_id


def test_finish_project_exports_each_repair_iteration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from src.finish_project import finish_project
    from src.generators import generate_from_paramspec

    target = _file_backend(monkeypatch, tmp_path)
    wardrobe = json.loads((ROOT / "paramspecs" / "wardrobe_demo.json").read_text(encoding="utf-8"))
    project = generate_from_paramspec(wardrobe)
    shelf = next(panel for panel in project["panels"] if panel["type"] == "shelf")
    shelf["placement"]["x2"] = 1184  # crosses deterministic structural boundaries
    project_path = tmp_path / "repair-project.json"
    project_path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

    with span("chat.request"):
        result = finish_project(project_path)
    assert result.fixes_applied
    repair_rows = [row for row in _rows(target) if row["name"] == "repair.iteration"]
    assert repair_rows
    assert repair_rows[0]["attributes"]["repair.iteration"] == 1
    assert repair_rows[0]["attributes"]["repair.applied"] is True
