"""LangGraph orchestration for Studio AI edits.

The graph coordinates existing deterministic services.  It deliberately owns
no furniture geometry: ParamSpec validation, generation, drilling and quality
checks are delegated through :class:`DeterministicEngine`.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Protocol, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt


PROMPT_VERSION = "studio-langgraph-v1"
MAX_REPAIR_ITERATIONS = 2


class GraphState(TypedDict, total=False):
    """Serializable state contract persisted by LangGraph checkpoints."""

    thread_id: str
    generation: str
    project_key: str
    prompt_version: str
    source_spec: dict[str, Any]
    spec: dict[str, Any]
    message: str
    history: list[dict[str, Any]]
    context: dict[str, Any]
    images: list[dict[str, Any]]
    provider: str | None
    intent: str
    route: str
    vision_facts: str
    operations: list[dict[str, Any]]
    check_results: dict[str, list[str]]
    revisions: list[dict[str, Any]]
    input_revision: str
    output_revision: str
    project: dict[str, Any]
    drilling: list[dict[str, Any]]
    repair_count: int
    max_repairs: int
    approval_required: bool
    approved: bool
    cancelled: bool
    status: str
    reply: str
    changes: list[str]
    usage: dict[str, Any]
    created: bool
    node_history: list[str]


class IntentClassifier(Protocol):
    def classify(self, state: GraphState) -> str: ...


class ContextCollector(Protocol):
    def collect(self, state: GraphState) -> dict[str, Any]: ...


class VisionExtractor(Protocol):
    def extract(self, state: GraphState) -> str: ...


class OperationPlanner(Protocol):
    def plan(self, state: GraphState) -> dict[str, Any]: ...


class OperationValidator(Protocol):
    def validate(
        self, spec: Mapping[str, Any], operations: list[dict[str, Any]]
    ) -> list[str]: ...


class OperationApplier(Protocol):
    def apply(
        self, spec: Mapping[str, Any], operations: list[dict[str, Any]]
    ) -> dict[str, Any]: ...


class DeterministicEngine(Protocol):
    def validate_paramspec(self, spec: Mapping[str, Any]) -> list[str]: ...

    def generate_geometry(self, spec: Mapping[str, Any]) -> dict[str, Any]: ...

    def compute_drilling(self, project: Mapping[str, Any]) -> list[dict[str, Any]]: ...

    def quality_gates(
        self,
        spec: Mapping[str, Any],
        project: Mapping[str, Any],
        drilling: list[dict[str, Any]],
    ) -> dict[str, list[str]]: ...


class RepairPlanner(Protocol):
    def repair(self, state: GraphState) -> list[dict[str, Any]]: ...


class RevisionStore(Protocol):
    def persist(self, project_key: str, revision: Mapping[str, Any]) -> None: ...


class GraphConflict(RuntimeError):
    """Another generation is already mutating the same Studio product."""


class GraphRevisionError(RuntimeError):
    """A request or resumed checkpoint references a different ParamSpec."""


def spec_revision(spec: Mapping[str, Any]) -> str:
    payload = json.dumps(
        spec, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class GenerationGuard:
    """Process-local single-writer guard keyed by tenant product."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._active: dict[str, tuple[str, str]] = {}

    @contextmanager
    def acquire(
        self, project_key: str, generation: str, expected_revision: str
    ) -> Iterator[None]:
        with self._lock:
            active = self._active.get(project_key)
            if active is not None and active[0] != generation:
                raise GraphConflict(
                    "Для этого изделия уже выполняется другая AI-команда"
                )
            self._active[project_key] = (generation, expected_revision)
        try:
            yield
        finally:
            with self._lock:
                if self._active.get(project_key) == (generation, expected_revision):
                    self._active.pop(project_key, None)


