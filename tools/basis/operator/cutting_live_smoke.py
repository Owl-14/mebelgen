"""Separately authorized live Cutting smoke operator entrypoint.

This file is intentionally outside ``main.py`` and ``qa/``. It has no useful
defaults: an approved fixture digest, durable ledger path, unique run id,
bounded deadline and explicit authorization phrase are all mandatory.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.cloud_cutting import CuttingClient, CuttingError, PINNED_ORIGIN  # noqa: E402
from src.cutting_ledger import CuttingLedgerError, MutationLedger  # noqa: E402
from src.cutting_preflight import load_cutting_fixture, run_cutting_flow  # noqa: E402

AUTHORIZATION_PHRASE = "MEB-140-CUTTING-LIVE-APPROVED"


def _positive(value: str) -> float:
    number = float(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return number


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--approved-fixture-sha256", required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--overall-timeout", type=_positive, required=True)
    parser.add_argument("--production-interval", type=_positive, default=5.0)
    parser.add_argument("--max-mutations", type=int, required=True)
    parser.add_argument("--authorization", required=True)
    args = parser.parse_args(argv)

    if args.authorization != AUTHORIZATION_PHRASE:
        parser.error("exact separate Cutting authorization phrase is required")
    if args.max_mutations != 5:
        parser.error("the reviewed smoke flow requires exactly five durable mutations")
    if not args.ledger.is_absolute():
        parser.error("live ledger path must be absolute")
    ledger_path = args.ledger.resolve()
    try:
        ledger_path.relative_to(ROOT)
    except ValueError:
        pass
    else:
        parser.error("live ledger must be outside the repository")
    if not os.environ.get("BAZIS_API_KEY"):
        parser.error("BAZIS_API_KEY must be supplied through the operator environment")

    # Approval and all hashes are verified before the ledger run or client can
    # execute the first mutation.
    fixture = load_cutting_fixture(
        args.fixture,
        require_live_approval=True,
        approved_fixture_sha256=args.approved_fixture_sha256,
    )
    now = time.time()
    ledger = MutationLedger(ledger_path)
    ledger.register_run(
        run_id=args.run_id,
        mode="live",
        fixture_sha256=fixture.fixture_sha256,
        model_sha256=fixture.model_sha256,
        transport_origin=PINNED_ORIGIN,
        max_mutations=args.max_mutations,
        deadline_epoch=now + args.overall_timeout,
        now_epoch=now,
    )
    trace = []
    client = CuttingClient(
        allow_live=True,
        allow_mutations=True,
        ledger=ledger,
        run_id=args.run_id,
        overall_timeout=args.overall_timeout,
        trace_sink=trace.append,
    )
    if not client.live_evidence_allowed:
        raise RuntimeError("internal pinned transport attestation failed")
    try:
        evidence = run_cutting_flow(
            client,
            fixture,
            production_interval=args.production_interval,
            require_live_evidence=True,
        )
    except CuttingError as exc:
        print(json.dumps(exc.as_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    except (CuttingLedgerError, OSError, TypeError, ValueError) as exc:
        print(json.dumps({
            "code": getattr(exc, "code", "cutting.operator.failed"),
            "message": str(exc),
            "trace_id": client.trace_id,
            "run_id": args.run_id,
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    print(json.dumps({"evidence": evidence, "trace": trace}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
