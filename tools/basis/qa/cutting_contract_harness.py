"""Network-impossible offline Cutting contract harness for MEB-140.

This entrypoint has no live flags. It uses an injected scripted session, an
explicitly allowlisted test origin, a non-production key and a temporary
durable SQLite ledger. It can never produce ``mode=live`` evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.cloud_cutting import CuttingClient, CuttingError, CuttingTimeouts  # noqa: E402
from src.cutting_ledger import MutationLedger  # noqa: E402
from src.cutting_preflight import load_cutting_fixture, run_cutting_flow  # noqa: E402

DEFAULT_FIXTURE = ROOT / "qa" / "fixtures" / "cutting_test_order.json"
TEST_ORIGIN = "https://cutting-contract.invalid"


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
    """Requests-compatible object with no socket or network implementation."""

    def __init__(self, responses: Sequence[_Response]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> _Response:
        if not self.responses:
            raise AssertionError("unexpected extra Cutting request")
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


def _offline_session(data: Mapping[str, Any]) -> _ScriptedSession:
    return _ScriptedSession([
        _Response(200, 140001),
        _Response(200, [{"id": 140002}]),
        _Response(200, data["cadModelMaterials"]),
        _Response(200),
        _Response(200, data["cuttingMaterials"]),
        _Response(200),
        _Response(200),
        _Response(200, "https://example.invalid/signed-production-archive"),
        _Response(200, data["cuttedMaterials"]),
    ])


def _verify_calls(session: _ScriptedSession, data: Mapping[str, Any]) -> None:
    actual = [
        f"{call['method']} {urlsplit(call['url']).path.replace('/api-cutting-public', '')}"
        for call in session.calls
    ]
    if actual != list(data["expectedOrder"]):
        raise AssertionError(f"request order mismatch: {actual!r}")
    upload = session.calls[1]
    if set(upload["files"]) != {"models"}:
        raise AssertionError("OpenAPI requires multipart field 'models'")
    if upload["params"] != {"orderId": 140001, "count": 1, "cutModels": True}:
        raise AssertionError("upload query contract mismatch")
    links = session.calls[3]
    if links["json"] != [{
        "originalMaterialFullName": "__FIXTURE_CAD_SHEET__",
        "materialType": 0,
        "linkedMaterialFullName": "__FIXTURE_CONFIRMED_MATBASE_TARGET__",
    }]:
        raise AssertionError("strict material-link payload mismatch")
    if any(call["headers"] != {"apiKey": "offline-test-key"} for call in session.calls):
        raise AssertionError("test key transport contract mismatch")
    if any(call["allow_redirects"] is not False or call["verify"] is not True for call in session.calls):
        raise AssertionError("redirect/TLS transport contract mismatch")
    if session.responses:
        raise AssertionError("not all scripted responses were consumed")


def run_offline(fixture_path: Path) -> dict[str, Any]:
    fixture = load_cutting_fixture(fixture_path, require_live_approval=False)
    trace: list[Mapping[str, Any]] = []
    session = _offline_session(fixture.data)
    with tempfile.TemporaryDirectory(prefix="meb140-cutting-ledger-") as temp_dir:
        ledger = MutationLedger(Path(temp_dir) / "ledger.sqlite3")
        run = ledger.approve_run(
            approval_digest=hashlib.sha256(
                f"offline-contract:{fixture.fixture_sha256}".encode("ascii")
            ).hexdigest(),
            mode="offline_contract",
            fixture_sha256=fixture.fixture_sha256,
            model_sha256=fixture.model_sha256,
            transport_origin=TEST_ORIGIN,
            max_mutations=5,
            deadline_epoch=10_000_000_000,
        )
        client = CuttingClient(
            base_url=TEST_ORIGIN,
            test_api_key="offline-test-key",
            test_endpoint_allowlist=[TEST_ORIGIN],
            allow_live=True,
            allow_mutations=True,
            ledger=ledger,
            ledger_run=run,
            overall_timeout=30,
            session=session,
            trace_sink=trace.append,
            timeouts=CuttingTimeouts(connect=1, read=1, mutation_read=1),
        )
        evidence = run_cutting_flow(client, fixture, production_interval=0.01)
        if client.live_evidence_allowed or evidence["mode"] != "offline_contract":
            raise AssertionError("injected offline session cannot claim live evidence")
        if ledger.run_operation_count(client.run_id) != 5:
            raise AssertionError("durable mutation count mismatch")
        _verify_calls(session, fixture.data)
    return {"evidence": evidence, "trace": trace}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    args = parser.parse_args(argv)
    try:
        output = run_offline(args.fixture.resolve())
    except CuttingError as exc:
        print(json.dumps(exc.as_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    except (AssertionError, OSError, TypeError, ValueError) as exc:
        print(json.dumps({
            "code": "cutting.preflight.failed", "message": str(exc),
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
