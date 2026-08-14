#!/usr/bin/env python3
"""Print a read-only ParamSpec v1 compatibility and equivalence report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.paramspec_migration import audit_paramspec_v1_catalogs  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-root", required=True, type=Path)
    parser.add_argument("--tenant-root", required=True, type=Path)
    args = parser.parse_args()
    report = audit_paramspec_v1_catalogs(args.legacy_root, args.tenant_root)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return (
        1
        if report["invalid"]
        or report["equivalence_failed"]
        or report["canonical_idempotence_failed"]
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
