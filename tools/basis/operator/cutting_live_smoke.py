"""Separately authorized live Cutting smoke operator entrypoint.

The fixture, deadline, mutation limit and ledger identity come only from the
machine's canonical approved config. Callers cannot provide a ledger path,
fixture path, approval digest or run id.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.cloud_cutting import CuttingError  # noqa: E402
from src.cutting_ledger import CuttingLedgerError  # noqa: E402
from src.cutting_operator_trust import (  # noqa: E402
    build_operator_client,
    load_canonical_operator_approval,
)
from src.cutting_preflight import run_cutting_flow  # noqa: E402

AUTHORIZATION_PHRASE = "MEB-140-CUTTING-LIVE-APPROVED"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorization", required=True)
    args = parser.parse_args(argv)
    if args.authorization != AUTHORIZATION_PHRASE:
        parser.error("exact separate Cutting authorization phrase is required")
    api_key = os.environ.get("BAZIS_API_KEY")
    if not api_key:
        parser.error("BAZIS_API_KEY must be supplied through the operator environment")

    trace = []
    client = None
    try:
        approval = load_canonical_operator_approval()
        client, _ledger, _run = build_operator_client(
            approval, api_key=api_key, trace_sink=trace.append,
        )
        evidence = run_cutting_flow(
            client,
            approval.fixture,
            production_interval=approval.production_interval,
            require_live_evidence=True,
        )
    except CuttingError as exc:
        print(json.dumps(exc.as_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    except (CuttingLedgerError, OSError, RuntimeError, TypeError, ValueError) as exc:
        payload = {
            "code": getattr(exc, "code", "cutting.operator.failed"),
            "message": str(exc),
        }
        if client is not None:
            payload["trace_id"] = client.trace_id
            payload["run_id"] = client.run_id
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    print(json.dumps({"evidence": evidence, "trace": trace}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
