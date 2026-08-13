"""Machine-readable offline readiness report for the MEB-132 parent contour.

The report intentionally never calls Basis, Cutting or LLM services.  External
acceptance rows remain blocked until evidence from the licensed environment is
attached by MEB-137--140.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.production_gate import evaluate_production_gate  # noqa: E402


def build_report() -> dict[str, Any]:
    products = []
    for path in sorted((ROOT / "paramspecs").glob("*.json")):
        if path.name.endswith((".project.json", ".versions.json")):
            continue
        spec = json.loads(path.read_text(encoding="utf-8"))
        if spec.get("schemaVersion") != "paramspec-v1":
            continue
        decision = evaluate_production_gate(spec)
        products.append({
            "paramspec": path.name,
            "ok": decision.report.ok,
            "error_codes": sorted({issue.code for issue in decision.report.errors}),
        })

    external = [
        {"gate": "licensed_basis_environment", "owner": "MEB-137", "status": "blocked"},
        {"gate": "native_json_import_to_saved_b3d", "owner": "MEB-138", "status": "blocked"},
        {"gate": "production_matbase_mapping", "owner": "MEB-139", "status": "blocked"},
        {"gate": "cutting_to_production_files", "owner": "MEB-140", "status": "blocked"},
    ]
    passed = sum(item["ok"] for item in products)
    return {
        "task": "MEB-132",
        "scope": "offline",
        "ready": passed == len(products) and all(x["status"] == "passed" for x in external),
        "products": {"total": len(products), "passed": passed,
                     "failed": len(products) - passed, "results": products},
        "external_acceptance": external,
        "limitations": [
            "No paid Basis/Cutting/LLM API was called.",
            "A repository fixture or CFRN check does not prove native Basis import E2E.",
            "External gates must not be changed to passed without licensed-environment evidence.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = build_report()
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
