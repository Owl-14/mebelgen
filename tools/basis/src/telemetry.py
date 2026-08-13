"""Privacy-first OpenTelemetry helpers for the Studio pipeline.

Only compact, explicitly allow-listed metadata is accepted.  Prompts, images,
ParamSpec payloads and credentials never become span attributes.
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import os
import secrets
import threading
import time
from functools import wraps
from pathlib import Path
from typing import Any, Iterator, Mapping
from urllib.parse import urlsplit


_SAFE_KEYS = frozenset({
    "trace_id", "project.hash", "revision.hash", "provider", "model",
    "prompt.version", "operation.types", "latency_ms", "panel.count",
    "hole.count", "check.name", "check.outcome", "error.codes",
    "image.count", "repair.iteration", "repair.applied", "approval.outcome",
    "revision.persisted", "http.route", "http.status_code", "response.changed",
    "gen_ai.system", "gen_ai.request.model", "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens", "gen_ai.usage.total_tokens",
    "langsmith.span.kind", "langsmith.trace.name",
    "rollout.primary", "rollout.shadow", "rollout.canary", "rollout.stopped",
    "shadow.equal", "shadow.spec_equal", "shadow.geometry_equal",
    "shadow.drilling_equal", "checkpoint.bytes", "checkpoint.degraded",
    "gen_ai.usage.cost_usd",
})
_SEQUENCE_KEYS = frozenset({"operation.types", "error.codes"})
_FALLBACK_TRACE_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "akeda_trace_id", default=""
)
_ROLLOUT_ATTRIBUTES: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "akeda_rollout_attributes", default={}
)
_CONFIG_LOCK = threading.Lock()
_FILE_LOCK = threading.Lock()
_PROVIDER: Any = None
_TRACER: Any = None


def hash_payload(value: Any, *, length: int = 20) -> str:
    """Return a stable non-reversible identifier for a project or revision."""

    try:
        raw = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError):
        raw = str(type(value).__name__).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[: max(8, min(int(length), 64))]


def prompt_version(path: str | Path) -> str:
    """Hash a prompt template without exporting its contents."""

    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]
    except OSError:
        return "missing"


def operation_types(before: Mapping[str, Any] | None, after: Mapping[str, Any] | None) -> list[str]:
    """Describe changed top-level ParamSpec fields without exporting values."""

    left, right = dict(before or {}), dict(after or {})
    return sorted(key for key in set(left) | set(right) if left.get(key) != right.get(key))[:32]


def _safe_value(key: str, value: Any) -> Any | None:
    if key not in _SAFE_KEYS:
        return None
    if key in _SEQUENCE_KEYS:
        if not isinstance(value, (list, tuple, set, frozenset)):
            value = [value]
        return [str(item)[:120] for item in list(value)[:50] if item is not None]
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value, 3)
    if value is None:
        return None
    return str(value)[:240]


def safe_attributes(attributes: Mapping[str, Any] | None) -> dict[str, Any]:
    """Apply the deny-by-default telemetry redaction policy."""

    clean: dict[str, Any] = {}
    for key, value in dict(attributes or {}).items():
        safe = _safe_value(str(key), value)
        if safe is not None and safe != []:
            clean[str(key)] = safe
    return clean


class _JsonFileExporter:
    """Small JSONL development exporter implementing the OTel exporter contract."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def export(self, spans: Any) -> Any:
        from opentelemetry.sdk.trace.export import SpanExportResult

        rows: list[str] = []
        for item in spans:
            ctx = item.context
            parent = item.parent
            rows.append(json.dumps({
                "trace_id": f"{ctx.trace_id:032x}",
                "span_id": f"{ctx.span_id:016x}",
                "parent_span_id": f"{parent.span_id:016x}" if parent else None,
                "name": item.name,
                "start_time_unix_nano": item.start_time,
                "end_time_unix_nano": item.end_time,
                "status": item.status.status_code.name,
                "attributes": safe_attributes(item.attributes),
            }, ensure_ascii=False, separators=(",", ":")))
        if rows:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with _FILE_LOCK, self.path.open("a", encoding="utf-8") as stream:
                stream.write("\n".join(rows) + "\n")
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


