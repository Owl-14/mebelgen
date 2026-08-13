"""Production drilling goldens and Studio ↔ CFRN ↔ B3D parity (MEB-152)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from hardware_parity_report import build_report  # noqa: E402


GOLDEN = ROOT / "qa" / "fixtures" / "hardware_connections_golden.json"
REQUIRED_CASES = {
    "dowel_eccentric", "confirmat", "removable_shelf", "fixed_shelf",
    "hinges", "guides", "facade_tie", "back", "legs", "rod", "metal_frame",
}


def test_hardware_connection_goldens_and_cross_format_parity():
    config = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert set(config["cases"]) == REQUIRED_CASES
    report = build_report(config)
    assert report["cloud_calls"] == 0
    assert report["ok"], json.dumps(report, ensure_ascii=False, indent=2)


def test_every_golden_locks_full_drilling_signature_and_bom():
    config = json.loads(GOLDEN.read_text(encoding="utf-8"))
    for name, case in config["cases"].items():
        expected = case["expected"]
        assert expected["count"] > 0, name
        assert len(expected["sha256"]) == 64, name
        assert expected["bom"], name
