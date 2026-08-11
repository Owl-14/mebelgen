from __future__ import annotations

import json
import http.client
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from src.studio_graph import (
    MAX_REPAIR_ITERATIONS,
    PROMPT_VERSION,
    CompatibilityOperationAdapter,
    DefaultContextCollector,
    GenerationGuard,
    GraphAdapters,
    GraphConflict,
    GraphRevisionError,
    GraphState,
    MemoryRevisionStore,
    NoopRepairPlanner,
    RuleBasedPlanner,
    StudioGraphOrchestrator,
    spec_revision,
)


def _spec(width: float = 600) -> dict:
    return {
        "schemaVersion": "paramspec-v1",
        "project_name": "Graph test",
        "furniture_type": "тумба",
        "archetype": "corpus",
        "dimensions": {"width": width, "depth": 450, "height": 700},
        "materials": {"board_thickness": 16, "board_material": "ЛДСП"},
        "sections": [],
    }


class Intent:
    def __init__(self, value: str = "llm") -> None:
        self.value = value
        self.calls = 0

    def classify(self, state: GraphState) -> str:
        self.calls += 1
        return self.value


class Vision:
    def __init__(self, facts: str = "") -> None:
        self.facts = facts
        self.calls = 0

    def extract(self, state: GraphState) -> str:
        self.calls += 1
        return self.facts


class Planner:
    def __init__(self, operations: list[dict] | None = None) -> None:
        self.operations = operations or [
            {"type": "set_param", "path": "dimensions.width", "value": 700}
        ]
        self.calls = 0

    def plan(self, state: GraphState) -> dict:
        self.calls += 1
        return {
            "operations": self.operations,
            "reply": "planned",
            "changes": ["width"],
            "usage": {"total": 3},
        }


class Engine:
    def __init__(self, failures: int = 0, *, always_fail: bool = False) -> None:
        self.failures = failures
        self.always_fail = always_fail
        self.generated: list[dict] = []
        self.drilled: list[dict] = []
        self.gate_calls = 0

    def validate_paramspec(self, spec) -> list[str]:
        return [] if spec.get("schemaVersion") == "paramspec-v1" else ["schema"]

    def generate_geometry(self, spec) -> dict:
        value = dict(spec)
        self.generated.append(value)
        return {"project_name": value.get("project_name"), "panels": []}

    def compute_drilling(self, project) -> list[dict]:
        self.drilled.append(dict(project))
        return []

    def quality_gates(self, spec, project, drilling) -> dict[str, list[str]]:
        self.gate_calls += 1
        if self.always_fail or self.gate_calls <= self.failures:
            return {"geometry": ["gate failed"]}
        return {"geometry": []}


class Repair:
    def __init__(self, *, empty: bool = False) -> None:
        self.empty = empty
        self.calls = 0

    def repair(self, state: GraphState) -> list[dict]:
        self.calls += 1
        if self.empty:
            return []
        return [
            {
                "type": "set_param",
                "path": "dimensions.width",
                "value": 700 + self.calls,
            }
        ]


def _adapters(
    *,
    intent: Intent | None = None,
    planner: Planner | None = None,
    engine: Engine | None = None,
    repair: Repair | None = None,
    revisions: MemoryRevisionStore | None = None,
) -> GraphAdapters:
    operations = CompatibilityOperationAdapter()
    return GraphAdapters(
        intent=intent or Intent(),
        context=DefaultContextCollector(),
        vision=Vision(),
        planner=planner or Planner(),
        validator=operations,
        applier=operations,
        engine=engine or Engine(),
        repair=repair or Repair(empty=True),
        revisions=revisions or MemoryRevisionStore(),
    )


def test_graph_state_declares_required_durable_contract() -> None:
    fields = GraphState.__annotations__
    for required in (
        "revisions",
        "prompt_version",
        "operations",
        "check_results",
        "input_revision",
        "output_revision",
        "generation",
    ):
        assert required in fields
    assert PROMPT_VERSION


