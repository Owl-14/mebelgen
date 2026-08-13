"""Mandatory offline engine-check matrix (MEB-149).

Run from ``tools/basis`` with ``python -m qa.engine_checks``. The harness owns
fixture coverage and reporting only; production decisions stay in the gate.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "qa" / "fixtures" / "engine_checks" / "manifest.json"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.production_gate import CheckStep, evaluate_production_gate  # noqa: E402


REQUIRED_ARCHETYPES = frozenset(
    {"corpus", "cabinet", "wardrobe", "drawer_unit", "door_unit", "shelving",
     "desk", "round_table", "composite"}
)
REQUIRED_FEATURES = frozenset(
    {"thin_back", "thick_back", "overlay", "inset", "legs", "door_left",
     "door_right", "door_up", "door_down", "drawer_shallow", "drawer_deep"}
)
DRAWER_DEEP_THRESHOLD_MM = 420.0


@dataclass(frozen=True)
class MatrixCase:
    fixture: str
    path: Path
    archetype: str
    features: tuple[str, ...]


@dataclass(frozen=True)
class MatrixRow:
    archetype: str
    fixture: str
    gate: str
    status: str
    details: tuple[str, ...] = ()


def load_cases(manifest_path: Path = MANIFEST) -> list[MatrixCase]:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    return [MatrixCase(
        fixture=str(item["id"]), path=ROOT / str(item["path"]),
        archetype=str(item["archetype"]),
        features=tuple(str(value) for value in item.get("features") or []),
    ) for item in data.get("fixtures") or []]


def coverage_errors(cases: list[MatrixCase]) -> list[str]:
    archetypes = {case.archetype for case in cases}
    features = {feature for case in cases for feature in case.features}
    errors: list[str] = []
    if REQUIRED_ARCHETYPES - archetypes:
        errors.append(f"нет архетипов: {', '.join(sorted(REQUIRED_ARCHETYPES - archetypes))}")
    if REQUIRED_FEATURES - features:
        errors.append(f"нет вариантов: {', '.join(sorted(REQUIRED_FEATURES - features))}")
    counts = {case.fixture: sum(other.fixture == case.fixture for other in cases) for case in cases}
    duplicates = sorted(fixture for fixture, count in counts.items() if count > 1)
    if duplicates:
        errors.append(f"дубли fixture id: {', '.join(duplicates)}")
    return errors


def derive_features(spec: dict[str, Any], project: dict[str, Any]) -> frozenset[str]:
    """Derive matrix traits from generated production data, never manifest labels."""

    features: set[str] = set()
    panels = [panel for panel in project.get("panels") or [] if isinstance(panel, dict)]
    backs = [panel for panel in panels if str(panel.get("type") or "").lower() == "back"]
    for panel in backs:
        thickness = panel.get("thickness")
        if isinstance(thickness, (int, float)):
            features.add("thin_back" if float(thickness) <= 6.0 else "thick_back")

    facade_types = {"door_front", "drawer_front", "facade", "front_panel", "front"}
    facades = [panel for panel in panels
               if str(panel.get("type") or "").lower() in facade_types]
    for panel in facades:
        placement = panel.get("placement") or {}
        z1 = placement.get("z1")
        if isinstance(z1, (int, float)):
            features.add("overlay" if float(z1) < -0.5 else "inset")
        swing = panel.get("swing")
        if swing in {"left", "right", "up", "down"}:
            features.add(f"door_{swing}")

    legs = (project.get("hardware") or {}).get("legs") or {}
    if isinstance(legs.get("count"), (int, float)) and float(legs["count"]) > 0:
        features.add("legs")

    depths = [drawer.get("dimensions", {}).get("depth")
              for drawer in project.get("drawers") or [] if isinstance(drawer, dict)]
    numeric_depths = [float(depth) for depth in depths if isinstance(depth, (int, float))]
    if numeric_depths:
        features.add("drawer_deep" if max(numeric_depths) > DRAWER_DEEP_THRESHOLD_MM
                     else "drawer_shallow")
    return frozenset(features & REQUIRED_FEATURES)


def _step_row(case: MatrixCase, step: CheckStep) -> MatrixRow:
    return MatrixRow(case.archetype, case.fixture, step.name, step.status,
                     tuple(issue.detail for issue in step.issues))


def run_case(case: MatrixCase) -> list[MatrixRow]:
    try:
        spec: Any = json.loads(case.path.read_text(encoding="utf-8"))
    except Exception as error:
        return [MatrixRow(case.archetype, case.fixture, "fixture", "error", (str(error),))]
    actual = spec.get("archetype") if isinstance(spec, dict) else None
    if actual != case.archetype:
        return [MatrixRow(case.archetype, case.fixture, "fixture_contract", "error",
                          (f"manifest archetype={case.archetype}, ParamSpec archetype={actual}",))]
    decision = evaluate_production_gate(spec)
    rows = [_step_row(case, step) for step in decision.report.checks]
    if decision.project is not None:
        declared = frozenset(case.features)
        derived = derive_features(spec, decision.project)
        if declared != derived:
            details = []
            if declared - derived:
                details.append(f"не подтверждены: {', '.join(sorted(declared - derived))}")
            if derived - declared:
                details.append(f"не задекларированы: {', '.join(sorted(derived - declared))}")
            rows.append(MatrixRow(case.archetype, case.fixture, "feature_contract", "error",
                                  tuple(details)))
    return rows


def run_matrix(cases: list[MatrixCase] | None = None) -> list[MatrixRow]:
    selected = cases if cases is not None else load_cases()
    rows = [MatrixRow("matrix", "manifest", "coverage", "error", (detail,))
            for detail in coverage_errors(selected)]
    for case in selected:
        rows.extend(run_case(case))
    return rows


def format_report(rows: list[MatrixRow]) -> str:
    headers = ("archetype", "fixture", "gate", "status")
    widths = [len(value) for value in headers]
    for row in rows:
        for index, value in enumerate((row.archetype, row.fixture, row.gate, row.status)):
            widths[index] = max(widths[index], len(value))
    lines = [
        f"{headers[0]:<{widths[0]}}  {headers[1]:<{widths[1]}}  "
        f"{headers[2]:<{widths[2]}}  {headers[3]:<{widths[3]}}",
        "-" * (sum(widths) + 6),
    ]
    for row in rows:
        lines.append(f"{row.archetype:<{widths[0]}}  {row.fixture:<{widths[1]}}  "
                     f"{row.gate:<{widths[2]}}  {row.status.upper():<{widths[3]}}")
        lines.extend(f"  ↳ {detail}" for detail in row.details)
    errors = sum(row.status == "error" for row in rows)
    warnings = sum(row.status == "warning" for row in rows)
    fixtures = len({row.fixture for row in rows if row.archetype != "matrix"})
    gates = len({row.gate for row in rows
                 if row.gate not in {"fixture", "fixture_contract", "feature_contract"}})
    lines.append("-" * (sum(widths) + 6))
    lines.append(f"fixtures: {fixtures} | gates: {gates} | errors: {errors} | warnings: {warnings}")
    return "\n".join(lines)


def main() -> int:
    rows = run_matrix()
    print(format_report(rows))
    return 1 if any(row.status == "error" for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
