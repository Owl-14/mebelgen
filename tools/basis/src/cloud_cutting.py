"""Safety-first client for the BAZIS Cloud Cutting Public API.

The endpoint is external and may charge for mutations.  Network access is
therefore disabled by default.  Callers must opt in to live reads and provide
an explicit, bounded mutation budget plus an idempotency key for every POST.
The contract is based on the official OpenAPI 3.0.1 snapshot captured in
repository history at ``5bc53df:docs/bazis_cloud_cutting_swagger.json``.
"""

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

import requests

from .cloud_api import BASE_URL

PREFIX = "/api-cutting-public"
SUPPORTED_MODEL_EXTENSIONS = {
    ".b3d", ".fr3d", ".shn", ".obl", ".oblx", ".zbprj", ".xml",
    ".k3bz", ".cfrn",
}
_ORDER_FIELDS = {"managerId", "clientId", "note", "factoryOrderId", "factoryId", "uid1C"}
_MATERIAL_LINK_FIELDS = {
    "originalMaterialFullName", "materialType", "linkedMaterialFullName",
}
_NO_REPLAY = object()


class CuttingError(RuntimeError):
    """Stable, privacy-safe error returned by the Cutting integration."""

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
            if value <= 0:
                raise ValueError(f"{name} timeout must be positive")


TraceSink = Callable[[Mapping[str, Any]], None]