class _RolloutFilteringExporter:
    """Apply request rollout selection after server-owned identity is known."""

    def __init__(self, exporter: Any, mode: str) -> None:
        self.exporter = exporter
        self.mode = mode

    def export(self, spans: Any) -> Any:
        from opentelemetry.sdk.trace.export import SpanExportResult

        rows = list(spans)
        if self.mode == "canary":
            rows = [row for row in rows if bool((row.attributes or {}).get("rollout.canary"))]
        elif self.mode == "shadow":
            rows = [row for row in rows if bool((row.attributes or {}).get("rollout.shadow"))]
        if not rows:
            return SpanExportResult.SUCCESS
        return self.exporter.export(rows)

    def shutdown(self) -> Any:
        return self.exporter.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return bool(self.exporter.force_flush(timeout_millis=timeout_millis))


def _ratio() -> float:
    raw = os.environ.get("AKEDA_TELEMETRY_SAMPLE_RATE", "0.1")
    try:
        return max(0.0, min(float(raw), 1.0))
    except ValueError:
        return 0.1


def configure(*, force: bool = False) -> Any:
    """Configure exporters lazily from environment variables.

    ``AKEDA_TELEMETRY_BACKEND`` accepts ``none``, ``console``, ``file``,
    ``otlp`` and ``langsmith`` (comma-separated).  Export is disabled by
    default, while valid trace IDs are still created for Studio history.
    """

    global _PROVIDER, _TRACER
    with _CONFIG_LOCK:
        if _TRACER is not None and not force:
            return _TRACER
        if force and _PROVIDER is not None:
            try:
                _PROVIDER.shutdown()
            except Exception:
                pass
            _PROVIDER = _TRACER = None
        try:
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import (
                BatchSpanProcessor,
                ConsoleSpanExporter,
                SimpleSpanProcessor,
            )
            from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
        except ImportError:
            return None

        provider = TracerProvider(
            sampler=ParentBased(TraceIdRatioBased(_ratio())),
            resource=Resource.create({
                "service.name": os.environ.get("OTEL_SERVICE_NAME", "akeda-studio"),
                "service.version": os.environ.get("AKEDA_SERVICE_VERSION", "dev"),
            }),
        )
        exporter_mode = os.environ.get(
            "AKEDA_ROLLOUT_TRACING_EXPORTERS", "on"
        ).strip().casefold()
        exporter_killed = os.environ.get(
            "AKEDA_KILL_SWITCH_TRACING_EXPORTERS", ""
        ).strip().casefold() in {"1", "true", "yes", "on"}
        backends = {
            item.strip().casefold()
            for item in os.environ.get("AKEDA_TELEMETRY_BACKEND", "none").split(",")
            if item.strip()
        }
        if exporter_killed or exporter_mode == "off":
            backends = {"none"}
        if "console" in backends:
            provider.add_span_processor(SimpleSpanProcessor(
                _RolloutFilteringExporter(ConsoleSpanExporter(), exporter_mode)
            ))
        if "file" in backends:
            path = Path(os.environ.get("AKEDA_TELEMETRY_FILE", "out/traces.jsonl"))
            provider.add_span_processor(SimpleSpanProcessor(
                _RolloutFilteringExporter(_JsonFileExporter(path), exporter_mode)
            ))
        if "otlp" in backends:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            provider.add_span_processor(BatchSpanProcessor(
                _RolloutFilteringExporter(OTLPSpanExporter(), exporter_mode)
            ))
        if "langsmith" in backends:
            api_key = os.environ.get("LANGSMITH_API_KEY", "").strip()
            if api_key:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
                endpoint = os.environ.get(
                    "LANGSMITH_OTEL_ENDPOINT",
                    "https://api.smith.langchain.com/otel/v1/traces",
                )
                headers = {"x-api-key": api_key}
                project = os.environ.get("LANGSMITH_PROJECT", "").strip()
                if project:
                    headers["Langsmith-Project"] = project
                provider.add_span_processor(BatchSpanProcessor(
                    _RolloutFilteringExporter(
                        OTLPSpanExporter(endpoint=endpoint, headers=headers), exporter_mode
                    )
                ))
        _PROVIDER = provider
        _TRACER = provider.get_tracer("akeda.studio")
        return _TRACER