def test_happy_path_covers_every_normal_transition_and_persists_revision() -> None:
    revisions = MemoryRevisionStore()
    graph = StudioGraphOrchestrator(_adapters(revisions=revisions))

    result = graph.run(
        project_key="tenant/product",
        spec=_spec(),
        message="change it",
        generation="generation-1",
    )

    expected = [
        "ingest",
        "classify_intent",
        "collect_context",
        "vision_extract",
        "plan_operations",
        "validate_operations",
        "deterministic_apply",
        "validate_paramspec",
        "generate_geometry",
        "compute_drilling",
        "quality_gates",
        "approval",
        "persist_revision",
        "summarize",
    ]
    assert result["graph"]["nodes"] == expected
    assert result["graph"]["status"] == "completed"
    assert result["spec"]["dimensions"]["width"] == 700
    assert result["spec"]["schemaVersion"] == "paramspec-v1"
    assert result["graph"]["input_revision"] != result["graph"]["output_revision"]
    assert len(result["graph"]["revisions"]) == 2
    assert revisions.rows["tenant/product"][0]["prompt_version"] == PROMPT_VERSION


def test_quality_to_bounded_repair_to_validation_and_approval_transition() -> None:
    engine = Engine(failures=1)
    repair = Repair()
    graph = StudioGraphOrchestrator(_adapters(engine=engine, repair=repair))

    result = graph.run(
        project_key="p",
        spec=_spec(),
        message="complex",
        generation="repair-once",
    )

    nodes = result["graph"]["nodes"]
    repair_index = nodes.index("bounded_repair")
    assert nodes[repair_index - 1 : repair_index + 2] == [
        "quality_gates",
        "bounded_repair",
        "validate_operations",
    ]
    assert nodes[-3:] == ["approval", "persist_revision", "summarize"]
    assert result["graph"]["repair_count"] == 1
    assert repair.calls == 1


def test_bounded_repair_never_exceeds_two_iterations() -> None:
    engine = Engine(always_fail=True)
    repair = Repair()
    graph = StudioGraphOrchestrator(_adapters(engine=engine, repair=repair))

    result = graph.run(
        project_key="p",
        spec=_spec(),
        message="unrepairable",
        generation="repair-two",
        max_repairs=20,
    )

    assert MAX_REPAIR_ITERATIONS == 2
    assert result["graph"]["nodes"].count("bounded_repair") == 2
    assert result["graph"]["repair_count"] == 2
    assert repair.calls == 2
    assert result["spec"] is None
    assert result["graph"]["status"] == "rejected"


def test_failed_gate_with_no_repair_transitions_directly_to_approval() -> None:
    graph = StudioGraphOrchestrator(
        _adapters(engine=Engine(always_fail=True), repair=Repair(empty=True))
    )
    result = graph.run(
        project_key="p", spec=_spec(), message="bad", generation="no-repair"
    )
    nodes = result["graph"]["nodes"]
    index = nodes.index("bounded_repair")
    assert nodes[index : index + 3] == ["bounded_repair", "approval", "summarize"]
    assert "persist_revision" not in nodes


def test_rule_based_dimension_command_does_not_call_llm_planner(monkeypatch) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("LLM path must not run for a simple command")

    monkeypatch.setattr("src.spec_chat.chat_edit", forbidden)
    graph = StudioGraphOrchestrator()
    result = graph.run(
        project_key="real",
        spec=_spec(),
        message="Сделай ширину 750 и высоту 800",
        generation="rule",
    )
    assert result["spec"]["dimensions"]["width"] == 750
    assert result["spec"]["dimensions"]["height"] == 800
    assert result["usage"] == {}


def test_complex_command_uses_injected_planner_only_when_needed() -> None:
    intent = Intent("llm")
    planner = Planner()
    graph = StudioGraphOrchestrator(_adapters(intent=intent, planner=planner))
    result = graph.run(
        project_key="p", spec=_spec(), message="add shelves", generation="llm"
    )
    assert planner.calls == 1
    assert result["usage"] == {"total": 3}


