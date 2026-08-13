from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from src.basis_import_preflight import build_preflight_report, validate_evidence_taxonomy


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "projects" / "cabinet_700x400x500.json"


def _project() -> dict:
    return json.loads(PROJECT.read_text(encoding="utf-8"))


def test_source_preflight_records_expectations_without_claiming_import_result() -> None:
    report = build_preflight_report(_project(), source="projects/cabinet_700x400x500.json")
    expected = report["source_expectations"]

    assert report["source_preflight_status"] == "pass", report["errors"]
    assert report["evidence_taxonomy"]["kind"] == "source_preflight_expectations"
    assert report["evidence_taxonomy"]["native_basis_observed"] is False
    assert expected["dimensions_and_geometry"]["panel_count"] == 12
    assert expected["dimensions_and_geometry"]["orientation_counts"] == {
        "front": 2,
        "horizont": 8,
        "vertical": 2,
    }
    assert len(expected["panel_calls"]) == 12
    assert expected["materials_input"]["panel_material_counts"] == {"ЛДСП": 12}
    assert expected["edge_banding_input"] == {
        "source_assignments_by_thickness_mm": {"0.4": 48},
        "missing_in_source": [],
        "importer_api_calls_observed": False,
    }
    assert expected["drilling_computed_by_python"]["count"] == 154
    assert expected["drilling_computed_by_python"]["geometry_errors"] == []
    assert expected["drilling_computed_by_python"]["importer_api_calls_observed"] is False
    assert expected["cfrn_computed_by_python"]["encoding_errors"] == []
    assert expected["cfrn_computed_by_python"]["hole_parity_errors"] == []
    assert len(expected["drilling_computed_by_python"]["fingerprint_sha256"]) == 64
    assert report["licensed_basis"]["status"] == "blocked_not_run"
    assert set(report["imported_result"].values()) == {"blocked_not_observed"}
    assert validate_evidence_taxonomy(report) == []
    assert "edge_banding" not in report and "drilling" not in report


def test_committed_offline_report_is_reproducible() -> None:
    actual = build_preflight_report(_project(), source="projects/cabinet_700x400x500.json")
    expected = json.loads(
        (ROOT / "qa" / "basis_import" / "offline-preflight-report.json").read_text(encoding="utf-8")
    )
    assert actual == expected


def test_preflight_rejects_orientation_dimension_drift() -> None:
    project = _project()
    project["panels"][0]["placement"]["x2"] += 10

    report = build_preflight_report(project)

    assert report["source_preflight_status"] == "fail"
    assert any("placement.width" in error or "габарит" in error for error in report["errors"])


def test_exact_importer_runs_in_offline_node_harness(tmp_path: Path) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is optional for Python-only environments")
    output = tmp_path / "harness.json"
    subprocess.run(
        [
            node,
            str(ROOT / "scripts" / "run_basis_import_harness.mjs"),
            "projects/cabinet_700x400x500.json",
            str(output),
        ],
        check=True,
        cwd=ROOT,
    )
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["mock_execution_status"] == "pass", report["errors"]
    assert report["acceptance_status"] == "blocked_missing_licensed_basis"
    calls = report["observed_importer_calls"]
    assert calls["panels"]["call_count"] > 0
    assert calls["materials"]["call_count"] == 12
    assert calls["edge_banding"] == {"status": "not_observed", "call_count": 0, "call_sequences": []}
    assert calls["drilling"] == {"status": "not_observed", "call_count": 0, "call_sequences": []}
    assert calls["native_b3d"] == {"status": "not_observed", "call_count": 0, "call_sequences": []}
    assert report["mock_panel_comparison"]["mock_built_panel_count"] == 12
    assert {panel["orientation"] for panel in report["mock_panel_comparison"]["panels"]} == {
        "front",
        "horizont",
        "vertical",
    }
    assert any(call["api"] == "objects3d.NewPanel" for call in report["api_call_log"])
    assert any(call["api"] == "panel.MaterialName.set" for call in report["api_call_log"])
    assert set(report["imported_result"].values()) == {"blocked_not_observed"}
    assert validate_evidence_taxonomy(report) == []
    expected = json.loads(
        (ROOT / "qa" / "basis_import" / "importer-harness-report.json").read_text(encoding="utf-8")
    )
    assert report == expected


@pytest.mark.parametrize("domain", ["edge_banding", "drilling"])
def test_harness_fails_when_an_absent_api_call_is_required(tmp_path: Path, domain: str) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is optional for Python-only environments")
    output = tmp_path / f"missing-{domain}-call.json"
    completed = subprocess.run(
        [
            node,
            str(ROOT / "scripts" / "run_basis_import_harness.mjs"),
            "projects/cabinet_700x400x500.json",
            str(output),
            "--require-call-domain",
            domain,
        ],
        check=False,
        cwd=ROOT,
    )
    report = json.loads(output.read_text(encoding="utf-8"))
    assert completed.returncode == 1
    assert report["mock_execution_status"] == "fail"
    assert report["observed_importer_calls"][domain]["call_count"] == 0
    assert f"required importer API call domain was not observed: {domain}" in report["errors"]


@pytest.mark.parametrize("artifact", ["offline-preflight-report.json", "importer-harness-report.json"])
@pytest.mark.parametrize("domain", ["edge_banding", "drilling"])
def test_taxonomy_rejects_misleading_imported_result(artifact: str, domain: str) -> None:
    report = json.loads((ROOT / "qa" / "basis_import" / artifact).read_text(encoding="utf-8"))
    report["imported_result"][domain] = {"status": "pass", "count": 48}

    errors = validate_evidence_taxonomy(report)

    assert f"imported_result.{domain} is a misleading offline claim" in errors


def test_basis_api_detector_is_read_only_and_reports_importer_capabilities() -> None:
    source = (ROOT / "scripts" / "detect_basis_api.js").read_text(encoding="utf-8")
    assert "read_only: true" in source
    assert "importer_core_ready" in source
    assert "NewFurnitureValue" in source
    assert "not_observed_by_detector" in source
    assert "blocked_not_observed" in source
    assert "api-cutting" not in source.lower()
    assert "api-cloud" not in source.lower()