def _positive_id(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _json_fingerprint(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _resource_ref(value: int) -> str:
    return hashlib.sha256(str(value).encode("ascii")).hexdigest()[:12]


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class CuttingClient:
    """Cutting API client with live, mutation-budget and replay guards.

    ``session`` is injectable so the complete production flow can be tested
    offline without DNS or HTTP access.  The client does not implement hidden
    retries: a timed-out mutation is marked ambiguous and cannot be replayed
    under the same idempotency key.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        key_header: str | None = None,
        *,
        allow_live: bool = False,
        allow_mutations: bool = False,
        max_mutations: int = 0,
        max_upload_bytes: int = 100 * 1024 * 1024,
        timeouts: CuttingTimeouts | None = None,
        session: Any | None = None,
        trace_sink: TraceSink | None = None,
        trace_id: str | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.api_key = api_key or os.environ.get("BAZIS_API_KEY")
        self.base = (base_url or BASE_URL).rstrip("/")
        self.key_header = key_header or os.environ.get("BAZIS_API_KEY_HEADER", "apiKey")
        self.allow_live = allow_live
        self.allow_mutations = allow_mutations
        self.max_mutations = max_mutations
        self.max_upload_bytes = max_upload_bytes
        self.timeouts = timeouts or CuttingTimeouts()
        self.session = session or requests.Session()
        self.trace_sink = trace_sink
        self.trace_id = trace_id or uuid.uuid4().hex
        self.clock = clock
        self.sleeper = sleeper
        self._mutation_count = 0
        self._idempotency: dict[str, dict[str, Any]] = {}

        if max_mutations < 0:
            raise ValueError("max_mutations cannot be negative")
        if max_upload_bytes <= 0:
            raise ValueError("max_upload_bytes must be positive")

    @property
    def mutation_count(self) -> int:
        return self._mutation_count

    def _error(
        self,
        code: str,
        message: str,
        *,
        status: int | None = None,
        retryable: bool = False,
    ) -> CuttingError:
        return CuttingError(
            code, message, trace_id=self.trace_id, status=status, retryable=retryable
        )

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise self._error(
                "cutting.auth.missing",
                "BAZIS_API_KEY is required for live Cutting access.",
            )
        return {self.key_header: self.api_key}

    def _url(self, suffix: str) -> str:
        return f"{self.base}{PREFIX}{suffix}"

    def _emit_trace(self, **event: Any) -> None:
        if self.trace_sink is None:
            return
        safe = {"trace_id": self.trace_id, **event}
        self.trace_sink(copy.deepcopy(safe))

    def _live_guard(self) -> None:
        if not self.allow_live:
            raise self._error(
                "cutting.live_guard.required",
                "Live Cutting access is disabled; pass an explicit live authorization.",
            )

    def _reserve_mutation(self, operation: str, idempotency_key: str, fingerprint: str) -> Any:
        if not idempotency_key or not idempotency_key.strip():
            raise self._error(
                "cutting.idempotency.required",
                f"Mutation {operation} requires a non-empty idempotency key.",
            )
        previous = self._idempotency.get(idempotency_key)
        if previous is not None:
            if previous["fingerprint"] != fingerprint:
                raise self._error(
                    "cutting.idempotency.collision",
                    "The idempotency key was already used for a different request.",
                )
            if previous["state"] == "success":
                self._emit_trace(
                    step=operation,
                    method="POST",
                    route=previous["route"],
                    outcome="idempotent_replay",
                    mutation_count=self._mutation_count,
                )
                return copy.deepcopy(previous["result"])
            raise self._error(
                "cutting.idempotency.ambiguous",
                "The earlier mutation may have reached the server; inspect remote state before retrying.",
            )
        if not self.allow_mutations:
            raise self._error(
                "cutting.cost_guard.required",
                "Cutting mutations are disabled; explicit mutation authorization is required.",
            )
        if self._mutation_count >= self.max_mutations:
            raise self._error(
                "cutting.cost_guard.exhausted",
                f"Mutation budget exhausted before {operation}.",
            )
        return _NO_REPLAY

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
        mutation = method.upper() != "GET"
        fingerprint = ""
        if mutation:
            fingerprint = _json_fingerprint({
                "operation": operation,
                "route": route,
                "params": params,
                "json": json_body,
                "meta": fingerprint_meta,
            })
            replay = self._reserve_mutation(operation, idempotency_key or "", fingerprint)
            if replay is not _NO_REPLAY:
                return replay
            self._mutation_count += 1
            self._idempotency[idempotency_key or ""] = {
                "fingerprint": fingerprint,
                "state": "ambiguous",
                "route": route,
            }

        started = self.clock()
        timeout = (
            self.timeouts.connect,
            self.timeouts.mutation_read if mutation else self.timeouts.read,
        )
        try:
            response = self.session.request(
                method.upper(),
                self._url(suffix),
                headers=self._headers(),
                params=dict(params) if params else None,
                json=json_body,
                files=files,
                timeout=timeout,
            )
        except requests.Timeout as exc:
            code = "cutting.transport.ambiguous" if mutation else "cutting.transport.timeout"
            self._emit_trace(
                step=operation, method=method.upper(), route=route,
                outcome="error", error_code=code,
                duration_ms=round((self.clock() - started) * 1000),
                mutation_count=self._mutation_count,
            )
            raise self._error(
                code,
                "Cutting request timed out. Inspect remote state before retrying a mutation."
                if mutation else "Cutting request timed out.",
                retryable=not mutation,
            ) from exc
        except requests.RequestException as exc:
            code = "cutting.transport.ambiguous" if mutation else "cutting.transport.unavailable"
            self._emit_trace(
                step=operation, method=method.upper(), route=route,
                outcome="error", error_code=code,
                duration_ms=round((self.clock() - started) * 1000),
                mutation_count=self._mutation_count,
            )
            raise self._error(
                code,
                "Cutting transport failed. Inspect remote state before retrying a mutation."
                if mutation else "Cutting service is unavailable.",
                retryable=not mutation,
            ) from exc

        status = int(response.status_code)
        if not 200 <= status < 300:
            code, retryable = self._classify_http(status)
            self._emit_trace(
                step=operation, method=method.upper(), route=route,
                outcome="error", error_code=code, http_status=status,
                duration_ms=round((self.clock() - started) * 1000),
                mutation_count=self._mutation_count,
            )
            raise self._error(
                code,
                f"Cutting API rejected {operation} (HTTP {status}).",
                status=status,
                retryable=retryable and not mutation,
            )

        try:
            result = self._decode_response(response, operation)
            if response_validator is not None:
                result = response_validator(result)
        except (TypeError, ValueError, KeyError) as exc:
            contract_error = self._error(
                "cutting.response.contract",
                f"Cutting API returned an invalid {operation} response.",
                status=status,
            )
            self._emit_trace(
                step=operation, method=method.upper(), route=route,
                outcome="error", error_code=contract_error.code, http_status=status,
                duration_ms=round((self.clock() - started) * 1000),
                mutation_count=self._mutation_count,
            )
            raise contract_error from exc
        except CuttingError as exc:
            self._emit_trace(
                step=operation, method=method.upper(), route=route,
                outcome="error", error_code=exc.code, http_status=status,
                duration_ms=round((self.clock() - started) * 1000),
                mutation_count=self._mutation_count,
            )
            raise
        if mutation:
            self._idempotency[idempotency_key or ""].update(
                state="success", result=copy.deepcopy(result)
            )
        self._emit_trace(
            step=operation, method=method.upper(), route=route,
            outcome="success", http_status=status,
            duration_ms=round((self.clock() - started) * 1000),
            mutation_count=self._mutation_count,
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
            except (ValueError, json.JSONDecodeError) as exc:
                raise self._error(
                    "cutting.response.invalid_json",
                    f"Cutting API returned malformed JSON for {operation}.",
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

    @staticmethod
    def _validate_material_links(links: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        if isinstance(links, (str, bytes)) or not isinstance(links, Sequence) or not links:
            raise ValueError("material links must be a non-empty array")
        validated: list[dict[str, Any]] = []
        for index, link in enumerate(links):
            if not isinstance(link, Mapping):
                raise TypeError(f"material link {index} must be an object")
            if set(link) != _MATERIAL_LINK_FIELDS:
                raise ValueError(f"material link {index} must contain exactly the contract fields")
            original = link["originalMaterialFullName"]
            linked = link["linkedMaterialFullName"]
            material_type = link["materialType"]
            if not isinstance(original, str) or not original.strip():
                raise ValueError(f"material link {index} has no original material name")
            if not isinstance(linked, str) or not linked.strip():
                raise ValueError(f"material link {index} has no linked material name")
            if isinstance(material_type, bool) or not isinstance(material_type, int) or not 0 <= material_type <= 5:
                raise ValueError(f"material link {index} materialType must be 0..5")
            validated.append(dict(link))
        return validated

    # Orders
    def list_orders(self, page_index: int = 0, page_size: int = 20, **filters: Any) -> Any:
        if page_index < 0 or not 1 <= page_size <= 1000:
            raise ValueError("page_index must be >= 0 and page_size must be 1..1000")
        return self._request(
            "GET", "/orders", route="/orders", operation="list_orders",
            params={"pageIndex": page_index, "pageSize": page_size, **filters},
        )

    def create_order(self, payload: Mapping[str, Any], *, idempotency_key: str) -> Any:
        body = self._validate_order_payload(payload)
        return self._request(
            "POST", "/orders", route="/orders", operation="create_order",
            json_body=body, idempotency_key=idempotency_key,
        )

    def get_order(self, order_id: int) -> Any:
        order_id = _positive_id(order_id, "order_id")
        return self._request(
            "GET", f"/orders/{order_id}", route="/orders/{id}", operation="get_order",
        )

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
        if size <= 0:
            raise ValueError("CAD model must not be empty")
        if size > self.max_upload_bytes:
            raise ValueError("CAD model exceeds max_upload_bytes")
        digest = _file_sha256(path)
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
        body = self._validate_material_links(links)
        return self._request(
            "POST", f"/cad-models/{model_id}/set-link-materials",
            route="/cad-models/{id}/set-link-materials", operation="set_link_materials",
            json_body=body, idempotency_key=idempotency_key,
            fingerprint_meta={"model_ref": _resource_ref(model_id), "link_count": len(body)},
        )

    # Production
    def run_cutting(self, order_id: int, *, idempotency_key: str) -> Any:
        order_id = _positive_id(order_id, "order_id")
        return self._request(
            "POST", f"/orders/{order_id}/run-cutting",
            route="/orders/{id}/run-cutting", operation="run_cutting",
            idempotency_key=idempotency_key,
            fingerprint_meta={"order_ref": _resource_ref(order_id)},
        )

    def run_production_files(self, order_id: int, *, idempotency_key: str) -> Any:
        order_id = _positive_id(order_id, "order_id")
        return self._request(
            "POST", f"/orders/{order_id}/run-generation-production-files",
            route="/orders/{id}/run-generation-production-files",
            operation="run_production_files", idempotency_key=idempotency_key,
            fingerprint_meta={"order_ref": _resource_ref(order_id)},
        )

    def production_files_url(self, order_id: int) -> Any:
        order_id = _positive_id(order_id, "order_id")
        return self._request(
            "GET", f"/orders/{order_id}/production-files-url",
            route="/orders/{id}/production-files-url", operation="production_files_url",
        )

    def run_control_program(self, order_id: int, *, idempotency_key: str) -> Any:
        order_id = _positive_id(order_id, "order_id")
        return self._request(
            "POST", f"/orders/{order_id}/run-generation-control-program-files",
            route="/orders/{id}/run-generation-control-program-files",
            operation="run_control_program", idempotency_key=idempotency_key,
            fingerprint_meta={"order_ref": _resource_ref(order_id)},
        )

    def control_program_url(self, order_id: int) -> Any:
        order_id = _positive_id(order_id, "order_id")
        return self._request(
            "GET", f"/orders/{order_id}/control-program-files-url",
            route="/orders/{id}/control-program-files-url", operation="control_program_url",
        )

    # Long tasks
    def list_long_tasks(self) -> Any:
        return self._request(
            "GET", "/long-tasks", route="/long-tasks", operation="list_long_tasks",
        )

    def long_task(self, task_id: int) -> Any:
        task_id = _positive_id(task_id, "task_id")
        return self._request(
            "GET", f"/long-tasks/{task_id}", route="/long-tasks/{id}", operation="long_task",
        )

    def poll_long_task(self, task_id: int, *, timeout: float = 600, interval: float = 5) -> Any:
        task_id = _positive_id(task_id, "task_id")
        if timeout <= 0 or interval <= 0:
            raise ValueError("timeout and interval must be positive")
        deadline = self.clock() + timeout
        first_poll = True
        while True:
            if not first_poll and self.clock() >= deadline:
                raise self._error(
                    "cutting.long_task.timeout",
                    "Cutting long task did not complete within the bounded timeout.",
                    retryable=True,
                )
            first_poll = False
            task = self.long_task(task_id)
            if not isinstance(task, Mapping):
                raise self._error(
                    "cutting.response.contract",
                    "Cutting API returned an invalid long-task response.",
                )
            state = str(task.get("state", task.get("status", ""))).lower()
            if state in ("success", "completed", "2"):
                return task
            if state in ("failed", "error", "3"):
                raise self._error(
                    "cutting.long_task.failed",
                    "Cutting long task failed; inspect remote task details.",
                )
            self.sleeper(min(interval, max(0.0, deadline - self.clock())))


def api_overview() -> str:
    return "\n".join([
        f"BAZIS Cloud Cutting Public API: {BASE_URL}{PREFIX}",
        "Network access is disabled by default.",
        "Offline contract: python qa/cutting_contract_harness.py",
        "Live mutations require --allow-live, --allow-mutations, a bounded budget,",
        "and a unique idempotency prefix. Never retry an ambiguous mutation.",
        "Flow: create order -> upload .b3d -> read/link materials -> run-cutting",
        "      -> generation-production-files -> production-files-url.",
        "Key: env BAZIS_API_KEY (header apiKey).",
    ])
