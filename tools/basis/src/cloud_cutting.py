"""Fail-closed client for the pinned BAZIS Cloud Cutting Public API."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

import requests

from .cutting_ledger import CuttingLedgerError, LedgerRun, MutationLedger
from .material_link_contract import serialize_link_payload

PINNED_ORIGIN = "https://cloud.bazissoft.ru"
PREFIX = "/api-cutting-public"
SUPPORTED_MODEL_EXTENSIONS = {
    ".b3d", ".fr3d", ".shn", ".obl", ".oblx", ".zbprj", ".xml",
    ".k3bz", ".cfrn",
}
_ORDER_FIELDS = {"managerId", "clientId", "note", "factoryOrderId", "factoryId", "uid1C"}
_OPERATOR_TRUST_TOKEN = object()


class CuttingError(RuntimeError):
    """Stable, privacy-safe integration error."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        trace_id: str,
        status: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.trace_id = trace_id
        self.status = status
        self.retryable = retryable

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "trace_id": self.trace_id,
            "status": self.status,
            "retryable": self.retryable,
        }


@dataclass(frozen=True)
class CuttingTimeouts:
    connect: float = 10.0
    read: float = 60.0
    mutation_read: float = 300.0

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise ValueError(f"{name} timeout must be positive")


TraceSink = Callable[[Mapping[str, Any]], None]


