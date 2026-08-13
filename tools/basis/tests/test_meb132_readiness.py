from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from qa.meb132_readiness import build_report  # noqa: E402


def test_parent_readiness_report_never_claims_external_e2e() -> None:
    report = build_report()

    assert report["task"] == "MEB-132"
    assert report["scope"] == "offline"
    assert report["products"]["total"] > 0
    assert report["products"]["passed"] + report["products"]["failed"] \
        == report["products"]["total"]
    assert report["ready"] is False
    assert {gate["owner"] for gate in report["external_acceptance"]} \
        == {"MEB-137", "MEB-138", "MEB-139", "MEB-140"}
    assert all(gate["status"] == "blocked" for gate in report["external_acceptance"])
