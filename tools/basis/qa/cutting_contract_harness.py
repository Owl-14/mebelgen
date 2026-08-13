"""Offline-first Cutting contract harness for MEB-140.

Default execution uses a scripted in-memory HTTP session and cannot access the
network.  ``--live`` exists only to print/execute the separately authorized
smoke command; it keeps all safety guards enabled and never guesses material
links or retries an ambiguous mutation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.cloud_cutting import CuttingClient, CuttingError, CuttingTimeouts  # noqa: E402

DEFAULT_FIXTURE = ROOT / "qa" / "fixtures" / "cutting_test_order.json"


@dataclass
class _Response:
    status_code: int
    payload: Any = None
    content_type: str = "application/json"

    @property
    def headers(self) -> dict[str, str]:
        return {"content-type": self.content_type}

    @property
    def content(self) -> bytes:
        if self.payload is None:
            return b""
        if "json" in self.content_type:
            return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")
        return str(self.payload).encode("utf-8")

    @property
    def text(self) -> str:
        return self.content.decode("utf-8")

    def json(self) -> Any:
        return json.loads(self.content)


class _ScriptedSession:
    """Minimal requests-compatible session; it has no network implementation."""

    def __init__(self, responses: Sequence[_Response]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> _Response:
        if not self.responses:
            raise AssertionError("unexpected extra Cutting request")
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


def _load_fixture(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "contractVersion", "order", "model", "expectedSourceMaterials",
        "materialLinks", "expectedOrder",
    }
    if set(data) != required:
        raise ValueError("fixture must contain exactly the Cutting harness contract fields")
    if data["contractVersion"] != "cutting-public-openapi-2026-06-30":
        raise ValueError("unsupported Cutting fixture contractVersion")
    return data


def _extract_id(value: Any, kind: str) -> int:
    candidate = value
    if isinstance(value, list) and value:
        candidate = value[0]
    if isinstance(candidate, Mapping):
        for field in ("id", f"{kind}Id"):
            if field in candidate:
                candidate = candidate[field]
                break
    if isinstance(candidate, str) and candidate.isdigit():
        candidate = int(candidate)
    if isinstance(candidate, bool) or not isinstance(candidate, int) or candidate <= 0:
        raise ValueError(f"{kind} response does not contain a positive id")
    return candidate


def _ref(value: int) -> str:
    return hashlib.sha256(str(value).encode("ascii")).hexdigest()[:12]


def _assert_material_contract(source: Sequence[str], fixture: Mapping[str, Any]) -> None:
    expected = fixture["expectedSourceMaterials"]
    if list(source) != expected:
        raise ValueError(
            "cutting.materials.unverified: source materials differ from the approved fixture; "
            "do not submit guessed links"
        )
    linked_sources = [item["originalMaterialFullName"] for item in fixture["materialLinks"]]
    if sorted(linked_sources) != sorted(source):
        raise ValueError(
            "cutting.materials.incomplete: every returned source material must have exactly one approved link"
        )


def _poll_production_url(
    client: CuttingClient,
    order_id: int,
    *,
    timeout: float,
    interval: float,
) -> Any:
    deadline = time.monotonic() + timeout
    while True:
        try:
            return client.production_files_url(order_id)
        except CuttingError as exc:
            if exc.code != "cutting.http.not_found" or time.monotonic() >= deadline:
                raise
            time.sleep(min(interval, max(0.0, deadline - time.monotonic())))


def run_flow(
    client: CuttingClient,
    fixture: Mapping[str, Any],
    fixture_path: Path,
    *,
    idempotency_prefix: str,
    evidence_mode: str = "offline_contract",
    production_timeout: float = 600.0,
    production_interval: float = 5.0,
) -> dict[str, Any]:
    model_path = (fixture_path.parent / fixture["model"]["path"]).resolve()
    order_payload = dict(fixture["order"])
    if order_payload.get("factoryOrderId") == "${IDEMPOTENCY_PREFIX}":
        order_payload["factoryOrderId"] = idempotency_prefix

    order_id = _extract_id(
        client.create_order(order_payload, idempotency_key=f"{idempotency_prefix}:create"),
        "order",
    )
    model_response = client.upload_cad_model(
        order_id,
        model_path,
        count=fixture["model"]["count"],
        cut_models=fixture["model"]["cutModels"],
        idempotency_key=f"{idempotency_prefix}:upload",
    )
    model_id = _extract_id(model_response, "model")
    source_materials = client.cad_model_materials(model_id)
    _assert_material_contract(source_materials, fixture)
    client.set_link_materials(
        model_id,
        fixture["materialLinks"],
        idempotency_key=f"{idempotency_prefix}:materials",
    )
    client.run_cutting(order_id, idempotency_key=f"{idempotency_prefix}:cutting")
    client.run_production_files(order_id, idempotency_key=f"{idempotency_prefix}:production")
    production_url = _poll_production_url(
        client,
        order_id,
        timeout=production_timeout,
        interval=production_interval,
    )
    if not isinstance(production_url, str) or not production_url.strip():
        raise ValueError("cutting.response.contract: production files URL is missing")
    return {
        "mode": evidence_mode,
        "contract_version": fixture["contractVersion"],
        "order_ref": _ref(order_id),
        "model_ref": _ref(model_id),
        "material_count": len(source_materials),
        "mutation_count": client.mutation_count,
        "production_files_response_valid": True,
        "test_order": list(fixture["expectedOrder"]),
        "trace_id": client.trace_id,
    }


def _offline_client(fixture: Mapping[str, Any], trace: list[Mapping[str, Any]]) -> tuple[CuttingClient, _ScriptedSession]:
    session = _ScriptedSession([
        _Response(200, 140001),
        _Response(200, [{"id": 140002}]),
        _Response(200, fixture["expectedSourceMaterials"]),
        _Response(200),
        _Response(200),
        _Response(200),
        _Response(200, "https://example.invalid/signed-production-archive"),
    ])
    client = CuttingClient(
        api_key="offline-contract-key",
        base_url="https://offline.invalid",
        allow_live=True,
        allow_mutations=True,
        max_mutations=5,
        session=session,
        trace_sink=trace.append,
        timeouts=CuttingTimeouts(connect=1, read=1, mutation_read=1),
    )
    return client, session


def _verify_offline_calls(session: _ScriptedSession, fixture: Mapping[str, Any]) -> None:
    actual = [
        f"{call['method']} {urlsplit(call['url']).path.replace('/api-cutting-public', '')}"
        for call in session.calls
    ]
    expected = list(fixture["expectedOrder"])
    if actual != expected:
        raise AssertionError(f"request order mismatch: {actual!r}")
    upload = session.calls[1]
    if set(upload["files"]) != {"models"}:
        raise AssertionError("OpenAPI requires multipart field 'models'")
    if upload["params"] != {"orderId": 140001, "count": 1, "cutModels": True}:
        raise AssertionError("upload query contract mismatch")
    links = session.calls[3]
    if links["json"] != fixture["materialLinks"]:
        raise AssertionError("material link payload mismatch")
    if any(call["headers"] != {"apiKey": "offline-contract-key"} for call in session.calls):
        raise AssertionError("apiKey header contract mismatch")
    if session.responses:
        raise AssertionError("not all scripted responses were consumed")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--live", action="store_true", help="use the real Cutting endpoint")
    parser.add_argument("--allow-mutations", action="store_true")
    parser.add_argument("--max-mutations", type=int, default=0)
    parser.add_argument("--idempotency-prefix")
    parser.add_argument("--production-timeout", type=float, default=600)
    parser.add_argument("--production-interval", type=float, default=5)
    args = parser.parse_args(argv)

    fixture_path = args.fixture.resolve()
    fixture = _load_fixture(fixture_path)
    trace: list[Mapping[str, Any]] = []
    if args.live:
        if not args.allow_mutations or args.max_mutations != 5:
            parser.error("live smoke requires --allow-mutations --max-mutations 5")
        if not args.idempotency_prefix or len(args.idempotency_prefix) < 12:
            parser.error("live smoke requires a unique idempotency prefix (12+ characters)")
        if not os.environ.get("BAZIS_API_KEY"):
            parser.error("live smoke requires BAZIS_API_KEY in the environment")
        client = CuttingClient(
            allow_live=True,
            allow_mutations=True,
            max_mutations=args.max_mutations,
            trace_sink=trace.append,
        )
        session = None
    else:
        if args.allow_mutations or args.idempotency_prefix:
            parser.error("mutation authorization flags are only valid together with --live")
        client, session = _offline_client(fixture, trace)
        args.idempotency_prefix = "MEB-140-OFFLINE"

    try:
        evidence = run_flow(
            client,
            fixture,
            fixture_path,
            idempotency_prefix=args.idempotency_prefix,
            evidence_mode="live" if args.live else "offline_contract",
            production_timeout=args.production_timeout,
            production_interval=args.production_interval,
        )
        if session is not None:
            _verify_offline_calls(session, fixture)
    except CuttingError as exc:
        print(json.dumps(exc.as_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    except (AssertionError, OSError, TypeError, ValueError) as exc:
        print(json.dumps({
            "code": "cutting.preflight.failed",
            "message": str(exc),
            "trace_id": client.trace_id,
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2

    output = {"evidence": evidence, "trace": trace}
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