def _positive_id(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _origin(value: str) -> str:
    parsed = urlsplit(value.rstrip("/"))
    if parsed.scheme.lower() != "https":
        raise ValueError("Cutting endpoint must use HTTPS")
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Cutting endpoint must be a plain HTTPS origin")
    if parsed.path not in ("", "/"):
        raise ValueError("Cutting endpoint must not include a path")
    if parsed.port is not None:
        raise ValueError("Cutting endpoint must not specify a port")
    return f"https://{parsed.hostname.lower()}"


def _validated_https_url(value: Any) -> str:
    if not isinstance(value, str) or not value or value != value.strip() \
            or any(ord(char) < 32 for char in value):
        raise ValueError("production files URL must be a clean HTTPS URL")
    parsed = urlsplit(value)
    if parsed.scheme.lower() != "https" or not parsed.hostname \
            or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("production files URL must be an absolute credential-free HTTPS URL")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("production files URL has an invalid port") from exc
    if port not in (None, 443):
        raise ValueError("production files URL must use the default HTTPS port")
    return value


class CuttingClient:
    """Cutting client with pinned transport, deadline and durable mutations.

    Production credentials are sent only to ``PINNED_ORIGIN`` through an
    internally created ``requests.Session``. Injected sessions and explicitly
    allowlisted test origins can exercise the contract with ``test_api_key``
    but can never produce live evidence.
    """

    _IMMUTABLE_AFTER_CONSTRUCTION = {
        "transport_origin", "transport_kind", "base", "session", "api_key",
        "ledger", "run_id", "_ledger_run", "_operator_trusted",
        "_construction_complete", "_session_injected",
        "_approval_revalidator",
    }

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_construction_complete", False) \
                and name in self._IMMUTABLE_AFTER_CONSTRUCTION:
            raise AttributeError(f"{name} is immutable after CuttingClient construction")
        object.__setattr__(self, name, value)

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        *,
        test_api_key: str | None = None,
        test_endpoint_allowlist: Sequence[str] = (),
        allow_live: bool = False,
        allow_mutations: bool = False,
        ledger: MutationLedger | None = None,
        ledger_run: LedgerRun | None = None,
        overall_timeout: float | None = None,
        max_upload_bytes: int = 100 * 1024 * 1024,
        timeouts: CuttingTimeouts | None = None,
        session: Any | None = None,
        trace_sink: TraceSink | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
        _operator_trust: object | None = None,
        _approval_revalidator: Callable[[], None] | None = None,
    ) -> None:
        requested_origin = _origin(base_url or PINNED_ORIGIN)
        allowlisted = {_origin(item) for item in test_endpoint_allowlist}
        injected = session is not None
        self.transport_origin = requested_origin
        self._session_injected = injected
        if requested_origin == PINNED_ORIGIN:
            if injected:
                raise ValueError("injected sessions cannot target the production origin")
            if test_api_key is not None:
                raise ValueError("test_api_key cannot target the production origin")
            self.api_key = api_key or os.environ.get("BAZIS_API_KEY")
            self.transport_kind = "pinned_https" if not injected else "injected_test"
        else:
            if requested_origin not in allowlisted:
                raise ValueError("test Cutting endpoint is not explicitly allowlisted")
            if api_key is not None:
                raise ValueError("production api_key cannot be used with a test endpoint")
            self.api_key = test_api_key
            self.transport_kind = "allowlisted_test"

        self.base = requested_origin
        self.allow_live = allow_live
        self.allow_mutations = allow_mutations
        self.ledger = ledger
        self._operator_trusted = _operator_trust is _OPERATOR_TRUST_TOKEN
        self._approval_revalidator = _approval_revalidator
        self.max_upload_bytes = max_upload_bytes
        self.timeouts = timeouts or CuttingTimeouts()
        self.session = session or requests.Session()
        self.trace_sink = trace_sink
        self._trace_id = uuid.uuid4().hex
        self.clock = clock
        self.wall_clock = wall_clock
        self.sleeper = sleeper
        self.started_at = self._timestamp(self.wall_clock())
        self._deadline = None
        if overall_timeout is not None:
            if isinstance(overall_timeout, bool) or not isinstance(overall_timeout, (int, float)) \
                    or overall_timeout <= 0:
                raise ValueError("overall_timeout must be positive")
            self._deadline = self.clock() + float(overall_timeout)
        if max_upload_bytes <= 0:
            raise ValueError("max_upload_bytes must be positive")
        self._ledger_run: LedgerRun | None = None
        if ledger is not None or ledger_run is not None:
            if ledger is None or ledger_run is None:
                raise ValueError("ledger and ledger_run must be supplied together")
            persisted = ledger.get_run(ledger_run.run_id)
            if persisted != ledger_run:
                raise ValueError("ledger_run does not match durable ledger approval")
            self._ledger_run = persisted
            if self._ledger_run.transport_origin != self.transport_origin:
                raise ValueError("ledger run transport origin does not match client transport")
        self.run_id = self._ledger_run.run_id if self._ledger_run else None
        self._construction_complete = True

    @staticmethod
    def _timestamp(epoch: float) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))

    @property
    def trace_id(self) -> str:
        return self._trace_id

    @property
    def live_evidence_allowed(self) -> bool:
        return (
            self.transport_kind == "pinned_https"
            and self._operator_trusted
            and self._approval_revalidator is not None
            and self._ledger_run is not None
            and self._ledger_run.mode == "live"
        )

    @property
    def fixture_sha256(self) -> str | None:
        return self._ledger_run.fixture_sha256 if self._ledger_run else None

    @property
    def model_sha256(self) -> str | None:
        return self._ledger_run.model_sha256 if self._ledger_run else None

    @property
    def approved_mode(self) -> str | None:
        return self._ledger_run.mode if self._ledger_run else None

    @property
    def approval_digest(self) -> str | None:
        return self._ledger_run.approval_digest if self._ledger_run else None

    def revalidate_approval(self) -> None:
        if self.approved_mode == "live":
            if not self.live_evidence_allowed or self._approval_revalidator is None:
                raise ValueError("live approval is outside the canonical operator trust boundary")
            self._approval_revalidator()

    def _error(
        self,
        code: str,
        message: str,
        *,
        status: int | None = None,
        retryable: bool = False,
    ) -> CuttingError:
        return CuttingError(
            code, message, trace_id=self.trace_id, status=status, retryable=retryable,
        )

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise self._error(
                "cutting.auth.missing", "an API key is required for authorized Cutting access",
            )
        return {"apiKey": self.api_key}

    def _url(self, suffix: str) -> str:
        return f"{self.base}{PREFIX}{suffix}"

    def _emit_trace(self, **event: Any) -> None:
        if self.trace_sink is None:
            return
        base = {
            "trace_id": self.trace_id,
            "run_id": self.run_id,
            "timestamp": self._timestamp(self.wall_clock()),
            "transport": self.transport_kind,
            "fixture_sha256": self.fixture_sha256,
            "model_sha256": self.model_sha256,
            "approval_digest": self.approval_digest,
        }
        self.trace_sink(copy.deepcopy({**base, **event}))

    def _live_guard(self) -> None:
        if not self.allow_live:
            raise self._error(
                "cutting.live_guard.required", "Cutting access requires explicit authorization",
            )
        if self.transport_kind == "pinned_https" and not self._operator_trusted:
            raise self._error(
                "cutting.transport.attestation",
                "pinned live transport is available only inside the operator trust boundary",
            )

    def remaining_seconds(self) -> float:
        if self._deadline is None:
            raise self._error(
                "cutting.deadline.required", "authorized Cutting access requires an overall deadline",
            )
        remaining = self._deadline - self.clock()
        if remaining <= 0:
            raise self._error(
                "cutting.deadline.exceeded", "the overall Cutting deadline has expired",
            )
        return remaining

    def _request_timeout(self, mutation: bool) -> tuple[float, float]:
        remaining = self.remaining_seconds()
        read_limit = self.timeouts.mutation_read if mutation else self.timeouts.read
        return min(self.timeouts.connect, remaining), min(read_limit, remaining)

    def _prepare_mutation(
        self,
        *,
        operation: str,
        route: str,
        idempotency_key: str,
        fingerprint: str,
    ) -> None:
        if not self.allow_mutations:
            raise self._error(
                "cutting.cost_guard.required", "Cutting mutations require explicit authorization",
            )
        if self.ledger is None or self._ledger_run is None or not self.run_id:
            raise self._error(
                "cutting.ledger.required", "a registered durable ledger run is required before mutation",
            )
        expected_mode = "live" if self.transport_kind == "pinned_https" else "offline_contract"
        if self._ledger_run.mode != expected_mode:
            raise self._error(
                "cutting.transport.attestation", "ledger mode does not match the confirmed transport",
            )
        try:
            self.ledger.prepare(
                run_id=self.run_id,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
                route=route,
                trace_id=self.trace_id,
                now_epoch=self.wall_clock(),
            )
            self.ledger.mark_ambiguous(idempotency_key, now_epoch=self.wall_clock())
        except CuttingLedgerError as exc:
            raise self._error(exc.code, str(exc)) from exc

    def _mark_success(self, key: str, result: Any) -> None:
        assert self.ledger is not None
        try:
            self.ledger.mark_success(
                key, sha256_json(result), now_epoch=self.wall_clock(),
            )
        except CuttingLedgerError as exc:
            raise self._error(exc.code, str(exc)) from exc

    def _request(
        self,
        method: str,
        suffix: str,
        *,
        route: str,
        operation: str,
        params: Mapping[str, Any] | None = None,
        json_body: Any = None,
        files: Any = None,
        idempotency_key: str | None = None,
        fingerprint_meta: Any = None,
        response_validator: Callable[[Any], Any] | None = None,
    ) -> Any:
        self._live_guard()
        headers = self._headers()
        mutation = method.upper() != "GET"
        timeout = self._request_timeout(mutation)
        fingerprint = ""
        if mutation:
            fingerprint = sha256_json({
                "operation": operation,
                "route": route,
                "params": params,
                "json": json_body,
                "meta": fingerprint_meta,
            })
            self._prepare_mutation(
                operation=operation,
                route=route,
                idempotency_key=idempotency_key or "",
                fingerprint=fingerprint,
            )

        started = self.clock()
        try:
            response = self.session.request(
                method.upper(), self._url(suffix), headers=headers,
                params=dict(params) if params else None, json=json_body, files=files,
                timeout=timeout, allow_redirects=False, verify=True,
            )
        except requests.Timeout as exc:
            code = "cutting.transport.ambiguous" if mutation else "cutting.transport.timeout"
            self._emit_trace(
                step=operation, method=method.upper(), route=route, outcome="error",
                error_code=code, duration_ms=round((self.clock() - started) * 1000),
            )
            raise self._error(
                code,
                "mutation outcome is unknown; reconcile remote state before any new attempt"
                if mutation else "Cutting request timed out",
                retryable=not mutation,
            ) from exc
        except requests.RequestException as exc:
            code = "cutting.transport.ambiguous" if mutation else "cutting.transport.unavailable"
            self._emit_trace(
                step=operation, method=method.upper(), route=route, outcome="error",
                error_code=code, duration_ms=round((self.clock() - started) * 1000),
            )
            raise self._error(
                code,
                "mutation outcome is unknown; reconcile remote state before any new attempt"
                if mutation else "Cutting service is unavailable",
                retryable=not mutation,
            ) from exc

        if self.clock() > self._deadline:  # type: ignore[operator]
            raise self._error(
                "cutting.transport.ambiguous" if mutation else "cutting.deadline.exceeded",
                "request returned after the overall deadline; mutation requires reconciliation"
                if mutation else "request exceeded the overall deadline",
            )
        status = int(response.status_code)
        if not 200 <= status < 300:
            code, retryable = self._classify_http(status)
            self._emit_trace(
                step=operation, method=method.upper(), route=route, outcome="error",
                error_code=code, http_status=status,
                duration_ms=round((self.clock() - started) * 1000),
            )
            raise self._error(
                code, f"Cutting API rejected {operation} (HTTP {status})",
                status=status, retryable=retryable and not mutation,
            )

        try:
            result = self._decode_response(response, operation)
            if response_validator is not None:
                result = response_validator(result)
        except CuttingError as exc:
            self._emit_trace(
                step=operation, method=method.upper(), route=route, outcome="error",
                error_code=exc.code, http_status=status,
                duration_ms=round((self.clock() - started) * 1000),
            )
            raise
        except (TypeError, ValueError, KeyError) as exc:
            error = self._error(
                "cutting.response.contract", f"invalid {operation} response", status=status,
            )
            self._emit_trace(
                step=operation, method=method.upper(), route=route, outcome="error",
                error_code=error.code, http_status=status,
                duration_ms=round((self.clock() - started) * 1000),
            )
            raise error from exc
        if mutation:
            self._mark_success(idempotency_key or "", result)
        self._emit_trace(
            step=operation, method=method.upper(), route=route, outcome="success",
            http_status=status, duration_ms=round((self.clock() - started) * 1000),
        )
        return result

    @staticmethod
    def _classify_http(status: int) -> tuple[str, bool]:
        if status in (401, 403):
            return "cutting.http.auth", False
        if status in (400, 422):
            return "cutting.http.validation", False
        if status == 404:
            return "cutting.http.not_found", False
        if status == 409:
            return "cutting.http.conflict", False
        if status == 429:
            return "cutting.http.rate_limited", True
        if 300 <= status < 400:
            return "cutting.http.redirect_blocked", False
        if status >= 500:
            return "cutting.http.server", True
        return "cutting.http.error", False

    def _decode_response(self, response: Any, operation: str) -> Any:
        content = getattr(response, "content", b"")
        if not content:
            return None
        content_type = str(response.headers.get("content-type", "")).lower()
        if "json" in content_type:
            try:
                return response.json()
            except ValueError as exc:
                raise self._error(
                    "cutting.response.invalid_json", f"malformed JSON for {operation}",
                    status=int(response.status_code),
                ) from exc
        return response.text

    @staticmethod
    def _validate_order_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise TypeError("order payload must be an object")
        unknown = set(payload) - _ORDER_FIELDS
        if unknown:
            raise ValueError(f"unsupported order fields: {', '.join(sorted(unknown))}")
        for field in ("managerId", "clientId", "factoryId"):
            value = payload.get(field)
            if value is not None:
                _positive_id(value, field)
        for field in ("note", "factoryOrderId", "uid1C"):
            value = payload.get(field)
            if value is not None and not isinstance(value, str):
                raise TypeError(f"{field} must be a string or null")
        return dict(payload)

    # Orders
    def list_orders(self, page_index: int = 0, page_size: int = 20, **filters: Any) -> Any:
        if page_index < 0 or not 1 <= page_size <= 1000:
            raise ValueError("page_index must be >= 0 and page_size must be 1..1000")
        return self._request(
            "GET", "/orders", route="/orders", operation="list_orders",
            params={"pageIndex": page_index, "pageSize": page_size, **filters},
        )

    def create_order(self, payload: Mapping[str, Any], *, idempotency_key: str) -> Any:
        return self._request(
            "POST", "/orders", route="/orders", operation="create_order",
            json_body=self._validate_order_payload(payload), idempotency_key=idempotency_key,
        )

    def get_order(self, order_id: int) -> Any:
        order_id = _positive_id(order_id, "order_id")
        return self._request("GET", f"/orders/{order_id}", route="/orders/{id}", operation="get_order")

    def order_details(self, order_id: int) -> Any:
        order_id = _positive_id(order_id, "order_id")
        return self._request(
            "GET", f"/orders/{order_id}/details", route="/orders/{id}/details",
            operation="order_details",
        )

    def order_specification(self, order_id: int) -> Any:
        order_id = _positive_id(order_id, "order_id")
        return self._request(
            "GET", f"/orders/{order_id}/specification",
            route="/orders/{id}/specification", operation="order_specification",
        )

    # CAD models
    def list_cad_models(self, order_id: int) -> Any:
        order_id = _positive_id(order_id, "order_id")
        return self._request(
            "GET", "/cad-models", route="/cad-models", operation="list_cad_models",
            params={"orderId": order_id},
        )

    def upload_cad_model(
        self,
        order_id: int,
        file: str | Path,
        count: int = 1,
        cut_models: bool = True,
        *,
        idempotency_key: str,
    ) -> Any:
        order_id = _positive_id(order_id, "order_id")
        if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
            raise ValueError("count must be a positive integer")
        if not isinstance(cut_models, bool):
            raise TypeError("cut_models must be boolean")
        path = Path(file)
        if path.suffix.lower() not in SUPPORTED_MODEL_EXTENSIONS:
            raise ValueError("unsupported CAD model extension")
        if not path.is_file():
            raise FileNotFoundError(path)
        size = path.stat().st_size
        if size <= 0 or size > self.max_upload_bytes:
            raise ValueError("CAD model size is outside the approved range")
        digest = sha256_file(path)
        if self.model_sha256 is not None and digest != self.model_sha256:
            raise self._error(
                "cutting.fixture.model_hash", "model hash differs from the approved ledger run",
            )
        with path.open("rb") as handle:
            return self._request(
                "POST", "/cad-models", route="/cad-models", operation="upload_cad_model",
                params={"orderId": order_id, "count": count, "cutModels": cut_models},
                files={"models": (path.name, handle, "application/octet-stream")},
                idempotency_key=idempotency_key,
                fingerprint_meta={"extension": path.suffix.lower(), "bytes": size, "sha256": digest},
            )

    def cad_model_materials(self, model_id: int) -> list[str]:
        model_id = _positive_id(model_id, "model_id")

        def validate(value: Any) -> list[str]:
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise TypeError("materials response must be an array of strings")
            return value

        return self._request(
            "GET", f"/cad-models/{model_id}/materials",
            route="/cad-models/{id}/materials", operation="cad_model_materials",
            response_validator=validate,
        )

    def set_link_materials(
        self,
        model_id: int,
        links: Sequence[Mapping[str, Any]],
        *,
        idempotency_key: str,
    ) -> Any:
        model_id = _positive_id(model_id, "model_id")
        body = serialize_link_payload(links)
        if not body:
            raise ValueError("material links must not be empty")
        return self._request(
            "POST", f"/cad-models/{model_id}/set-link-materials",
            route="/cad-models/{id}/set-link-materials", operation="set_link_materials",
            json_body=body, idempotency_key=idempotency_key,
            fingerprint_meta={"model_ref": sha256_json(model_id), "link_count": len(body)},
        )

    def cutting_materials(self, order_id: int) -> Any:
        order_id = _positive_id(order_id, "order_id")
        return self._request(
            "GET", "/cutting-materials", route="/cutting-materials",
            operation="cutting_materials", params={"orderId": order_id},
        )

    def cutted_materials(self, order_id: int) -> Any:
        order_id = _positive_id(order_id, "order_id")
        return self._request(
            "GET", f"/orders/{order_id}/cutted-materials",
            route="/orders/{id}/cutted-materials", operation="cutted_materials",
        )

    # Production
    def run_cutting(self, order_id: int, *, idempotency_key: str) -> Any:
        order_id = _positive_id(order_id, "order_id")
        return self._request(
            "POST", f"/orders/{order_id}/run-cutting",
            route="/orders/{id}/run-cutting", operation="run_cutting",
            idempotency_key=idempotency_key,
            fingerprint_meta={"order_ref": sha256_json(order_id)},
        )

    def run_production_files(self, order_id: int, *, idempotency_key: str) -> Any:
        order_id = _positive_id(order_id, "order_id")
        return self._request(
            "POST", f"/orders/{order_id}/run-generation-production-files",
            route="/orders/{id}/run-generation-production-files",
            operation="run_production_files", idempotency_key=idempotency_key,
            fingerprint_meta={"order_ref": sha256_json(order_id)},
        )

    def production_files_url(self, order_id: int) -> Any:
        order_id = _positive_id(order_id, "order_id")
        return self._request(
            "GET", f"/orders/{order_id}/production-files-url",
            route="/orders/{id}/production-files-url", operation="production_files_url",
            response_validator=_validated_https_url,
        )

    def run_control_program(self, order_id: int, *, idempotency_key: str) -> Any:
        order_id = _positive_id(order_id, "order_id")
        return self._request(
            "POST", f"/orders/{order_id}/run-generation-control-program-files",
            route="/orders/{id}/run-generation-control-program-files",
            operation="run_control_program", idempotency_key=idempotency_key,
            fingerprint_meta={"order_ref": sha256_json(order_id)},
        )

    def control_program_url(self, order_id: int) -> Any:
        order_id = _positive_id(order_id, "order_id")
        return self._request(
            "GET", f"/orders/{order_id}/control-program-files-url",
            route="/orders/{id}/control-program-files-url", operation="control_program_url",
            response_validator=_validated_https_url,
        )

    # Long tasks
    def list_long_tasks(self) -> Any:
        return self._request("GET", "/long-tasks", route="/long-tasks", operation="list_long_tasks")

    def long_task(self, task_id: int) -> Any:
        task_id = _positive_id(task_id, "task_id")
        return self._request(
            "GET", f"/long-tasks/{task_id}", route="/long-tasks/{id}", operation="long_task",
        )

    def poll_long_task(self, task_id: int, *, timeout: float = 600, interval: float = 5) -> Any:
        task_id = _positive_id(task_id, "task_id")
        if timeout <= 0 or interval <= 0:
            raise ValueError("timeout and interval must be positive")
        local_deadline = min(self.clock() + timeout, self.clock() + self.remaining_seconds())
        while True:
            if self.clock() >= local_deadline:
                raise self._error(
                    "cutting.long_task.timeout", "long task exceeded its bounded deadline",
                    retryable=True,
                )
            task = self.long_task(task_id)
            if not isinstance(task, Mapping):
                raise self._error("cutting.response.contract", "invalid long-task response")
            state = str(task.get("state", task.get("status", ""))).lower()
            if state in ("success", "completed", "2"):
                return task
            if state in ("failed", "error", "3"):
                raise self._error("cutting.long_task.failed", "Cutting long task failed")
            remaining = min(interval, local_deadline - self.clock(), self.remaining_seconds())
            if remaining <= 0:
                raise self._error("cutting.long_task.timeout", "long task deadline expired")
            self.sleeper(remaining)


def api_overview() -> str:
    return "\n".join([
        f"BAZIS Cloud Cutting Public API: {PINNED_ORIGIN}{PREFIX}",
        "The ordinary CLI exposes information only; live operations use the operator entrypoint.",
        "Offline contract: python qa/cutting_contract_harness.py",
        "Operator entrypoint: operator/cutting_live_smoke.py (separate authorization required).",
        "Every mutation requires an approved durable ledger run and unique key.",
        "Ambiguous operations require remote reconciliation and are never retried.",
    ])