class JsonRevisionStore:
    """Small durable audit of graph revisions; it never stores geometry."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def persist(self, project_key: str, revision: Mapping[str, Any]) -> None:
        with self._lock:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                data = {}
            rows = list(data.get(project_key) or [])
            rows.append(dict(revision))
            data[project_key] = rows[-100:]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(
                f".{self.path.name}.{uuid.uuid4().hex[:10]}.tmp"
            )
            try:
                temporary.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                temporary.replace(self.path)
            finally:
                if temporary.exists():
                    temporary.unlink()


class MemoryRevisionStore:
    def __init__(self) -> None:
        self.rows: dict[str, list[dict[str, Any]]] = {}
        self._lock = threading.RLock()

    def persist(self, project_key: str, revision: Mapping[str, Any]) -> None:
        with self._lock:
            self.rows.setdefault(project_key, []).append(dict(revision))


_DIMENSION_PATTERNS = {
    "width": re.compile(r"(?:ширин\w*|шире)\s*(?:до|=|:)?\s*(\d+(?:[.,]\d+)?)", re.I),
    "height": re.compile(r"(?:высот\w*|выше)\s*(?:до|=|:)?\s*(\d+(?:[.,]\d+)?)", re.I),
    "depth": re.compile(r"(?:глубин\w*|глубже)\s*(?:до|=|:)?\s*(\d+(?:[.,]\d+)?)", re.I),
}


class RuleBasedPlanner:
    """Deterministic fast path for unambiguous dimension commands."""

    def plan_operations(self, message: str) -> list[dict[str, Any]]:
        operations: list[dict[str, Any]] = []
        for field, pattern in _DIMENSION_PATTERNS.items():
            match = pattern.search(message or "")
            if match:
                value = float(match.group(1).replace(",", "."))
                operations.append(
                    {"type": "set_param", "path": f"dimensions.{field}", "value": value}
                )
        return operations


class DefaultIntentClassifier:
    def __init__(self, rules: RuleBasedPlanner) -> None:
        self.rules = rules

    def classify(self, state: GraphState) -> str:
        if not state.get("images") and self.rules.plan_operations(state.get("message", "")):
            return "rule"
        return "llm"


class DefaultContextCollector:
    def collect(self, state: GraphState) -> dict[str, Any]:
        return copy.deepcopy(state.get("context") or {})


class ExistingVisionExtractor:
    """Use the configured vision provider only for the existing two-stage path."""

    def extract(self, state: GraphState) -> str:
        images = state.get("images") or []
        if not images or state.get("route") == "rule":
            return ""
        from .spec_chat import get_chat_provider, resolve_provider_name

        build_name = resolve_provider_name(state.get("provider"))
        extract_name = (os.environ.get("VISION_EXTRACT_PROVIDER") or "").lower()
        if not extract_name or extract_name == build_name:
            return ""
        try:
            provider = get_chat_provider(extract_name)
            if not hasattr(provider, "vision_extract"):
                return ""
            return str(provider.vision_extract(images) or "")
        except Exception:
            # Preserve the old fallback: if the dedicated vision stage fails,
            # the builder receives the images and reports provider errors itself.
            return ""


class ExistingChatPlanner:
    """Adapter around the current Studio chat contract."""

    def __init__(self, rules: RuleBasedPlanner) -> None:
        self.rules = rules

    def plan(self, state: GraphState) -> dict[str, Any]:
        if state.get("route") == "rule":
            return {
                "operations": self.rules.plan_operations(state.get("message", "")),
                "reply": "Готово.",
                "changes": [],
                "usage": {},
                "created": False,
            }
        from .spec_chat import chat_edit

        message = state.get("message", "")
        images: list[dict[str, Any]] | None = state.get("images") or None
        facts = state.get("vision_facts", "").strip()
        if facts:
            message += (
                "\n\nРаспознано с фото ТЗ (используй как факты, ничего не "
                "додумывай сверх):\n" + facts
            )
            images = None
        result = chat_edit(
            copy.deepcopy(state.get("source_spec") or {}),
            message,
            copy.deepcopy(state.get("history") or []),
            copy.deepcopy(state.get("context") or {}),
            images,
            state.get("provider"),
        )
        new_spec = result.get("spec")
        operations = (
            [{"type": "replace_spec", "spec": new_spec}]
            if isinstance(new_spec, dict)
            else []
        )
        return {
            "operations": operations,
            "reply": str(result.get("reply") or ""),
            "changes": list(result.get("changes") or []),
            "usage": dict(result.get("usage") or {}),
            "created": bool(result.get("created")),
            "error": str(result.get("error") or ""),
        }


class CompatibilityOperationAdapter:
    """Temporary seam for MEB-143/MEB-144 operation and EditEngine work."""

    _SUPPORTED = {"set_param", "replace_spec", "noop"}

    def validate(
        self, spec: Mapping[str, Any], operations: list[dict[str, Any]]
    ) -> list[str]:
        errors: list[str] = []
        for index, operation in enumerate(operations):
            kind = str(operation.get("type") or "")
            if kind not in self._SUPPORTED:
                errors.append(f"operations.{index}: неизвестная операция {kind!r}")
            elif kind == "replace_spec" and not isinstance(operation.get("spec"), dict):
                errors.append(f"operations.{index}: replace_spec требует spec")
            elif kind == "set_param":
                path = str(operation.get("path") or "")
                if path not in {"dimensions.width", "dimensions.height", "dimensions.depth"}:
                    errors.append(f"operations.{index}: путь {path!r} не разрешён")
                try:
                    if float(operation.get("value")) <= 0:
                        raise ValueError
                except (TypeError, ValueError):
                    errors.append(f"operations.{index}: значение должно быть положительным")
        return errors

    def apply(
        self, spec: Mapping[str, Any], operations: list[dict[str, Any]]
    ) -> dict[str, Any]:
        result = copy.deepcopy(dict(spec))
        for operation in operations:
            kind = operation.get("type")
            if kind == "replace_spec":
                result = copy.deepcopy(operation["spec"])
            elif kind == "set_param":
                _, field = str(operation["path"]).split(".", 1)
                result.setdefault("dimensions", {})[field] = operation["value"]
        return result


class ExistingDeterministicEngine:
    """Delegates every production calculation to the current Studio engine."""

    def validate_paramspec(self, spec: Mapping[str, Any]) -> list[str]:
        from .paramspec import validate_paramspec

        return list(validate_paramspec(dict(spec)) or [])

    def generate_geometry(self, spec: Mapping[str, Any]) -> dict[str, Any]:
        from .generators import generate_from_paramspec

        return generate_from_paramspec(dict(spec))

    def compute_drilling(self, project: Mapping[str, Any]) -> list[dict[str, Any]]:
        from .hardware import compute_drilling

        return compute_drilling(dict(project))

    def quality_gates(
        self,
        spec: Mapping[str, Any],
        project: Mapping[str, Any],
        drilling: list[dict[str, Any]],
    ) -> dict[str, list[str]]:
        # build_payload is the existing Studio production-gate aggregation.  It
        # remains the source of truth until MEB-146 supplies a dedicated adapter.
        from .studio import build_payload

        payload = build_payload(dict(spec))
        return {
            str(name): [str(item) for item in (items or [])]
            for name, items in (payload.get("issues") or {}).items()
        }


class NoopRepairPlanner:
    def repair(self, state: GraphState) -> list[dict[str, Any]]:
        return []


@dataclass
class GraphAdapters:
    intent: IntentClassifier
    context: ContextCollector
    vision: VisionExtractor
    planner: OperationPlanner
    validator: OperationValidator
    applier: OperationApplier
    engine: DeterministicEngine
    repair: RepairPlanner
    revisions: RevisionStore

    @classmethod
    def defaults(cls, revision_store: RevisionStore | None = None) -> "GraphAdapters":
        rules = RuleBasedPlanner()
        operations = CompatibilityOperationAdapter()
        return cls(
            intent=DefaultIntentClassifier(rules),
            context=DefaultContextCollector(),
            vision=ExistingVisionExtractor(),
            planner=ExistingChatPlanner(rules),
            validator=operations,
            applier=operations,
            engine=ExistingDeterministicEngine(),
            repair=NoopRepairPlanner(),
            revisions=revision_store or MemoryRevisionStore(),
        )


class StudioGraphOrchestrator:
    """Compiled LangGraph plus safe run/resume helpers for Studio."""

    nodes = (
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
        "bounded_repair",
        "approval",
        "persist_revision",
        "summarize",
    )

    def __init__(
        self,
        adapters: GraphAdapters | None = None,
        *,
        checkpointer: Any | None = None,
        cancellation_probe: Callable[[str], bool] | None = None,
        generation_guard: GenerationGuard | None = None,
    ) -> None:
        self.adapters = adapters or GraphAdapters.defaults()
        self.cancellation_probe = cancellation_probe or (lambda generation: False)
        self.generation_guard = generation_guard or GenerationGuard()
        self.checkpointer = checkpointer or InMemorySaver()
        self.graph = self._build().compile(checkpointer=self.checkpointer)

    @classmethod
    def durable(
        cls,
        root: Path,
        *,
        cancellation_probe: Callable[[str], bool] | None = None,
    ) -> "StudioGraphOrchestrator":
        from langgraph.checkpoint.sqlite import SqliteSaver

        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            root / "checkpoints.sqlite3", check_same_thread=False
        )
        saver = SqliteSaver(connection)
        saver.setup()
        revisions = JsonRevisionStore(root / "revisions.json")
        instance = cls(
            GraphAdapters.defaults(revisions),
            checkpointer=saver,
            cancellation_probe=cancellation_probe,
        )
        instance._sqlite_connection = connection
        return instance

    def _build(self) -> StateGraph:
        builder = StateGraph(GraphState)
        for name in self.nodes:
            builder.add_node(name, getattr(self, f"_{name}"))
        builder.add_edge(START, "ingest")
        chain = self.nodes[: self.nodes.index("quality_gates") + 1]
        for current, following in zip(chain, chain[1:]):
            builder.add_conditional_edges(
                current,
                lambda state, next_node=following: self._route_cancel(state, next_node),
                {following: following, "summarize": "summarize"},
            )
        builder.add_conditional_edges(
            "quality_gates",
            self._after_quality,
            {"bounded_repair": "bounded_repair", "approval": "approval", "summarize": "summarize"},
        )
        builder.add_conditional_edges(
            "bounded_repair",
            self._after_repair,
            {"validate_operations": "validate_operations", "approval": "approval", "summarize": "summarize"},
        )
        builder.add_conditional_edges(
            "approval",
            lambda state: "persist_revision" if state.get("approved") else "summarize",
            {"persist_revision": "persist_revision", "summarize": "summarize"},
        )
        builder.add_edge("persist_revision", "summarize")
        builder.add_edge("summarize", END)
        return builder

    def _entered(self, state: GraphState, name: str) -> dict[str, Any]:
        update: dict[str, Any] = {
            "node_history": [*state.get("node_history", []), name]
        }
        generation = state.get("generation", "")
        if generation and self.cancellation_probe(generation):
            update.update(cancelled=True, status="cancelled")
        return update

    @staticmethod
    def _route_cancel(state: GraphState, following: str) -> str:
        return "summarize" if state.get("cancelled") else following

    @staticmethod
    def _has_gate_failures(state: GraphState) -> bool:
        return any(state.get("check_results", {}).get("quality", []))

    def _after_quality(self, state: GraphState) -> str:
        if state.get("cancelled"):
            return "summarize"
        if self._has_gate_failures(state) and state.get("repair_count", 0) < state.get("max_repairs", 2):
            return "bounded_repair"
        return "approval"

    def _after_repair(self, state: GraphState) -> str:
        if state.get("cancelled"):
            return "summarize"
        if state.get("operations"):
            return "validate_operations"
        return "approval"

    def _ingest(self, state: GraphState) -> dict[str, Any]:
        update = self._entered(state, "ingest")
        source = copy.deepcopy(state.get("source_spec") or {})
        actual_revision = spec_revision(source)
        expected = state.get("input_revision") or actual_revision
        if expected != actual_revision:
            raise GraphRevisionError("ParamSpec изменился до запуска графа")
        update.update(
            spec=source,
            input_revision=actual_revision,
            output_revision=actual_revision,
            prompt_version=state.get("prompt_version") or PROMPT_VERSION,
            operations=list(state.get("operations") or []),
            check_results=dict(state.get("check_results") or {}),
            revisions=[
                {"kind": "input", "revision": actual_revision, "generation": state.get("generation", "")}
            ],
            repair_count=0,
            max_repairs=min(MAX_REPAIR_ITERATIONS, max(0, int(state.get("max_repairs", 2)))),
            status="running",
        )
        return update

    def _classify_intent(self, state: GraphState) -> dict[str, Any]:
        update = self._entered(state, "classify_intent")
        intent = self.adapters.intent.classify(state)
        update.update(intent=intent, route="rule" if intent == "rule" else "llm")
        return update

    def _collect_context(self, state: GraphState) -> dict[str, Any]:
        update = self._entered(state, "collect_context")
        update["context"] = self.adapters.context.collect(state)
        return update

    def _vision_extract(self, state: GraphState) -> dict[str, Any]:
        update = self._entered(state, "vision_extract")
        if not update.get("cancelled"):
            update["vision_facts"] = self.adapters.vision.extract(state)
        return update

    def _plan_operations(self, state: GraphState) -> dict[str, Any]:
        update = self._entered(state, "plan_operations")
        if update.get("cancelled"):
            return update
        planned = self.adapters.planner.plan(state)
        update.update(
            operations=copy.deepcopy(planned.get("operations") or []),
            reply=str(planned.get("reply") or ""),
            changes=list(planned.get("changes") or []),
            usage=dict(planned.get("usage") or {}),
            created=bool(planned.get("created")),
        )
        error = str(planned.get("error") or "")
        if error:
            checks = dict(state.get("check_results") or {})
            checks["planning"] = [error]
            update["check_results"] = checks
        return update

    def _validate_operations(self, state: GraphState) -> dict[str, Any]:
        update = self._entered(state, "validate_operations")
        errors = self.adapters.validator.validate(
            state.get("source_spec") or {}, state.get("operations") or []
        )
        checks = dict(state.get("check_results") or {})
        checks["operations"] = errors
        update["check_results"] = checks
        return update

    def _deterministic_apply(self, state: GraphState) -> dict[str, Any]:
        update = self._entered(state, "deterministic_apply")
        if state.get("check_results", {}).get("operations"):
            return update
        spec = self.adapters.applier.apply(
            state.get("source_spec") or {}, state.get("operations") or []
        )
        update.update(spec=spec, output_revision=spec_revision(spec))
        if state.get("route") == "rule" and spec != state.get("source_spec"):
            update["changes"] = ["параметры изделия обновлены"]
        return update

    def _validate_paramspec(self, state: GraphState) -> dict[str, Any]:
        update = self._entered(state, "validate_paramspec")
        checks = dict(state.get("check_results") or {})
        checks["paramspec"] = self.adapters.engine.validate_paramspec(state.get("spec") or {})
        update["check_results"] = checks
        return update

    def _generate_geometry(self, state: GraphState) -> dict[str, Any]:
        update = self._entered(state, "generate_geometry")
        if state.get("check_results", {}).get("paramspec"):
            return update
        try:
            update["project"] = self.adapters.engine.generate_geometry(state.get("spec") or {})
        except Exception as error:
            checks = dict(state.get("check_results") or {})
            checks["generation"] = [str(error)]
            update["check_results"] = checks
        return update

    def _compute_drilling(self, state: GraphState) -> dict[str, Any]:
        update = self._entered(state, "compute_drilling")
        if not state.get("project"):
            return update
        try:
            update["drilling"] = self.adapters.engine.compute_drilling(state["project"])
        except Exception as error:
            checks = dict(state.get("check_results") or {})
            checks["drilling"] = [str(error)]
            update["check_results"] = checks
        return update

    def _quality_gates(self, state: GraphState) -> dict[str, Any]:
        update = self._entered(state, "quality_gates")
        checks = dict(state.get("check_results") or {})
        blockers = [
            item
            for name, items in checks.items()
            if name != "quality"
            for item in items
        ]
        if not blockers and state.get("project"):
            gates = self.adapters.engine.quality_gates(
                state.get("spec") or {}, state["project"], state.get("drilling") or []
            )
            blockers = [item for items in gates.values() for item in items]
            checks.update(gates)
        checks["quality"] = blockers
        update["check_results"] = checks
        return update

    def _bounded_repair(self, state: GraphState) -> dict[str, Any]:
        update = self._entered(state, "bounded_repair")
        count = state.get("repair_count", 0) + 1
        operations = self.adapters.repair.repair(state) if count <= MAX_REPAIR_ITERATIONS else []
        update.update(repair_count=min(count, MAX_REPAIR_ITERATIONS), operations=operations)
        if operations:
            update["check_results"] = {
                name: list(items)
                for name, items in state.get("check_results", {}).items()
                if name == "planning"
            }
        else:
            update["repair_count"] = state.get("max_repairs", MAX_REPAIR_ITERATIONS)
        return update

    def _approval(self, state: GraphState) -> dict[str, Any]:
        update = self._entered(state, "approval")
        blockers = state.get("check_results", {}).get("quality", [])
        if blockers:
            update.update(approved=False, status="rejected")
            return update
        if state.get("approval_required"):
            decision = interrupt(
                {
                    "generation": state.get("generation"),
                    "input_revision": state.get("input_revision"),
                    "output_revision": state.get("output_revision"),
                    "operations": state.get("operations") or [],
                    "checks": state.get("check_results") or {},
                }
            )
            approved = bool(decision.get("approved")) if isinstance(decision, dict) else bool(decision)
        else:
            approved = True
        update.update(approved=approved, status="approved" if approved else "rejected")
        return update

    def _persist_revision(self, state: GraphState) -> dict[str, Any]:
        update = self._entered(state, "persist_revision")
        if spec_revision(state.get("source_spec") or {}) != state.get("input_revision"):
            raise GraphRevisionError("Исходная редакция изменилась до сохранения")
        revision = {
            "kind": "output",
            "revision": state.get("output_revision"),
            "parent_revision": state.get("input_revision"),
            "generation": state.get("generation"),
            "prompt_version": state.get("prompt_version"),
            "operations": copy.deepcopy(state.get("operations") or []),
            "checks": copy.deepcopy(state.get("check_results") or {}),
        }
        self.adapters.revisions.persist(state.get("project_key", ""), revision)
        update.update(revisions=[*state.get("revisions", []), revision], status="persisted")
        return update

    def _summarize(self, state: GraphState) -> dict[str, Any]:
        update = self._entered(state, "summarize")
        if state.get("cancelled"):
            update.update(
                status="cancelled",
                reply="Команда остановлена",
                spec={},
                operations=[],
                changes=[],
            )
        elif not state.get("approved"):
            blockers = state.get("check_results", {}).get("quality", [])
            reply = state.get("reply") or "Правка отклонена проверками качества."
            if blockers:
                reply += "\n" + "\n".join(blockers[:5])
            update.update(status="rejected", reply=reply, spec={})
        else:
            update["status"] = "completed"
        return update

    @staticmethod
    def _thread_id(project_key: str, generation: str) -> str:
        project = hashlib.sha256(project_key.encode("utf-8")).hexdigest()[:20]
        return f"{project}:{generation}"

    def run(
        self,
        *,
        project_key: str,
        spec: Mapping[str, Any],
        message: str,
        history: list[dict[str, Any]] | None = None,
        context: dict[str, Any] | None = None,
        images: list[dict[str, Any]] | None = None,
        provider: str | None = None,
        generation: str | None = None,
        expected_revision: str | None = None,
        approval_required: bool = False,
        max_repairs: int = MAX_REPAIR_ITERATIONS,
    ) -> dict[str, Any]:
        generation = generation or uuid.uuid4().hex
        actual_revision = spec_revision(spec)
        if expected_revision and expected_revision != actual_revision:
            raise GraphRevisionError("Запрос относится к устаревшей редакции")
        thread_id = self._thread_id(project_key, generation)
        initial: GraphState = {
            "thread_id": thread_id,
            "generation": generation,
            "project_key": project_key,
            "source_spec": copy.deepcopy(dict(spec)),
            "message": str(message or ""),
            "history": copy.deepcopy(history or []),
            "context": copy.deepcopy(context or {}),
            "images": copy.deepcopy(images or []),
            "provider": provider,
            "input_revision": actual_revision,
            "prompt_version": PROMPT_VERSION,
            "approval_required": bool(approval_required),
            "max_repairs": min(MAX_REPAIR_ITERATIONS, max(0, int(max_repairs))),
        }
        config = {"configurable": {"thread_id": thread_id}}
        with self.generation_guard.acquire(project_key, generation, actual_revision):
            snapshot = self.graph.get_state(config)
            existing = dict(snapshot.values or {})
            if existing:
                if (
                    existing.get("project_key") != project_key
                    or existing.get("generation") != generation
                    or existing.get("input_revision") != actual_revision
                ):
                    raise GraphRevisionError(
                        "Generation уже связан с другой редакцией изделия"
                    )
                if snapshot.next:
                    pending = [
                        getattr(item, "value", item)
                        for task in snapshot.tasks
                        for item in getattr(task, "interrupts", ())
                    ]
                    return {
                        "ok": True,
                        "paused": True,
                        "thread_id": thread_id,
                        "interrupts": pending,
                        "prompt_version": existing.get("prompt_version"),
                    }
                return self._response(existing)
            result = self.graph.invoke(initial, config=config)
        return self._response(result)

    def resume(self, thread_id: str, *, approved: bool) -> dict[str, Any]:
        config = {"configurable": {"thread_id": thread_id}}
        snapshot = self.graph.get_state(config)
        state = dict(snapshot.values or {})
        if not state or state.get("thread_id") != thread_id:
            raise GraphRevisionError("Checkpoint для безопасного возобновления не найден")
        project_key = str(state.get("project_key") or "")
        generation = str(state.get("generation") or "")
        expected = str(state.get("input_revision") or "")
        if spec_revision(state.get("source_spec") or {}) != expected:
            raise GraphRevisionError("Checkpoint относится к другой редакции")
        with self.generation_guard.acquire(project_key, generation, expected):
            result = self.graph.invoke(
                Command(resume={"approved": bool(approved)}), config=config
            )
        return self._response(result)

    @staticmethod
    def _response(state: Mapping[str, Any]) -> dict[str, Any]:
        interrupts = state.get("__interrupt__") or []
        if interrupts:
            return {
                "ok": True,
                "paused": True,
                "thread_id": state.get("thread_id"),
                "interrupts": [getattr(item, "value", item) for item in interrupts],
                "prompt_version": state.get("prompt_version"),
            }
        completed = state.get("status") == "completed"
        changed = completed and bool(state.get("operations"))
        return {
            "reply": str(state.get("reply") or ("Готово." if completed else "")),
            "spec": copy.deepcopy(state.get("spec")) if changed else None,
            "changes": list(state.get("changes") or []),
            "usage": dict(state.get("usage") or {}),
            "created": bool(state.get("created")),
            "graph": {
                "status": state.get("status"),
                "thread_id": state.get("thread_id"),
                "generation": state.get("generation"),
                "prompt_version": state.get("prompt_version"),
                "input_revision": state.get("input_revision"),
                "output_revision": state.get("output_revision"),
                "operations": copy.deepcopy(state.get("operations") or []),
                "checks": copy.deepcopy(state.get("check_results") or {}),
                "revisions": copy.deepcopy(state.get("revisions") or []),
                "repair_count": state.get("repair_count", 0),
                "nodes": list(state.get("node_history") or []),
            },
            **(
                {"error": "Команда остановлена", "code": "operation_cancelled"}
                if state.get("status") == "cancelled"
                else {}
            ),
        }