def test_checkpoint_interrupt_and_safe_resume() -> None:
    graph = StudioGraphOrchestrator(_adapters())
    paused = graph.run(
        project_key="p",
        spec=_spec(),
        message="approve me",
        generation="approval",
        approval_required=True,
    )
    assert paused["paused"] is True
    assert paused["interrupts"][0]["operations"]

    resumed = graph.resume(paused["thread_id"], approved=True)
    assert resumed["graph"]["status"] == "completed"
    assert resumed["spec"]["dimensions"]["width"] == 700
    assert resumed["graph"]["nodes"][-3:] == [
        "approval",
        "persist_revision",
        "summarize",
    ]


def test_durable_checkpoint_resumes_after_orchestrator_recreation(tmp_path: Path) -> None:
    first = StudioGraphOrchestrator.durable(tmp_path)
    paused = first.run(
        project_key="p",
        spec=_spec(),
        message="Сделай ширину 710",
        generation="durable",
        approval_required=True,
    )
    first._sqlite_connection.close()

    second = StudioGraphOrchestrator.durable(tmp_path)
    try:
        resumed = second.resume(paused["thread_id"], approved=True)
        assert resumed["spec"]["dimensions"]["width"] == 710
        persisted = json.loads((tmp_path / "revisions.json").read_text(encoding="utf-8"))
        assert persisted["p"][0]["generation"] == "durable"
    finally:
        second._sqlite_connection.close()


def test_cancellation_is_checkpointed_and_skips_mutating_nodes() -> None:
    graph = StudioGraphOrchestrator(
        _adapters(), cancellation_probe=lambda generation: generation == "cancel-me"
    )
    result = graph.run(
        project_key="p", spec=_spec(), message="stop", generation="cancel-me"
    )
    assert result["code"] == "operation_cancelled"
    assert result["graph"]["nodes"] == ["ingest", "summarize"]
    assert result["spec"] is None


def test_generation_guard_rejects_parallel_request_for_same_product() -> None:
    entered = threading.Event()
    release = threading.Event()

    class SlowPlanner(Planner):
        def plan(self, state: GraphState) -> dict:
            entered.set()
            assert release.wait(5)
            return super().plan(state)

    graph = StudioGraphOrchestrator(
        _adapters(intent=Intent("llm"), planner=SlowPlanner()),
        generation_guard=GenerationGuard(),
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            graph.run,
            project_key="same",
            spec=_spec(),
            message="first",
            generation="first",
        )
        assert entered.wait(5)
        with pytest.raises(GraphConflict):
            graph.run(
                project_key="same",
                spec=_spec(),
                message="second",
                generation="second",
            )
        release.set()
        assert first.result(timeout=10)["graph"]["status"] == "completed"


def test_revision_guard_rejects_stale_request_and_unknown_resume() -> None:
    graph = StudioGraphOrchestrator(_adapters())
    with pytest.raises(GraphRevisionError):
        graph.run(
            project_key="p",
            spec=_spec(),
            message="stale",
            generation="stale",
            expected_revision=spec_revision(_spec(601)),
        )
    with pytest.raises(GraphRevisionError):
        graph.resume("missing", approved=True)


def test_generation_retry_is_idempotent_and_cannot_switch_revision() -> None:
    planner = Planner()
    graph = StudioGraphOrchestrator(_adapters(planner=planner))
    first = graph.run(
        project_key="p", spec=_spec(), message="edit", generation="retry"
    )
    second = graph.run(
        project_key="p", spec=_spec(), message="edit", generation="retry"
    )
    assert second == first
    assert planner.calls == 1
    with pytest.raises(GraphRevisionError):
        graph.run(
            project_key="p", spec=_spec(601), message="edit", generation="retry"
        )


