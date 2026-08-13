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
from dataclasses import asdict, dataclass, replace
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
_EVAL_CASE_SCHEMA = "trace-eval-case-v1"
_EVAL_STATUSES = frozenset({"accepted", "replied", "rejected"})
_SHA256_HEX_LENGTH = 64


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


def _scope_key(mode: str, tenant_hash: str, cohort: str) -> str:
    raw = f"{mode}|{tenant_hash}|{cohort}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _evidence_digest(value: Any) -> str:
    canonical = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _is_sha256(value: Any) -> bool:
    return bool(
        isinstance(value, str)
        and len(value) == _SHA256_HEX_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


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

    def canary_assignment(
        self, tenant_id: str | None, user_id: str | None
    ) -> tuple[bool, str]:
        if tenant_id and tenant_id in self.canary_tenants:
            return True, "tenant_allowlist"
        if user_id and user_id in self.canary_users:
            return True, "user_allowlist"
        if self.canary_percent <= 0:
            return False, "not_selected"
        identity = f"{tenant_id or 'local'}:{user_id or 'anonymous'}"
        bucket = int(hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8], 16)
        bucket_number = bucket % 10_000
        threshold = int(self.canary_percent * 100)
        selected = bucket_number < threshold
        cohort = f"percent_{threshold:04d}"
        return selected, cohort if selected else f"excluded_{cohort}"

    def canary_selected(self, tenant_id: str | None, user_id: str | None) -> bool:
        return self.canary_assignment(tenant_id, user_id)[0]


