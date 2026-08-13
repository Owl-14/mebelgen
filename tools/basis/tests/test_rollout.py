"""MEB-158: shadow/canary rollout, SLO stops and bounded checkpoints."""

from __future__ import annotations

import json
import sqlite3
import sys
import http.client
import threading
from pathlib import Path
from http.server import ThreadingHTTPServer

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.rollout import (  # noqa: E402
    COMPONENTS,
    RolloutConfig,
    RolloutController,
    RolloutMetric,
    RolloutStateStore,
    SloBudgets,
    compare_shadow_results,
)
from src.studio_graph import (  # noqa: E402
    CheckpointStorageError,
    GraphAdapters,
    MemoryRevisionStore,
    StudioGraphOrchestrator,
)


def _config(**modes: str) -> RolloutConfig:
    return RolloutConfig(
        modes={name: modes.get(name, "on") for name in COMPONENTS},
        killed=frozenset(),
        canary_tenants=frozenset(),
        canary_users=frozenset(),
        canary_percent=0,
    )


def _spec() -> dict:
    return json.loads(
        (ROOT / "paramspecs" / "komi_72_tumba_podkatnaya.json").read_text(
            encoding="utf-8"
        )
    )


def test_component_flags_and_kill_switches_are_independent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    for component in COMPONENTS:
        monkeypatch.setenv(f"AKEDA_ROLLOUT_{component.upper()}", "on")
    monkeypatch.setenv("AKEDA_KILL_SWITCH_EDIT_ENGINE", "1")
    controller = RolloutController(tmp_path)
    plan = controller.plan("tenant-a", "user-a")

    assert plan.primary == "legacy"
    assert plan.component_modes["edit_engine"] == "off"
    assert plan.component_modes["typed_ops"] == "on"
    assert plan.component_modes["full_gate"] == "on"
    assert "kill_switch:edit_engine" in plan.reasons


def test_canary_is_selected_from_server_identity_not_request_payload(tmp_path: Path) -> None:
    config = RolloutConfig(
        modes={name: "canary" for name in COMPONENTS},
        killed=frozenset(),
        canary_tenants=frozenset({"tenant-selected"}),
        canary_users=frozenset({"user-selected"}),
        canary_percent=0,
    )
    controller = RolloutController(tmp_path, config=config)
    assert controller.plan("tenant-selected", "other").primary == "graph"
    assert controller.plan("other", "user-selected").primary == "graph"
    assert controller.plan("other", "other").primary == "legacy"


def test_shadow_requires_all_candidate_components_and_keeps_legacy_primary(
    tmp_path: Path,
) -> None:
    controller = RolloutController(
        tmp_path, config=_config(langgraph="shadow")
    )
    plan = controller.plan(None, None)
    assert plan.primary == "legacy"
    assert plan.shadow is True

    disabled = RolloutController(
        tmp_path / "disabled",
        config=_config(langgraph="shadow", full_gate="off"),
    ).plan(None, None)
    assert disabled.primary == "legacy"
    assert disabled.shadow is False


def test_shadow_graph_does_not_persist_revision() -> None:
    revisions = MemoryRevisionStore()
    graph = StudioGraphOrchestrator(GraphAdapters.defaults(revisions))
    result = graph.run(
        project_key="shadow-product",
        spec=_spec(),
        message="сделай ширину 410",
        generation="shadow-run",
        shadow=True,
    )
    assert result["graph"]["shadow"] is True
    assert result["spec"]["dimensions"]["width"] == 410
    assert revisions.rows == {}


def test_shadow_comparison_covers_paramspec_geometry_and_drilling() -> None:
    result = StudioGraphOrchestrator().run(
        project_key="comparison",
        spec=_spec(),
        message="сделай ширину 410",
        generation="comparison-run",
        shadow=True,
    )
    report = compare_shadow_results(result, result)
    assert report["equal"] is True
    assert report["geometry_equal"] is True
    assert report["drilling_equal"] is True


