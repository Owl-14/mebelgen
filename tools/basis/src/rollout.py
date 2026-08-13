"""Safe rollout control plane for the Studio AI pipeline.

The module is deliberately independent from HTTP and provider code.  It makes
component decisions from server-owned tenant/user identities, stores only
hashed actor references and compact counters, and fails closed to the stable
pipeline when its local state cannot be read or written.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


COMPONENTS = (
    "typed_ops",
    "edit_engine",
    "full_gate",
    "split_prompts",
    "tracing_exporters",
    "langgraph",
)
_CANDIDATE_COMPONENTS = COMPONENTS[:4] + ("langgraph",)
_MODES = frozenset({"off", "shadow", "canary", "on"})
_TRUE = frozenset({"1", "true", "yes", "on"})


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    return default if raw is None else raw.strip().casefold() in _TRUE


def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    try:
        return max(minimum, float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    try:
        return max(minimum, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def _percentile(values: Iterable[float], percentile: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def _hash_identity(value: str | None) -> str:
    if not value:
        return "anonymous"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


@dataclass(frozen=True)
class SloBudgets:
    latency_p50_ms: float = 5_000.0
    latency_p95_ms: float = 15_000.0
    tokens_p95: float = 12_000.0
    cost_usd_p95: float = 0.25
    invalid_op_rate: float = 0.05
    false_rejection_rate: float = 0.03
    edit_success_rate: float = 0.90
    checkpoint_bytes: int = 64 * 1024 * 1024
    checkpoint_retention_days: int = 7
    minimum_samples: int = 20
    window_samples: int = 200

    @classmethod
    def from_env(cls) -> "SloBudgets":
        return cls(
            latency_p50_ms=_env_float("AKEDA_SLO_LATENCY_P50_MS", 5_000.0),
            latency_p95_ms=_env_float("AKEDA_SLO_LATENCY_P95_MS", 15_000.0),
            tokens_p95=_env_float("AKEDA_SLO_TOKENS_P95", 12_000.0),
            cost_usd_p95=_env_float("AKEDA_SLO_COST_USD_P95", 0.25),
            invalid_op_rate=min(1.0, _env_float("AKEDA_SLO_INVALID_OP_RATE", 0.05)),
            false_rejection_rate=min(
                1.0, _env_float("AKEDA_SLO_FALSE_REJECTION_RATE", 0.03)
            ),
            edit_success_rate=min(
                1.0, _env_float("AKEDA_SLO_EDIT_SUCCESS_RATE", 0.90)
            ),
            checkpoint_bytes=_env_int(
                "AKEDA_SLO_CHECKPOINT_BYTES", 64 * 1024 * 1024, minimum=1
            ),
            checkpoint_retention_days=_env_int(
                "AKEDA_CHECKPOINT_RETENTION_DAYS", 7, minimum=1
            ),
            minimum_samples=_env_int("AKEDA_SLO_MINIMUM_SAMPLES", 20, minimum=1),
            window_samples=_env_int("AKEDA_SLO_WINDOW_SAMPLES", 200, minimum=1),
        )


@dataclass(frozen=True)
class RolloutConfig:
    modes: dict[str, str]
    killed: frozenset[str]
    canary_tenants: frozenset[str]
    canary_users: frozenset[str]
    canary_percent: float

    @classmethod
    def from_env(cls) -> "RolloutConfig":
        modes: dict[str, str] = {}
        legacy_graph = _env_bool("STUDIO_LANGGRAPH_ORCHESTRATION")
        for component in COMPONENTS:
            default = "on" if component != "langgraph" else ("on" if legacy_graph else "off")
            value = os.environ.get(
                f"AKEDA_ROLLOUT_{component.upper()}", default
            ).strip().casefold()
            modes[component] = value if value in _MODES else "off"
        killed = frozenset(
            component
            for component in COMPONENTS
            if _env_bool(f"AKEDA_KILL_SWITCH_{component.upper()}")
        )
        tenants = frozenset(
            item.strip() for item in os.environ.get("AKEDA_CANARY_TENANTS", "").split(",")
            if item.strip()
        )
        users = frozenset(
            item.strip() for item in os.environ.get("AKEDA_CANARY_USERS", "").split(",")
            if item.strip()
        )
        return cls(
            modes=modes,
            killed=killed,
            canary_tenants=tenants,
            canary_users=users,
            canary_percent=min(
                100.0, _env_float("AKEDA_CANARY_PERCENT", 0.0)
            ),
        )

    def canary_selected(self, tenant_id: str | None, user_id: str | None) -> bool:
        if tenant_id and tenant_id in self.canary_tenants:
            return True
        if user_id and user_id in self.canary_users:
            return True
        if self.canary_percent <= 0:
            return False
        identity = f"{tenant_id or 'local'}:{user_id or 'anonymous'}"
        bucket = int(hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8], 16)
        return bucket % 10_000 < int(self.canary_percent * 100)


@dataclass(frozen=True)
class RolloutPlan:
    component_modes: dict[str, str]
    primary: str
    shadow: bool
    canary: bool
    stopped: bool = False
    reasons: tuple[str, ...] = ()

    def public_dict(self) -> dict[str, Any]:
        return {
            "primary": self.primary,
            "shadow": self.shadow,
            "canary": self.canary,
            "stopped": self.stopped,
            "reasons": list(self.reasons),
            "components": dict(self.component_modes),
        }


@dataclass(frozen=True)
class RolloutMetric:
    latency_ms: float
    total_tokens: int = 0
    cost_usd: float = 0.0
    invalid_operation: bool = False
    false_rejection: bool = False
    edit_attempted: bool = True
    edit_success: bool = False
    checkpoint_bytes: int = 0
    shadow_equal: bool | None = None
    source: str = "studio"


class RolloutStateStore:
    """Bounded atomic JSON state; corruption or I/O errors fail closed."""

    def __init__(self, root: Path, *, max_events: int = 2_000) -> None:
        self.root = Path(root)
        self.path = self.root / "state.json"
        self.dashboard_path = self.root / "dashboard.json"
        self.max_events = max(50, int(max_events))
        self._lock = threading.RLock()
        self.degraded_reason = ""

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {"version": 1, "events": [], "stops": {}, "updated_at": 0}

    def read(self) -> dict[str, Any]:
        with self._lock:
            if self.degraded_reason:
                raise RuntimeError(self.degraded_reason)
            if not self.path.exists():
                return self._empty()
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict) or payload.get("version") != 1:
                    raise ValueError("unsupported rollout state")
                payload.setdefault("events", [])
                payload.setdefault("stops", {})
                return payload
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
                self.degraded_reason = f"rollout_state_read:{type(error).__name__}"
                raise RuntimeError(self.degraded_reason) from error

    def write(self, payload: Mapping[str, Any]) -> None:
        with self._lock:
            temporary: Path | None = None
            try:
                self.root.mkdir(parents=True, exist_ok=True)
                temporary = self.root / f".state.{uuid.uuid4().hex[:10]}.tmp"
                temporary.write_text(
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8",
                )
                temporary.replace(self.path)
                self.degraded_reason = ""
            except OSError as error:
                self.degraded_reason = f"rollout_state_write:{type(error).__name__}"
                raise RuntimeError(self.degraded_reason) from error
            finally:
                if temporary is not None and temporary.exists():
                    try:
                        temporary.unlink()
                    except OSError:
                        pass

    def append(self, event: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            payload = self.read()
            payload["events"] = [
                *list(payload.get("events") or []), dict(event)
            ][-self.max_events :]
            payload["updated_at"] = int(time.time())
            self.write(payload)
            return payload

    def stop(self, components: Iterable[str], reasons: Iterable[str]) -> dict[str, Any]:
        with self._lock:
            payload = self.read()
            now = int(time.time())
            clean_reasons = sorted({str(reason)[:160] for reason in reasons if reason})
            for component in components:
                payload.setdefault("stops", {})[component] = {
                    "at": now, "reasons": clean_reasons
                }
            payload["updated_at"] = now
            self.write(payload)
            return payload

    def clear_stops(self) -> None:
        with self._lock:
            payload = self.read()
            payload["stops"] = {}
            payload["updated_at"] = int(time.time())
            self.write(payload)


class RolloutController:
    def __init__(
        self,
        root: Path,
        *,
        config: RolloutConfig | None = None,
        budgets: SloBudgets | None = None,
        store: RolloutStateStore | None = None,
    ) -> None:
        self.config = config or RolloutConfig.from_env()
        self.budgets = budgets or SloBudgets.from_env()
        self.store = store or RolloutStateStore(root)

    def _state_or_degraded(self) -> tuple[dict[str, Any], str]:
        try:
            return self.store.read(), ""
        except RuntimeError as error:
            return RolloutStateStore._empty(), str(error)

    def plan(self, tenant_id: str | None, user_id: str | None) -> RolloutPlan:
        state, degraded = self._state_or_degraded()
        stopped = set((state.get("stops") or {}).keys())
        selected = self.config.canary_selected(tenant_id, user_id)
        component_modes: dict[str, str] = {}
        reasons: list[str] = []
        for component in COMPONENTS:
            mode = self.config.modes.get(component, "off")
            if component in self.config.killed:
                mode = "off"
                reasons.append(f"kill_switch:{component}")
            if component in stopped:
                mode = "off"
                reasons.append(f"slo_stop:{component}")
            if mode == "canary" and not selected:
                mode = "off"
            component_modes[component] = mode
        if degraded:
            reasons.append(degraded)
            for component in _CANDIDATE_COMPONENTS:
                component_modes[component] = "off"

        candidate_modes = [component_modes[name] for name in _CANDIDATE_COMPONENTS]
        shadow = all(mode in {"shadow", "on", "canary"} for mode in candidate_modes) and any(
            mode == "shadow" for mode in candidate_modes
        )
        graph_primary = all(mode in {"on", "canary"} for mode in candidate_modes)
        return RolloutPlan(
            component_modes=component_modes,
            primary="graph" if graph_primary else "legacy",
            shadow=shadow and not graph_primary,
            canary=selected and any(mode == "canary" for mode in candidate_modes),
            stopped=bool(stopped or degraded or self.config.killed),
            reasons=tuple(sorted(set(reasons))),
        )

    def exporter_enabled(self) -> bool:
        plan = self.plan(None, None)
        return plan.component_modes["tracing_exporters"] in {"on", "canary", "shadow"}

    def record(
        self,
        metric: RolloutMetric,
        *,
        tenant_id: str | None,
        user_id: str | None,
        plan: RolloutPlan,
    ) -> dict[str, Any]:
        event = {
            "at": int(time.time()),
            "tenant_hash": _hash_identity(tenant_id),
            "user_hash": _hash_identity(user_id),
            "primary": plan.primary,
            "shadow": plan.shadow,
            "canary": plan.canary,
            **asdict(metric),
        }
        try:
            state = self.store.append(event)
        except RuntimeError as error:
            return {"stopped": True, "reasons": [str(error)], "samples": 0}
        summary = self.summarize(state.get("events") or [])
        reasons = self._violations(summary)
        if reasons and (plan.canary or plan.shadow or plan.primary == "graph"):
            try:
                self.store.stop(_CANDIDATE_COMPONENTS, reasons)
            except RuntimeError as error:
                reasons.append(str(error))
        dashboard = {
            "version": 1,
            "generated_at": int(time.time()),
            "budgets": asdict(self.budgets),
            "summary": summary,
            "stopped": bool(reasons),
            "reasons": reasons,
            "links": {
                "engine_matrix": "python -m qa.engine_checks",
                "trace_eval": "python main.py trace-eval",
                "provider_bakeoff": "MEB-153 paid live gate (manual only)",
            },
        }
        self._write_dashboard(dashboard)
        return dashboard

    def _write_dashboard(self, payload: Mapping[str, Any]) -> None:
        try:
            self.store.root.mkdir(parents=True, exist_ok=True)
            temporary = self.store.root / f".dashboard.{uuid.uuid4().hex[:10]}.tmp"
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.store.dashboard_path)
        except OSError:
            return

    def summarize(self, events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        rows = list(events)[-self.budgets.window_samples :]
        count = len(rows)
        if not count:
            return {"samples": 0}
        edit_rows = [row for row in rows if bool(row.get("edit_attempted", True))]
        rate = lambda key: sum(bool(row.get(key)) for row in edit_rows) / max(
            1, len(edit_rows)
        )
        return {
            "samples": count,
            "latency_p50_ms": round(_percentile((row.get("latency_ms", 0) for row in rows), 0.50), 3),
            "latency_p95_ms": round(_percentile((row.get("latency_ms", 0) for row in rows), 0.95), 3),
            "tokens_p95": round(_percentile((row.get("total_tokens", 0) for row in rows), 0.95), 3),
            "cost_usd_p95": round(_percentile((row.get("cost_usd", 0) for row in rows), 0.95), 6),
            "invalid_op_rate": round(rate("invalid_operation"), 6),
            "false_rejection_rate": round(rate("false_rejection"), 6),
            "edit_success_rate": round(rate("edit_success"), 6),
            "edit_samples": len(edit_rows),
            "checkpoint_bytes_max": max(int(row.get("checkpoint_bytes", 0)) for row in rows),
            "shadow_match_rate": round(
                sum(row.get("shadow_equal") is True for row in rows)
                / max(1, sum(row.get("shadow_equal") is not None for row in rows)),
                6,
            ),
        }

    def _violations(self, summary: Mapping[str, Any]) -> list[str]:
        if int(summary.get("samples", 0)) < self.budgets.minimum_samples:
            return []
        enough_edits = int(summary.get("edit_samples", 0)) >= self.budgets.minimum_samples
        checks = (
            (summary.get("latency_p50_ms", 0) > self.budgets.latency_p50_ms, "latency_p50"),
            (summary.get("latency_p95_ms", 0) > self.budgets.latency_p95_ms, "latency_p95"),
            (summary.get("tokens_p95", 0) > self.budgets.tokens_p95, "tokens_p95"),
            (summary.get("cost_usd_p95", 0) > self.budgets.cost_usd_p95, "cost_p95"),
            (
                enough_edits
                and summary.get("invalid_op_rate", 0) > self.budgets.invalid_op_rate,
                "invalid_op_rate",
            ),
            (
                enough_edits
                and summary.get("false_rejection_rate", 0)
                > self.budgets.false_rejection_rate,
                "false_rejection_rate",
            ),
            (
                enough_edits
                and summary.get("edit_success_rate", 0) < self.budgets.edit_success_rate,
                "edit_success_rate",
            ),
            (
                summary.get("checkpoint_bytes_max", 0) > self.budgets.checkpoint_bytes,
                "checkpoint_bytes",
            ),
        )
        return [name for failed, name in checks if failed]


def compare_shadow_results(
    primary: Mapping[str, Any], candidate: Mapping[str, Any]
) -> dict[str, Any]:
    """Compare accepted outputs without exposing ParamSpec or request contents."""

    from .studio_graph import ExistingDeterministicEngine, spec_revision

    left = primary.get("spec") if isinstance(primary.get("spec"), dict) else None
    right = candidate.get("spec") if isinstance(candidate.get("spec"), dict) else None
    report: dict[str, Any] = {
        "accepted_equal": (left is None) == (right is None),
        "outcome_equal": str(primary.get("code") or "") == str(candidate.get("code") or ""),
        "spec_equal": bool(left is not None and right is not None and spec_revision(left) == spec_revision(right)),
        "geometry_equal": False,
        "drilling_equal": False,
    }
    if left is None or right is None:
        report["equal"] = report["accepted_equal"] and report["outcome_equal"]
        return report
    engine = ExistingDeterministicEngine()
    try:
        left_project = engine.generate_geometry(left)
        right_project = engine.generate_geometry(right)
        report["geometry_equal"] = spec_revision(left_project) == spec_revision(right_project)
        left_drilling = engine.compute_drilling(left_project)
        right_drilling = engine.compute_drilling(right_project)
        report["drilling_equal"] = spec_revision({"holes": left_drilling}) == spec_revision(
            {"holes": right_drilling}
        )
        report["primary_panel_count"] = len(left_project.get("panels") or [])
        report["candidate_panel_count"] = len(right_project.get("panels") or [])
        report["primary_hole_count"] = len(left_drilling)
        report["candidate_hole_count"] = len(right_drilling)
    except Exception as error:
        report["comparison_error"] = type(error).__name__
    report["equal"] = all(
        report[key]
        for key in (
            "accepted_equal", "outcome_equal", "spec_equal", "geometry_equal",
            "drilling_equal",
        )
    )
    return report


__all__ = [
    "COMPONENTS", "RolloutConfig", "RolloutController", "RolloutMetric",
    "RolloutPlan", "RolloutStateStore", "SloBudgets", "compare_shadow_results",
]
