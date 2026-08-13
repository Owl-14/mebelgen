#!/usr/bin/env python3
"""Generate deterministic offline evidence before desktop BAZIS import."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.basis_import_preflight import preflight_file  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project", type=Path, help="BasisProductionModel project JSON")
    parser.add_argument("--output", type=Path, help="write the report to this JSON file")
    args = parser.parse_args()

    report = preflight_file(args.project.resolve(), source=args.project.as_posix())
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0 if report["source_preflight_status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