class SpanHandle:
    def __init__(self, raw: Any = None) -> None:
        self.raw = raw

    def set_attributes(self, attributes: Mapping[str, Any] | None) -> None:
        if self.raw is None:
            return
        for key, value in safe_attributes(attributes).items():
            self.raw.set_attribute(key, value)


@contextlib.contextmanager
def span(name: str, attributes: Mapping[str, Any] | None = None) -> Iterator[SpanHandle]:
    """Create a privacy-filtered span and always attach latency and trace ID."""

    tracer = configure()
    started = time.perf_counter()
    if tracer is None:
        previous = _FALLBACK_TRACE_ID.get()
        token = None
        if not previous:
            token = _FALLBACK_TRACE_ID.set(secrets.token_hex(16))
        try:
            yield SpanHandle()
        finally:
            if token is not None:
                _FALLBACK_TRACE_ID.reset(token)
        return

    from opentelemetry.trace import Status, StatusCode

    combined = {**_ROLLOUT_ATTRIBUTES.get(), **dict(attributes or {})}
    with tracer.start_as_current_span(
        name, attributes=safe_attributes(combined), record_exception=False
    ) as raw:
        handle = SpanHandle(raw)
        raw.set_attribute("trace_id", f"{raw.get_span_context().trace_id:032x}")
        try:
            yield handle
        except Exception as error:
            raw.set_status(Status(StatusCode.ERROR))
            raw.set_attribute("check.outcome", "error")
            raw.set_attribute("error.codes", [type(error).__name__])
            raise
        finally:
            raw.set_attribute("latency_ms", round((time.perf_counter() - started) * 1000, 3))


def current_trace_id() -> str:
    try:
        from opentelemetry import trace
        ctx = trace.get_current_span().get_span_context()
        if ctx.is_valid:
            return f"{ctx.trace_id:032x}"
    except ImportError:
        pass
    return _FALLBACK_TRACE_ID.get()


def add_current_attributes(attributes: Mapping[str, Any] | None) -> None:
    try:
        from opentelemetry import trace
        raw = trace.get_current_span()
        if raw.get_span_context().is_valid:
            SpanHandle(raw).set_attributes(attributes)
    except ImportError:
        return


def set_rollout_context(*, primary: str, shadow: bool, canary: bool) -> None:
    """Attach compact rollout selection to subsequent spans in this request."""

    _ROLLOUT_ATTRIBUTES.set({
        "rollout.primary": primary,
        "rollout.shadow": bool(shadow),
        "rollout.canary": bool(canary),
    })


def force_flush(timeout_millis: int = 30000) -> bool:
    if _PROVIDER is None:
        return True
    try:
        return bool(_PROVIDER.force_flush(timeout_millis=timeout_millis))
    except Exception:
        return False


def traced_http_request(function: Any) -> Any:
    """Trace selected Studio mutations without parsing or exporting request bodies."""

    @wraps(function)
    def wrapped(handler: Any, *args: Any, **kwargs: Any) -> Any:
        route = urlsplit(handler.path).path
        if route in {"/api/chat", "/api/import-tz"}:
            name = "chat.request"
        elif route == "/api/save":
            name = "revision.persist"
        elif route.startswith("/review/") and route.endswith("/decision"):
            name = "approval"
        else:
            return function(handler, *args, **kwargs)
        with span(name, {"http.route": route, "langsmith.trace.name": name}):
            return function(handler, *args, **kwargs)

    return wrapped


__all__ = [
    "add_current_attributes", "configure", "current_trace_id", "force_flush",
    "hash_payload", "operation_types", "prompt_version", "safe_attributes",
    "set_rollout_context", "span", "traced_http_request",
]
