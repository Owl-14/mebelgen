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
    BufferedRevisionStore,
    CheckpointStorageError,
    GraphAdapters,
    JsonRevisionStore,
    MemoryRevisionStore,
    StudioGraphOrchestrator,
)


def _strict_budgets(*, minimum_samples: int = 2) -> SloBudgets:
    return SloBudgets(
        latency_p50_ms=10,
        latency_p95_ms=10,
        tokens_p95=100,
        cost_usd_p95=1,
        invalid_op_rate=0.2,
        false_rejection_rate=0.2,
        edit_success_rate=0.8,
        checkpoint_bytes=1_000_000,
        checkpoint_retention_days=7,
        minimum_samples=minimum_samples,
        window_samples=10,
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


def test_reviewer_counterexample_shadow_slo_uses_candidate_metrics(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from src.studio import _Studio, make_handler
    import src.spec_chat as spec_chat

    for component in COMPONENTS:
        monkeypatch.setenv(f"AKEDA_ROLLOUT_{component.upper()}", "on")
    monkeypatch.setenv("AKEDA_ROLLOUT_LANGGRAPH", "shadow")
    spec_path = tmp_path / "product.json"
    source = _spec()
    spec_path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    studio = _Studio(spec_path, tmp_path / "out")
    candidate_spec = json.loads(json.dumps(source))
    candidate_spec["dimensions"]["width"] = 410
    candidate = {
        "spec": candidate_spec,
        "usage": {"total": 987, "cost_usd": 0.123},
        "code": "candidate_code",
        "graph": {"operations": [{"op": "set"}]},
    }
    legacy = {
        "spec": candidate_spec,
        "usage": {"total": 11, "cost_usd": 0.001},
        "code": "legacy_code",
    }
    monkeypatch.setattr(spec_chat, "chat_edit", lambda *_a, **_k: legacy)
    monkeypatch.setattr(studio.ai_shadow_graph, "run", lambda **_kwargs: candidate)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(studio))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=20
        )
        connection.request(
            "POST", "/api/chat",
            body=json.dumps({"spec": source, "message": "сделай ширину 410"}),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        response.read()
        connection.close()
        assert response.status == 200
        event = json.loads(
            (tmp_path / "out" / ".rollout" / "state.json").read_text(
                encoding="utf-8"
            )
        )["events"][-1]
        assert event["source"] == "shadow_candidate"
        assert event["total_tokens"] == 987
        assert event["cost_usd"] == pytest.approx(0.123)
        assert event["result_code"] == "candidate_code"
        assert event["edit_success"] is True
        assert event["paramspec_equal"] is True
        assert event["geometry_equal"] is True
        assert event["drilling_equal"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_reviewer_counterexample_storage_failure_reports_legacy_everywhere(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from src.studio import _Studio, make_handler
    import src.telemetry as telemetry

    for component in COMPONENTS:
        monkeypatch.setenv(f"AKEDA_ROLLOUT_{component.upper()}", "on")
    spec_path = tmp_path / "product.json"
    source = _spec()
    spec_path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    studio = _Studio(spec_path, tmp_path / "out")
    assert studio.ai_graph is not None
    studio.ai_graph.checkpoint_retention.max_bytes = 1
    attributes: list[dict] = []
    monkeypatch.setattr(telemetry, "add_current_attributes", attributes.append)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(studio))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=20
        )
        connection.request(
            "POST", "/api/chat",
            body=json.dumps({"spec": source, "message": "сделай ширину 410"}),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        payload = json.loads(response.read())
        connection.close()
        assert response.status == 200
        assert payload["rollout"]["primary"] == "legacy"
        assert payload["rollout"]["shadow"] is False
        assert payload["rollout"]["canary"] is False
        assert payload["rollout"]["checkpoint_degraded"] is True
        assert studio.ai_graph.storage_degraded is True
        assert any(
            item.get("rollout.primary") == "legacy"
            and item.get("checkpoint.degraded") is True
            for item in attributes
        )
        assert studio.rollout.plan(None, None).primary == "legacy"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        studio.ai_graph._sqlite_connection.close()


def test_slo_violation_stops_canary_and_rolls_future_requests_back(tmp_path: Path) -> None:
    budgets = _strict_budgets()
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


def test_reviewer_counterexample_slo_isolated_by_mode_tenant_and_cohort(
    tmp_path: Path,
) -> None:
    store = RolloutStateStore(tmp_path)
    canary = RolloutController(
        tmp_path,
        config=RolloutConfig(
            modes={name: "canary" for name in COMPONENTS},
            killed=frozenset(),
            canary_tenants=frozenset({"tenant-a", "tenant-b"}),
            canary_users=frozenset(),
            canary_percent=0,
        ),
        budgets=_strict_budgets(),
        store=store,
    )
    plan_a = canary.plan("tenant-a", "user-a")
    plan_b = canary.plan("tenant-b", "user-b")
    for _ in range(2):
        canary.record(
            RolloutMetric(latency_ms=50, edit_success=True),
            tenant_id="tenant-a", user_id="user-a", plan=plan_a,
        )
    assert canary.plan("tenant-a", "user-a").primary == "legacy"
    assert canary.plan("tenant-b", "user-b").primary == "graph"

    shadow = RolloutController(
        tmp_path,
        config=_config(langgraph="shadow"),
        budgets=_strict_budgets(),
        store=store,
    )
    shadow_plan = shadow.plan("tenant-a", "user-a")
    assert shadow_plan.scope_key != plan_a.scope_key
    assert shadow_plan.shadow is True


def test_reviewer_counterexample_persistent_stop_survives_healthy_window(
    tmp_path: Path,
) -> None:
    controller = RolloutController(
        tmp_path, config=_config(), budgets=_strict_budgets()
    )
    plan = controller.plan("tenant", "user")
    for _ in range(2):
        dashboard = controller.record(
            RolloutMetric(latency_ms=50, edit_success=True),
            tenant_id="tenant", user_id="user", plan=plan,
        )
    assert dashboard["stopped"] is True
    for _ in range(10):
        dashboard = controller.record(
            RolloutMetric(latency_ms=1, edit_success=True),
            tenant_id="tenant", user_id="user", plan=plan,
        )
    assert dashboard["summary"]["latency_p95_ms"] == 1
    assert dashboard["stopped"] is True
    assert "latency_p95" in dashboard["reasons"]


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


def test_reviewer_counterexample_missing_costs_are_not_zero_samples(
    tmp_path: Path,
) -> None:
    controller = RolloutController(
        tmp_path, config=_config(), budgets=_strict_budgets(minimum_samples=20)
    )
    plan = controller.plan("tenant", "user")
    for _ in range(19):
        dashboard = controller.record(
            RolloutMetric(latency_ms=1, cost_usd=None, edit_success=True),
            tenant_id="tenant", user_id="user", plan=plan,
        )
    dashboard = controller.record(
        RolloutMetric(latency_ms=1, cost_usd=0.50, edit_success=True),
        tenant_id="tenant", user_id="user", plan=plan,
    )
    assert dashboard["summary"]["cost_samples"] == 1
    assert dashboard["summary"]["cost_usd_p95"] == pytest.approx(0.50)
    assert dashboard["summary"]["cost_sample_status"] == "insufficient_samples"
    assert dashboard["stopped"] is False

    immediate = RolloutController(
        tmp_path / "immediate",
        config=_config(),
        budgets=_strict_budgets(minimum_samples=1),
    )
    immediate_plan = immediate.plan("tenant", "user")
    stopped = immediate.record(
        RolloutMetric(latency_ms=1, cost_usd=1.50, edit_success=True),
        tenant_id="tenant", user_id="user", plan=immediate_plan,
    )
    assert stopped["summary"]["cost_samples"] == 1
    assert "cost_p95" in stopped["reasons"]


def test_false_rejection_requires_meb151_label_and_live_divergence_is_separate(
    tmp_path: Path,
) -> None:
    controller = RolloutController(
        tmp_path, config=_config(), budgets=_strict_budgets(minimum_samples=1)
    )
    plan = controller.plan("tenant", "user")
    with pytest.raises(ValueError, match="MEB-151"):
        controller.record(
            RolloutMetric(latency_ms=1, false_rejection=True),
            tenant_id="tenant", user_id="user", plan=plan,
        )
    dashboard = controller.record(
        RolloutMetric(latency_ms=1, live_divergence=True, edit_success=True),
        tenant_id="tenant", user_id="user", plan=plan,
    )
    assert dashboard["summary"]["live_divergence_rate"] == 1
    assert dashboard["summary"]["false_rejection_samples"] == 0
    assert dashboard["summary"]["false_rejection_rate"] is None


def test_meb151_security_refusal_is_not_a_false_rejection(tmp_path: Path) -> None:
    from src.trace_replay import run_dataset

    report = run_dataset()
    refusal = next(case for case in report["cases"] if case["id"] == "prompt-injection-refusal")
    assert refusal["evaluation_label"] == {
        "source": "MEB-151",
        "expected_status": "rejected",
        "candidate_status": "rejected",
        "false_rejection": False,
    }
    controller = RolloutController(
        tmp_path, config=_config(), budgets=_strict_budgets(minimum_samples=1)
    )
    plan = controller.plan(None, None)
    dashboard = controller.record_eval_case(
        refusal, tenant_id=None, user_id=None, plan=plan
    )
    assert dashboard["summary"]["false_rejection_samples"] == 1
    assert dashboard["summary"]["false_rejection_rate"] == 0
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


def test_reviewer_counterexample_revision_flush_is_atomic_and_latches_degraded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    durable = StudioGraphOrchestrator.durable(tmp_path)
    assert isinstance(durable.buffered_revisions, BufferedRevisionStore)
    monkeypatch.setattr(
        durable.buffered_revisions.backing,
        "persist_many",
        lambda _rows: (_ for _ in ()).throw(OSError("disk unavailable")),
    )
    try:
        with pytest.raises(CheckpointStorageError, match="revision_storage:OSError"):
            durable.run(
                project_key="atomic-flush",
                spec=_spec(),
                message="сделай ширину 410",
                generation="flush-failure",
            )
        assert durable.storage_degraded is True
        assert durable.storage_error == "revision_storage:OSError"
        assert not (tmp_path / "revisions.json").exists()
    finally:
        durable._sqlite_connection.close()


def test_reviewer_counterexample_revision_read_error_fails_closed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "revisions.json"
    path.write_text("not-json", encoding="utf-8")
    store = JsonRevisionStore(path)
    with pytest.raises(CheckpointStorageError, match="revision_storage:JSONDecodeError"):
        store.persist("project", {"generation": "must-not-overwrite"})
    assert path.read_text(encoding="utf-8") == "not-json"


def test_reviewer_counterexample_orphan_checkpoint_is_cleaned_after_crash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("AKEDA_CHECKPOINT_ORPHAN_GRACE_SECONDS", "0")
    durable = StudioGraphOrchestrator.durable(tmp_path)
    assert durable.checkpoint_retention is not None

    def crash_before_touch(*_args: object, **_kwargs: object) -> dict:
        raise CheckpointStorageError("checkpoint_storage:simulated_crash")

    monkeypatch.setattr(durable.checkpoint_retention, "acquire", lambda *_args: None)
    monkeypatch.setattr(durable.checkpoint_retention, "touch", crash_before_touch)
    with pytest.raises(CheckpointStorageError, match="simulated_crash"):
        durable.run(
            project_key="orphan",
            spec=_spec(),
            message="сделай ширину 410",
            generation="crash-before-touch",
        )
    orphan_count = durable._sqlite_connection.execute(
        "SELECT COUNT(*) FROM checkpoints"
    ).fetchone()[0]
    assert orphan_count > 0
    durable._sqlite_connection.close()

    recovered = StudioGraphOrchestrator.durable(tmp_path)
    try:
        assert recovered.storage_degraded is False
        assert recovered._sqlite_connection.execute(
            "SELECT COUNT(*) FROM checkpoints"
        ).fetchone()[0] == 0
        assert recovered._sqlite_connection.execute(
            "SELECT COUNT(*) FROM writes"
        ).fetchone()[0] == 0
    finally:
        recovered._sqlite_connection.close()


def test_two_request_checkpoint_race_preserves_actively_leased_thread(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("AKEDA_CHECKPOINT_MAX_THREADS", "1")
    durable = StudioGraphOrchestrator.durable(tmp_path)
    assert durable.checkpoint_retention is not None
    first_thread = durable._thread_id("first-project", "first-generation")
    first_written = threading.Event()
    allow_first_to_finish = threading.Event()
    original_invoke = durable.graph.invoke

    def invoke_with_race(payload: object, *, config: dict) -> dict:
        result = original_invoke(payload, config=config)
        if config["configurable"]["thread_id"] == first_thread:
            first_written.set()
            assert allow_first_to_finish.wait(timeout=20)
        return result

    monkeypatch.setattr(durable.graph, "invoke", invoke_with_race)
    first_result: dict[str, object] = {}
    first_error: list[BaseException] = []

    def run_first() -> None:
        try:
            first_result.update(durable.run(
                project_key="first-project",
                spec=_spec(),
                message="сделай ширину 410",
                generation="first-generation",
            ))
        except BaseException as error:  # pragma: no cover - asserted below
            first_error.append(error)

    worker = threading.Thread(target=run_first)
    worker.start()
    try:
        assert first_written.wait(timeout=20)
        assert durable._sqlite_connection.execute(
            "SELECT lease_until FROM akeda_checkpoint_threads WHERE thread_id=?",
            (first_thread,),
        ).fetchone()[0] > int(__import__("time").time())
        before = durable._sqlite_connection.execute(
            "SELECT COUNT(*) FROM checkpoints WHERE thread_id=?", (first_thread,)
        ).fetchone()[0]
        assert before > 0
        durable.run(
            project_key="second-project",
            spec=_spec(),
            message="сделай ширину 420",
            generation="second-generation",
        )
        assert durable._sqlite_connection.execute(
            "SELECT COUNT(*) FROM checkpoints WHERE thread_id=?", (first_thread,)
        ).fetchone()[0] == before
    finally:
        allow_first_to_finish.set()
        worker.join(timeout=20)
        durable._sqlite_connection.close()
    assert not first_error
    assert first_result["spec"]["dimensions"]["width"] == 410