def test_operation_adapter_is_a_narrow_protocol_seam() -> None:
    adapter = CompatibilityOperationAdapter()
    operations = [
        {"type": "set_param", "path": "dimensions.depth", "value": 500}
    ]
    assert adapter.validate(_spec(), operations) == []
    assert adapter.apply(_spec(), operations)["dimensions"]["depth"] == 500
    assert adapter.validate(_spec(), [{"type": "geometry_magic"}])


def test_feature_flag_keeps_studio_default_off_and_can_enable_durable_graph(
    tmp_path: Path, monkeypatch
) -> None:
    from src.studio import _Studio

    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(_spec(), ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()

    monkeypatch.delenv("STUDIO_LANGGRAPH_ORCHESTRATION", raising=False)
    legacy = _Studio(spec_path, out)
    assert legacy.ai_graph is None

    monkeypatch.setenv("STUDIO_LANGGRAPH_ORCHESTRATION", "1")
    enabled = _Studio(spec_path, out)
    try:
        assert enabled.ai_graph is not None
        assert (out / ".studio_graph" / "checkpoints.sqlite3").is_file()
    finally:
        enabled.ai_graph._sqlite_connection.close()


def test_graph_response_preserves_legacy_chat_fields() -> None:
    result = StudioGraphOrchestrator(_adapters()).run(
        project_key="p", spec=_spec(), message="edit", generation="contract"
    )
    assert {"reply", "spec", "changes", "usage", "created"} <= result.keys()


def test_feature_flagged_chat_http_keeps_legacy_contract(tmp_path: Path, monkeypatch) -> None:
    from src.studio import _Studio, make_handler

    source = json.loads(
        (Path(__file__).parents[1] / "paramspecs" / "wardrobe_demo.json").read_text(
            encoding="utf-8"
        )
    )
    spec_dir = tmp_path / "paramspecs"
    spec_dir.mkdir()
    spec_path = spec_dir / "wardrobe.json"
    spec_path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("STUDIO_LANGGRAPH_ORCHESTRATION", "1")
    studio = _Studio(spec_path, tmp_path / "out")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(studio))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        body = json.dumps(
            {
                "spec": source,
                "message": "Сделай ширину 1200",
                "operation_id": "http-generation",
                "history": [],
            }
        ).encode("utf-8")
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=20
        )
        connection.request(
            "POST", "/api/chat", body=body, headers={"Content-Type": "application/json"}
        )
        response = connection.getresponse()
        raw = response.read()
        connection.close()
        assert response.status == 200, raw
        result = json.loads(raw)
        assert {"reply", "spec", "changes", "usage", "created"} <= result.keys()
        assert result["spec"]["dimensions"]["width"] == 1200
        assert result["spec"]["schemaVersion"] == "paramspec-v1"
        assert result["graph"]["prompt_version"] == PROMPT_VERSION
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        studio.ai_graph._sqlite_connection.close()


def test_real_engine_keeps_paramspec_v1_and_delegates_geometry() -> None:
    source = json.loads(
        (Path(__file__).parents[1] / "paramspecs" / "wardrobe_demo.json").read_text(
            encoding="utf-8"
        )
    )
    result = StudioGraphOrchestrator().run(
        project_key="wardrobe",
        spec=source,
        message="Сделай ширину 1200",
        generation="real-engine",
    )
    assert result["spec"]["schemaVersion"] == "paramspec-v1"
    assert result["graph"]["checks"]["quality"] == []
    assert result["graph"]["status"] == "completed"


def test_rule_parser_only_accepts_explicit_dimension_commands() -> None:
    rules = RuleBasedPlanner()
    assert rules.plan_operations("Ширина 900, глубина 500") == [
        {"type": "set_param", "path": "dimensions.width", "value": 900.0},
        {"type": "set_param", "path": "dimensions.depth", "value": 500.0},
    ]
    assert rules.plan_operations("сделай современнее") == []
    assert rules.plan_operations("увеличь ширину на 100") == []
