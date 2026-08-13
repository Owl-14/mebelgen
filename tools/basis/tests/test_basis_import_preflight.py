from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from src.basis_import_preflight import build_preflight_report


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "projects" / "cabinet_700x400x500.json"


def _project() -> dict:
    return json.loads(PROJECT.read_text(encoding="utf-8"))


def test_offline_preflight_covers_import_acceptance_dimensions_and_drilling() -> None:
    report = build_preflight_report(_project(), source="projects/cabinet_700x400x500.json")

    assert report["offline_status"] == "pass", report["errors"]
    assert report["dimensions"]["panel_count"] == 12
    assert report["dimensions"]["orientation_counts"] == {"front": 2, "horizont": 8, "vertical": 2}
    assert len(report["importer_projection"]) == 12
    assert report["materials"]["panel_material_counts"] == {"ЛДСП": 12}
    assert report["edge_banding"] == {
        "assignments_by_thickness_mm": {"0.4": 48},
        "missing": [],
    }
    assert report["drilling"]["count"] == 154
    assert report["drilling"]["geometry_errors"] == []
    assert report["drilling"]["cfrn_encoding_errors"] == []
    assert report["drilling"]["cfrn_hole_parity_errors"] == []
    assert len(report["drilling"]["fingerprint_sha256"]) == 64
    assert report["licensed_basis"]["status"] == "blocked_not_run"


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

    assert report["offline_status"] == "fail"
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
    assert report["status"] == "pass", report["errors"]
    assert report["panel_count"] == 12
    assert {panel["orientation"] for panel in report["panels"]} == {"front", "horizont", "vertical"}
    expected = json.loads(
        (ROOT / "qa" / "basis_import" / "importer-harness-report.json").read_text(encoding="utf-8")
    )
    assert report == expected


def test_basis_api_detector_is_read_only_and_reports_importer_capabilities() -> None:
    source = (ROOT / "scripts" / "detect_basis_api.js").read_text(encoding="utf-8")
    assert "read_only: true" in source
    assert "importer_core_ready" in source
    assert "NewFurnitureValue" in source
    assert "api-cutting" not in source.lower()
    assert "api-cloud" not in source.lower()
