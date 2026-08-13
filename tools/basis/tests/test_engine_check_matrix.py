"""MEB-149: matrix coverage, localization and negative gate contracts."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from qa.engine_checks import (MatrixCase, REQUIRED_ARCHETYPES, REQUIRED_FEATURES,
                              coverage_errors, load_cases, run_case, run_matrix)  # noqa: E402
from src.bounds_check import check_model_bounds  # noqa: E402
from src.production_gate import evaluate_production_gate  # noqa: E402

SPEC = json.loads((ROOT / "paramspecs" / "stol_ofisny_foto.json").read_text(encoding="utf-8"))


def _step(decision, name: str):
    return next(step for step in decision.report.checks if step.name == name)


def test_manifest_covers_required_archetypes_features_and_all_gates():
    cases = load_cases()
    assert not coverage_errors(cases)
    assert REQUIRED_ARCHETYPES <= {case.archetype for case in cases}
    assert REQUIRED_FEATURES <= {feature for case in cases for feature in case.features}
    rows = run_matrix(cases)
    assert rows and not [row for row in rows if row.status == "error"]
    assert all(row.archetype and row.fixture and row.gate for row in rows)
    assert {row.gate for row in rows} >= {
        "pydantic", "json_schema", "consistency", "geometry", "bounds",
        "cfrn_encoding", "cfrn_holes_parity", "drilling_geometry", "system_32",
        "purpose_registry", "completeness", "materials",
    }


def test_manifest_contract_localizes_wrong_archetype(tmp_path: Path):
    path = tmp_path / "wrong.json"
    path.write_text(json.dumps(SPEC), encoding="utf-8")
    rows = run_case(MatrixCase("wrong_archetype", path, "cabinet", ()))
    assert [(row.fixture, row.gate, row.status) for row in rows] == [
        ("wrong_archetype", "fixture_contract", "error")]


def test_manifest_contract_rejects_missing_matrix_variants():
    one = MatrixCase("only", ROOT / "paramspecs" / "stol_ofisny_foto.json", "desk", ())
    errors = coverage_errors([one])
    assert any("нет архетипов" in error for error in errors)
    assert any("нет вариантов" in error for error in errors)


def test_ci_runs_the_single_engine_matrix_command():
    workflow = (ROOT.parent.parent / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "run: python -m qa.engine_checks" in workflow


def test_bounds_gate_checks_structural_front_panel_but_allows_overlay_facade():
    project = {"overall_dimensions": {"width": 100, "depth": 80, "height": 70}, "panels": [
        {"name": "outside", "type": "shelf", "basis_orientation": "horizont",
         "placement": {"x1": 0, "x2": 120, "y1": 10, "y2": 26, "z1": 0, "z2": 80}},
        {"name": "back outside", "type": "back", "basis_orientation": "front",
         "placement": {"x1": 0, "x2": 100, "y1": 0, "y2": 90, "z1": 77, "z2": 80}},
        {"name": "overlay", "type": "door_front", "basis_orientation": "front",
         "placement": {"x1": -5, "x2": 105, "y1": 0, "y2": 70, "z1": -16, "z2": 0}},
    ]}
    assert check_model_bounds(project) == [
        "outside: x=[0,120] вне [0,100]", "back outside: y=[0,90] вне [0,70]"]


def test_gate_rejects_placement_dimensions_mismatch(monkeypatch):
    from src import generators
    original = generators.generate_from_paramspec
    def mismatch(spec):
        project = original(spec)
        project["panels"][0]["dimensions"]["width"] += 10
        return project
    monkeypatch.setattr(generators, "generate_from_paramspec", mismatch)
    decision = evaluate_production_gate(SPEC)
    assert not decision.report.ok
    assert any("dim_width_mismatch" in issue.code for issue in _step(decision, "consistency").issues)


def test_gate_localizes_hole_and_system32_contracts(monkeypatch):
    from src import drilling_check
    monkeypatch.setattr(drilling_check, "check_drilling_geometry", lambda project: {
        "errors": ["test hole: центр вне тела по x"],
        "warnings": ["test row: шаг 48 не кратен 32 (система 32)"],
    })
    decision = evaluate_production_gate(SPEC)
    assert _step(decision, "drilling_geometry").status == "error"
    assert _step(decision, "system_32").status == "warning"


def test_gate_rejects_purpose_missing_from_each_registry(monkeypatch):
    from src import fasteners3d, hardware, webviewer
    for module, name in ((hardware, "registered_fastener_purposes"),
                         (fasteners3d, "registered_fastener_purposes"),
                         (webviewer, "registered_viewer_fastener_purposes")):
        with monkeypatch.context() as patch:
            patch.setattr(module, name, lambda: frozenset())
            decision = evaluate_production_gate(SPEC)
            assert any(issue.code == "drilling.unregistered_purpose"
                       for issue in _step(decision, "purpose_registry").issues)


def test_gate_localizes_cfrn_failures(monkeypatch):
    from src import cfrn
    monkeypatch.setattr(cfrn, "check_cfrn_holes", lambda project: ["hole parity mismatch"])
    monkeypatch.setattr(cfrn, "check_cfrn_encoding", lambda project: ["encoding mismatch"])
    decision = evaluate_production_gate(copy.deepcopy(SPEC))
    assert _step(decision, "cfrn_holes_parity").issues[0].code == "cfrn.holes_parity"
    assert _step(decision, "cfrn_encoding").issues[0].code == "cfrn.encoding"
