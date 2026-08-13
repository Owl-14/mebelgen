from __future__ import annotations

import json

from scripts.audit_archetype_coverage import REPORT_PATH, build_report


def test_public_archetypes_have_fixture_and_exact_panel_golden() -> None:
    report = build_report()
    summary = report["summary"]
    assert report["contract_parity"]["all_match"] is True
    assert summary["public_archetype_count"] == 10
    assert summary["archetypes_with_paramspec_fixture"] == 10
    assert summary["archetypes_with_exact_panel_golden"] == 10
    assert summary["implementations_with_exact_panel_golden"] == 8


def test_coverage_inventory_has_explicit_remaining_gap_decomposition() -> None:
    report = build_report()
    assert all(row["supported_variants"] for row in report["archetypes"])
    assert all("uncovered_variants" in row for row in report["archetypes"])
    assert {gap["status"] for gap in report["remaining_gaps"]} == {
        "open", "blocked_external",
    }


def test_checked_in_machine_readable_report_is_current() -> None:
    assert json.loads(REPORT_PATH.read_text(encoding="utf-8")) == build_report()
