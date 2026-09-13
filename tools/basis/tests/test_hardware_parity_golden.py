"""Production drilling goldens and Studio ↔ CFRN ↔ B3D parity (MEB-152)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from hardware_parity_report import _baseline_digest, build_report  # noqa: E402


GOLDEN = ROOT / "qa" / "fixtures" / "hardware_connections_golden.json"
APPROVAL = ROOT / "qa" / "approvals" / "hardware_connections_approval.json"
REQUIRED_CASES = {
    "dowel_eccentric", "confirmat", "removable_shelf", "fixed_shelf",
    "hinges", "guides", "back", "rod", "metal_frame",
}


def test_hardware_connection_goldens_and_cross_format_parity():
    config = json.loads(GOLDEN.read_text(encoding="utf-8"))
    approval = json.loads(APPROVAL.read_text(encoding="utf-8"))
    assert set(config["cases"]) == REQUIRED_CASES
    report = build_report(config, approval)
    assert report["cloud_calls"] == 0
    assert report["ok"], json.dumps(report, ensure_ascii=False, indent=2)
    assert report["approval_provenance"]["ok"]
    assert report["approved_b3d_evidence"]["not_computed_parity"] is True
    assert "approved_b3d_evidence" not in report["computed_parity"]


def test_every_golden_locks_full_drilling_signature_and_bom():
    config = json.loads(GOLDEN.read_text(encoding="utf-8"))
    for name, case in config["cases"].items():
        expected = case["expected"]
        assert expected["count"] > 0, name
        assert len(expected["sha256"]) == 64, name
        assert expected["bom"], name


def test_recalculated_baseline_requires_separate_approval_change():
    config = json.loads(GOLDEN.read_text(encoding="utf-8"))
    approval = json.loads(APPROVAL.read_text(encoding="utf-8"))
    config["cases"]["rod"]["expected"]["count"] += 1
    report = build_report(config, approval)
    assert not report["approval_provenance"]["ok"]
    assert any(
        diff["field"] == "baseline_sha256"
        for diff in report["approval_provenance"]["differences"]
    )


def test_fixed_shelf_cannot_be_replaced_by_dowel_eccentric_class():
    config = json.loads(GOLDEN.read_text(encoding="utf-8"))
    approval = json.loads(APPROVAL.read_text(encoding="utf-8"))
    config["cases"]["fixed_shelf"] = json.loads(json.dumps(
        config["cases"]["dowel_eccentric"], ensure_ascii=False
    ))
    # Even a deliberate approval-digest update cannot bypass class semantics.
    approval["approval"]["baseline_sha256"] = _baseline_digest(config)
    report = build_report(config, approval)
    assert report["approval_provenance"]["ok"]
    fixed = report["computed_parity"]["cases"]["fixed_shelf"]
    assert not fixed["ok"]
    assert {diff["field"] for diff in fixed["differences"]} >= {
        "approved_paramspec", "topology",
    }