def test_shadow_http_returns_legacy_result_without_durable_candidate_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from src.studio import _Studio, make_handler

    for component in COMPONENTS:
        monkeypatch.setenv(f"AKEDA_ROLLOUT_{component.upper()}", "on")
    monkeypatch.setenv("AKEDA_ROLLOUT_LANGGRAPH", "shadow")
    spec_dir = tmp_path / "paramspecs"
    spec_dir.mkdir()
    spec_path = spec_dir / "product.json"
    source = _spec()
    spec_path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    studio = _Studio(spec_path, tmp_path / "out")
    assert studio.ai_graph is None
    assert studio.ai_shadow_graph is not None
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(studio))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        body = json.dumps({
            "spec": source,
            "message": "сделай ширину 410",
            "history": [],
        }).encode("utf-8")
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=20
        )
        connection.request(
            "POST", "/api/chat", body=body,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        payload = json.loads(response.read())
        connection.close()
        assert response.status == 200
        assert payload["spec"]["dimensions"]["width"] == 410
        assert payload["rollout"]["primary"] == "legacy"
        assert payload["rollout"]["shadow"] is True
        assert payload["rollout"]["shadow_comparison"]["equal"] is True
        assert not (tmp_path / "out" / ".studio_graph").exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_slo_violation_stops_canary_and_rolls_future_requests_back(tmp_path: Path) -> None:
    budgets = SloBudgets(
        latency_p50_ms=10,
        latency_p95_ms=10,
        tokens_p95=100,
        cost_usd_p95=1,
        invalid_op_rate=0.2,
        false_rejection_rate=0.2,
        edit_success_rate=0.8,
        checkpoint_bytes=1_000_000,
        checkpoint_retention_days=7,
        minimum_samples=2,
        window_samples=10,
    )
    controller = RolloutController(tmp_path, config=_config(), budgets=budgets)
    plan = controller.plan(None, None)
    assert plan.primary == "graph"
    controller.record(
        RolloutMetric(latency_ms=20, edit_success=True),
        tenant_id="tenant-secret", user_id="user-secret", plan=plan,
    )
    dashboard = controller.record(
        RolloutMetric(latency_ms=20, edit_success=True),
        tenant_id="tenant-secret", user_id="user-secret", plan=plan,
    )
    assert dashboard["stopped"] is True
    assert "latency_p95" in dashboard["reasons"]
    rolled_back = controller.plan(None, None)
    assert rolled_back.primary == "legacy"
    assert any(reason.startswith("slo_stop:") for reason in rolled_back.reasons)
    persisted = (tmp_path / "state.json").read_text(encoding="utf-8")
    assert "tenant-secret" not in persisted
    assert "user-secret" not in persisted


def test_read_only_queries_do_not_reduce_edit_success_slo(tmp_path: Path) -> None:
    budgets = SloBudgets(
        latency_p50_ms=100,
        latency_p95_ms=100,
        tokens_p95=100,
        cost_usd_p95=1,
        invalid_op_rate=0,
        false_rejection_rate=0,
        edit_success_rate=1,
        checkpoint_bytes=1_000_000,
        checkpoint_retention_days=7,
        minimum_samples=2,
        window_samples=10,
    )
    controller = RolloutController(tmp_path, config=_config(), budgets=budgets)
    plan = controller.plan(None, None)
    for _ in range(2):
        dashboard = controller.record(
            RolloutMetric(
                latency_ms=1, edit_attempted=False, edit_success=False
            ),
            tenant_id=None, user_id=None, plan=plan,
        )
    assert dashboard["summary"]["edit_samples"] == 0
    assert dashboard["stopped"] is False


def test_rollout_store_is_bounded_and_corruption_fails_closed(tmp_path: Path) -> None:
    store = RolloutStateStore(tmp_path, max_events=50)
    for index in range(75):
        store.append({"at": index, "latency_ms": index})
    assert len(store.read()["events"]) == 50
    store.path.write_text("not-json", encoding="utf-8")
    controller = RolloutController(tmp_path, config=_config(), store=store)
    plan = controller.plan(None, None)
    assert plan.primary == "legacy"
    assert plan.stopped is True
    assert any(reason.startswith("rollout_state_read:") for reason in plan.reasons)


def test_durable_checkpoints_are_pruned_and_storage_failure_degrades(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("AKEDA_CHECKPOINT_MAX_PER_THREAD", "4")
    durable = StudioGraphOrchestrator.durable(tmp_path / "healthy")
    try:
        result = durable.run(
            project_key="checkpointed",
            spec=_spec(),
            message="сделай ширину 410",
            generation="bounded",
        )
        assert result["checkpoint"]["max_per_thread"] == 4
        count = durable._sqlite_connection.execute(
            "SELECT COUNT(*) FROM checkpoints WHERE thread_id=?",
            (result["graph"]["thread_id"],),
        ).fetchone()[0]
        assert count <= 4
    finally:
        durable._sqlite_connection.close()

    unavailable = tmp_path / "not-a-directory"
    unavailable.write_text("occupied", encoding="utf-8")
    degraded = StudioGraphOrchestrator.durable(unavailable)
    assert degraded.storage_degraded is True
    assert degraded.storage_error.startswith("checkpoint_storage:")


def test_checkpoint_budget_is_reported_without_sensitive_payload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("AKEDA_CHECKPOINT_MAX_THREADS", "2")
    durable = StudioGraphOrchestrator.durable(tmp_path)
    try:
        for index in range(3):
            durable.run(
                project_key=f"product-{index}",
                spec=_spec(),
                message="сделай ширину 410",
                generation=f"generation-{index}",
            )
        threads = durable._sqlite_connection.execute(
            "SELECT COUNT(*) FROM akeda_checkpoint_threads"
        ).fetchone()[0]
        assert threads <= 2
        assert sqlite3.connect(tmp_path / "checkpoints.sqlite3").execute(
            "PRAGMA integrity_check"
        ).fetchone()[0] == "ok"
    finally:
        durable._sqlite_connection.close()


def test_checkpoint_budget_failure_does_not_commit_revision(tmp_path: Path) -> None:
    durable = StudioGraphOrchestrator.durable(tmp_path)
    durable.checkpoint_retention.max_bytes = 1
    try:
        with pytest.raises(CheckpointStorageError):
            durable.run(
                project_key="budget-failure",
                spec=_spec(),
                message="сделай ширину 410",
                generation="must-not-persist",
            )
        assert not (tmp_path / "revisions.json").exists()
    finally:
        durable._sqlite_connection.close()