@dataclass(frozen=True)
class RolloutPlan:
    component_modes: dict[str, str]
    requested_mode: str
    scope_key: str
    tenant_hash: str
    cohort: str
    primary: str
    shadow: bool
    canary: bool
    stopped: bool = False
    reasons: tuple[str, ...] = ()

    def fallback(self, reason: str) -> "RolloutPlan":
        modes = dict(self.component_modes)
        for component in _CANDIDATE_COMPONENTS:
            modes[component] = "off"
        return replace(
            self,
            component_modes=modes,
            primary="legacy",
            shadow=False,
            canary=False,
            stopped=True,
            reasons=tuple(sorted({*self.reasons, str(reason)})),
        )

    def public_dict(self) -> dict[str, Any]:
        return {
            "primary": self.primary,
            "requested_mode": self.requested_mode,
            "scope": self.scope_key,
            "cohort": self.cohort,
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
    cost_usd: float | None = None
    invalid_operation: bool = False
    false_rejection: bool | None = None
    false_rejection_label_source: str | None = None
    live_divergence: bool | None = None
    edit_attempted: bool = True
    edit_success: bool = False
    checkpoint_bytes: int = 0
    paramspec_equal: bool | None = None
    geometry_equal: bool | None = None
    drilling_equal: bool | None = None
    result_code: str = ""
    source: str = "studio"
    metric_kind: str = "live"


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
        return {"version": 2, "events": [], "stops": {}, "updated_at": 0}

    def read(self) -> dict[str, Any]:
        with self._lock:
            if self.degraded_reason:
                raise RuntimeError(self.degraded_reason)
            if not self.path.exists():
                return self._empty()
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict) or payload.get("version") not in {1, 2}:
                    raise ValueError("unsupported rollout state")
                if payload.get("version") == 1:
                    # PR #118 was never deployed; keep old metrics as history but
                    # do not let unscoped v1 stops poison a tenant/cohort.
                    payload = {
                        "version": 2,
                        "events": list(payload.get("events") or []),
                        "stops": {},
                        "updated_at": int(payload.get("updated_at") or 0),
                    }
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

    def stop(
        self,
        *,
        scope_key: str,
        mode: str,
        tenant_hash: str,
        cohort: str,
        components: Iterable[str],
        reasons: Iterable[str],
    ) -> dict[str, Any]:
        with self._lock:
            payload = self.read()
            now = int(time.time())
            clean_reasons = sorted({str(reason)[:160] for reason in reasons if reason})
            scope = payload.setdefault("stops", {}).setdefault(scope_key, {
                "mode": mode,
                "tenant_hash": tenant_hash,
                "cohort": cohort,
                "components": {},
            })
            for component in components:
                scope.setdefault("components", {})[component] = {
                    "at": now, "reasons": clean_reasons
                }
            payload["updated_at"] = now
            self.write(payload)
            return payload

    def clear_stops(self, scope_key: str | None = None) -> None:
        with self._lock:
            payload = self.read()
            if scope_key is None:
                payload["stops"] = {}
            else:
                payload.setdefault("stops", {}).pop(scope_key, None)
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
        self._degraded_reason = ""

    def _state_or_degraded(self) -> tuple[dict[str, Any], str]:
        if self._degraded_reason:
            return RolloutStateStore._empty(), self._degraded_reason
        try:
            return self.store.read(), ""
        except RuntimeError as error:
            return RolloutStateStore._empty(), str(error)

    @staticmethod
    def _mode(component_modes: Mapping[str, str]) -> str:
        candidate_modes = [component_modes[name] for name in _CANDIDATE_COMPONENTS]
        if all(mode in {"shadow", "on", "canary"} for mode in candidate_modes):
            if any(mode == "shadow" for mode in candidate_modes):
                return "shadow"
            if any(mode == "canary" for mode in candidate_modes):
                return "canary"
            return "on"
        return "legacy"

    def plan(self, tenant_id: str | None, user_id: str | None) -> RolloutPlan:
        state, degraded = self._state_or_degraded()
        selected, cohort = self.config.canary_assignment(tenant_id, user_id)
        tenant_hash = _hash_identity(tenant_id or "local")
        component_modes: dict[str, str] = {}
        for component in COMPONENTS:
            mode = self.config.modes.get(component, "off")
            if mode == "canary" and not selected:
                mode = "off"
            component_modes[component] = mode

        requested_mode = self._mode(component_modes)
        if requested_mode != "canary" and cohort == "not_selected":
            cohort = "all" if requested_mode in {"on", "shadow"} else "legacy"
        scope_key = _scope_key(requested_mode, tenant_hash, cohort)
        scope_stop = (state.get("stops") or {}).get(scope_key) or {}
        stopped_components = set((scope_stop.get("components") or {}).keys())
        reasons: list[str] = []
        for component in COMPONENTS:
            if component in self.config.killed:
                component_modes[component] = "off"
                reasons.append(f"kill_switch:{component}")
            if component in stopped_components:
                component_modes[component] = "off"
                reasons.append(f"slo_stop:{component}")
        if degraded:
            reasons.append(degraded)
            for component in _CANDIDATE_COMPONENTS:
                component_modes[component] = "off"

        effective_mode = self._mode(component_modes)
        return RolloutPlan(
            component_modes=component_modes,
            requested_mode=requested_mode,
            scope_key=scope_key,
            tenant_hash=tenant_hash,
            cohort=cohort,
            primary="graph" if effective_mode in {"on", "canary"} else "legacy",
            shadow=effective_mode == "shadow",
            canary=selected and requested_mode == "canary",
            stopped=bool(stopped_components or degraded or self.config.killed),
            reasons=tuple(sorted(set(reasons))),
        )

    def latch(self, plan: RolloutPlan, reason: str) -> RolloutPlan:
        try:
            self.store.stop(
                scope_key=plan.scope_key,
                mode=plan.requested_mode,
                tenant_hash=plan.tenant_hash,
                cohort=plan.cohort,
                components=_CANDIDATE_COMPONENTS,
                reasons=[reason],
            )
        except RuntimeError as error:
            self._degraded_reason = str(error)
            return plan.fallback(str(error))
        return plan.fallback(reason)

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
        if metric.false_rejection is not None and not (
            metric.metric_kind == "eval"
            and metric.source == "meb151_eval"
            and metric.false_rejection_label_source == "MEB-151"
        ):
            raise ValueError(
                "false_rejection requires independent MEB-151 eval-labelled evidence"
            )
        event = {
            "at": int(time.time()),
            "scope_key": plan.scope_key,
            "mode": plan.requested_mode,
            "tenant_hash": plan.tenant_hash,
            "cohort": plan.cohort,
            "user_hash": _hash_identity(user_id),
            "primary": plan.primary,
            "shadow": plan.shadow,
            "canary": plan.canary,
            **asdict(metric),
        }
        try:
            state = self.store.append(event)
        except RuntimeError as error:
            self._degraded_reason = str(error)
            return {"stopped": True, "reasons": [str(error)], "samples": 0}
        scoped_events = [
            row for row in (state.get("events") or [])
            if row.get("scope_key") == plan.scope_key
            and row.get("mode") == plan.requested_mode
            and row.get("tenant_hash") == plan.tenant_hash
            and row.get("cohort") == plan.cohort
        ]
        summary = self.summarize(scoped_events)
        reasons = self._violations(summary)
        if reasons and (plan.canary or plan.shadow or plan.primary == "graph"):
            try:
                state = self.store.stop(
                    scope_key=plan.scope_key,
                    mode=plan.requested_mode,
                    tenant_hash=plan.tenant_hash,
                    cohort=plan.cohort,
                    components=_CANDIDATE_COMPONENTS,
                    reasons=reasons,
                )
            except RuntimeError as error:
                self._degraded_reason = str(error)
                reasons.append(str(error))
        persistent = ((state.get("stops") or {}).get(plan.scope_key) or {}).get(
            "components"
        ) or {}
        persistent_reasons = sorted({
            str(reason)
            for item in persistent.values()
            for reason in (item.get("reasons") or [])
        })
        dashboard = {
            "version": 2,
            "generated_at": int(time.time()),
            "scope": {
                "key": plan.scope_key,
                "mode": plan.requested_mode,
                "tenant_hash": plan.tenant_hash,
                "cohort": plan.cohort,
            },
            "budgets": asdict(self.budgets),
            "summary": summary,
            "stopped": bool(persistent or self._degraded_reason),
            "reasons": sorted(set(persistent_reasons + reasons)),
            "links": {
                "engine_matrix": "python -m qa.engine_checks",
                "trace_eval": "python main.py trace-eval",
                "provider_bakeoff": "MEB-153 paid live gate (manual only)",
            },
        }
        self._write_dashboard(dashboard)
        return dashboard

    def record_eval_case(
        self,
        case: Mapping[str, Any],
        *,
        tenant_id: str | None,
        user_id: str | None,
        plan: RolloutPlan,
    ) -> dict[str, Any]:
        """Ingest a verified MEB-151 case or count it as inconclusive."""

        def inconclusive(reason: str) -> dict[str, Any]:
            return self.record(
                RolloutMetric(
                    latency_ms=0,
                    edit_attempted=False,
                    source="meb151_eval",
                    metric_kind="eval_inconclusive",
                    result_code=reason[:80],
                ),
                tenant_id=tenant_id,
                user_id=user_id,
                plan=plan,
            )

        if case.get("schema_version") != _EVAL_CASE_SCHEMA:
            return inconclusive("eval_schema_untrusted")
        if case.get("ok") is not True or case.get("mismatches") != []:
            return inconclusive("eval_verdict_not_ok")

        label = case.get("evaluation_label")
        if not isinstance(label, Mapping) or label.get("source") != "MEB-151":
            return inconclusive("eval_label_untrusted")
        decision = case.get("decision")
        output = case.get("output")
        decision_digest = case.get("decision_digest")
        node_outputs_digest = case.get("node_outputs_digest")
        output_digest = case.get("output_digest")
        verdict_digest = case.get("verdict_digest")
        if not isinstance(decision, Mapping) or not isinstance(output, Mapping):
            return inconclusive("eval_evidence_missing")
        if not all(_is_sha256(value) for value in (
            decision_digest, node_outputs_digest, output_digest, verdict_digest
        )):
            return inconclusive("eval_digest_invalid")
        try:
            valid_verdict = (
                decision_digest == _evidence_digest(decision)
                and output_digest == _evidence_digest(output)
                and verdict_digest == _evidence_digest({
                    "schema_version": _EVAL_CASE_SCHEMA,
                    "decision": decision_digest,
                    "node_outputs": node_outputs_digest,
                    "output": output_digest,
                    "evaluation_label": label,
                    "ok": True,
                })
            )
        except (TypeError, ValueError, OverflowError):
            valid_verdict = False
        if not valid_verdict:
            return inconclusive("eval_verdict_invalid")
        expected_status = label.get("expected_status")
        candidate_status = label.get("candidate_status")
        if not isinstance(expected_status, str) or expected_status not in _EVAL_STATUSES:
            return inconclusive("eval_expected_status_invalid")
        if not isinstance(candidate_status, str) or candidate_status not in _EVAL_STATUSES:
            return inconclusive("eval_candidate_status_invalid")
        if output.get("status") != candidate_status:
            return inconclusive("eval_candidate_status_mismatch")
        value = expected_status in {"accepted", "replied"} and candidate_status == "rejected"
        if label.get("false_rejection") is not value:
            return inconclusive("eval_false_rejection_invalid")
        return self.record(
            RolloutMetric(
                latency_ms=0,
                false_rejection=value,
                false_rejection_label_source="MEB-151",
                edit_attempted=False,
                source="meb151_eval",
                metric_kind="eval",
                result_code=candidate_status,
            ),
            tenant_id=tenant_id,
            user_id=user_id,
            plan=plan,
        )

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
        rows = list(events)
        if not rows:
            return {"samples": 0}
        live_rows = [
            row for row in rows if row.get("metric_kind", "live") == "live"
        ][-self.budgets.window_samples :]
        eval_rows = [
            row for row in rows
            if row.get("metric_kind") == "eval"
            and row.get("source") == "meb151_eval"
            and row.get("false_rejection_label_source") == "MEB-151"
            and isinstance(row.get("false_rejection"), bool)
        ][-self.budgets.window_samples :]
        inconclusive_rows = [
            row for row in rows
            if row.get("metric_kind") == "eval_inconclusive"
            and row.get("source") == "meb151_eval"
        ][-self.budgets.window_samples :]
        count = len(live_rows)
        edit_rows = [row for row in live_rows if bool(row.get("edit_attempted", True))]
        rate = lambda key: sum(bool(row.get(key)) for row in edit_rows) / max(
            1, len(edit_rows)
        )
        def sampled_rate(sample_rows: list[Mapping[str, Any]], key: str) -> float | None:
            return (
                round(sum(row.get(key) is True for row in sample_rows) / len(sample_rows), 6)
                if sample_rows else None
            )

        cost_values = [
            float(row["cost_usd"])
            for row in live_rows
            if isinstance(row.get("cost_usd"), (int, float))
            and not isinstance(row.get("cost_usd"), bool)
            and math.isfinite(float(row["cost_usd"]))
            and float(row["cost_usd"]) >= 0
        ]
        divergence_rows = [row for row in live_rows if isinstance(row.get("live_divergence"), bool)]
        match_rows = {
            key: [row for row in live_rows if isinstance(row.get(key), bool)]
            for key in ("paramspec_equal", "geometry_equal", "drilling_equal")
        }
        return {
            "samples": count,
            "eval_samples": len(eval_rows),
            "eval_inconclusive_samples": len(inconclusive_rows),
            "latency_p50_ms": round(_percentile((row.get("latency_ms", 0) for row in live_rows), 0.50), 3),
            "latency_p95_ms": round(_percentile((row.get("latency_ms", 0) for row in live_rows), 0.95), 3),
            "tokens_p95": round(_percentile((row.get("total_tokens", 0) for row in live_rows), 0.95), 3),
            "cost_samples": len(cost_values),
            "cost_sample_status": "ready" if len(cost_values) >= self.budgets.minimum_samples else "insufficient_samples",
            "cost_usd_p95": round(_percentile(cost_values, 0.95), 6) if cost_values else None,
            "invalid_op_rate": round(rate("invalid_operation"), 6),
            "false_rejection_samples": len(eval_rows),
            "false_rejection_inconclusive_samples": len(inconclusive_rows),
            "false_rejection_sample_status": "ready" if len(eval_rows) >= self.budgets.minimum_samples else "insufficient_samples",
            "false_rejection_rate": sampled_rate(eval_rows, "false_rejection"),
            "live_divergence_samples": len(divergence_rows),
            "live_divergence_rate": sampled_rate(divergence_rows, "live_divergence"),
            "edit_success_rate": round(rate("edit_success"), 6),
            "edit_samples": len(edit_rows),
            "checkpoint_bytes_max": max((int(row.get("checkpoint_bytes", 0)) for row in live_rows), default=0),
            "paramspec_samples": len(match_rows["paramspec_equal"]),
            "paramspec_match_rate": sampled_rate(match_rows["paramspec_equal"], "paramspec_equal"),
            "geometry_samples": len(match_rows["geometry_equal"]),
            "geometry_match_rate": sampled_rate(match_rows["geometry_equal"], "geometry_equal"),
            "drilling_samples": len(match_rows["drilling_equal"]),
            "drilling_match_rate": sampled_rate(match_rows["drilling_equal"], "drilling_equal"),
        }

    def _violations(self, summary: Mapping[str, Any]) -> list[str]:
        enough_live = int(summary.get("samples", 0)) >= self.budgets.minimum_samples
        enough_edits = int(summary.get("edit_samples", 0)) >= self.budgets.minimum_samples
        enough_costs = int(summary.get("cost_samples", 0)) >= self.budgets.minimum_samples
        enough_false_rejections = (
            int(summary.get("false_rejection_samples", 0)) >= self.budgets.minimum_samples
        )
        checks = (
            (enough_live and summary.get("latency_p50_ms", 0) > self.budgets.latency_p50_ms, "latency_p50"),
            (enough_live and summary.get("latency_p95_ms", 0) > self.budgets.latency_p95_ms, "latency_p95"),
            (enough_live and summary.get("tokens_p95", 0) > self.budgets.tokens_p95, "tokens_p95"),
            (enough_costs and summary.get("cost_usd_p95") is not None and summary["cost_usd_p95"] > self.budgets.cost_usd_p95, "cost_p95"),
            (
                enough_edits
                and summary.get("invalid_op_rate", 0) > self.budgets.invalid_op_rate,
                "invalid_op_rate",
            ),
            (
                enough_false_rejections
                and summary.get("false_rejection_rate") is not None
                and summary["false_rejection_rate"]
                > self.budgets.false_rejection_rate,
                "false_rejection_rate",
            ),
            (
                enough_edits
                and summary.get("edit_success_rate", 0) < self.budgets.edit_success_rate,
                "edit_success_rate",
            ),
            (
                enough_live and summary.get("checkpoint_bytes_max", 0) > self.budgets.checkpoint_bytes,
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
        "paramspec_equal": bool(
            left is not None and right is not None
            and spec_revision(left) == spec_revision(right)
        ),
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
            "accepted_equal", "outcome_equal", "paramspec_equal", "geometry_equal",
            "drilling_equal",
        )
    )
    return report


__all__ = [
    "COMPONENTS", "RolloutConfig", "RolloutController", "RolloutMetric",
    "RolloutPlan", "RolloutStateStore", "SloBudgets", "compare_shadow_results",
]
