"""MEB-146: AI candidates pass the entire production contour atomically."""

from __future__ import annotations

import copy
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.production_gate import (  # noqa: E402
    evaluate_production_gate,
    run_operation_repair_cycle,
)

SPEC = json.loads(
    (ROOT / "paramspecs" / "stol_ofisny_foto.json").read_text(encoding="utf-8")
)
CHECK_ORDER = [
    "pydantic",
    "json_schema",
    "generate",
    "consistency",
    "geometry",
    "cfrn_encoding",
    "cfrn_holes_parity",
    "drilling_geometry",
    "completeness_materials",
]


def _floating_panel(spec: dict[str, Any]) -> dict[str, Any]:
    candidate = copy.deepcopy(spec)
    candidate.setdefault("overrides", []).append(
        {
            "action": "add",
            "panel": "AI floating",
            "type": "shelf",
            "placement": {
                "x1": 100,
                "x2": 200,
                "y1": 100,
                "y2": 116,
                "z1": 100,
                "z2": 200,
            },
        }
    )
    return candidate


def test_gate_runs_canonical_order_and_keeps_warnings_structured():
    decision = evaluate_production_gate(SPEC)

    assert decision.report.ok
    assert decision.accepted_spec == SPEC
    assert decision.accepted_spec is not SPEC
    assert [check.name for check in decision.report.checks] == CHECK_ORDER
    report = decision.report.to_dict()
    assert report["ok"] is True
    assert all({"name", "status", "issues"} <= set(check) for check in report["checks"])
    assert report["warnings"]
    assert all(warning["purpose"] and warning["repair_options"]
               for warning in report["warnings"])


def test_red_candidate_is_not_accepted_and_current_revision_is_not_mutated():
    current = copy.deepcopy(SPEC)
    candidate = _floating_panel(current)
    before = copy.deepcopy(current)

    decision = evaluate_production_gate(candidate)

    assert not decision.report.ok
    assert decision.accepted_spec is None
    assert current == before
    error = next(item for item in decision.report.errors if item.code == "model.incomplete")
    assert "AI floating" in error.detail
    assert error.purpose
    assert any(option.kind == "operation" and option.operation for option in error.repair_options)
    assert any(option.kind == "manual_edit" for option in error.repair_options)


@dataclass
class ReducerResult:
    spec: dict[str, Any]


def test_gate_accepts_typed_reducer_result_without_owning_the_reducer():
    decision = evaluate_production_gate(ReducerResult(spec=copy.deepcopy(SPEC)))

    assert decision.report.ok
    assert decision.accepted_spec == SPEC


def test_repair_cycle_is_bounded_atomic_and_uses_only_supplied_reducer():
    current = copy.deepcopy(SPEC)
    calls: list[list[str]] = []

    def reducer(working: dict[str, Any], operations: list[str]) -> dict[str, Any]:
        calls.append(operations)
        if operations == ["add_bad"]:
            return {"spec": _floating_panel(working)}
        if operations == ["remove_bad"]:
            working["overrides"] = [
                item for item in working.get("overrides", [])
                if item.get("panel") != "AI floating"
            ]
            if not working["overrides"]:
                working.pop("overrides")
            return {"spec": working}
        raise AssertionError("unexpected operation")

    decision = run_operation_repair_cycle(
        current,
        [["add_bad"], ["remove_bad"], ["must_not_run"]],
        reducer,
        max_attempts=2,
    )

    assert decision.report.ok
    assert decision.accepted_spec == current
    assert current == SPEC
    assert calls == [["add_bad"], ["remove_bad"]]


def test_repair_cycle_never_publishes_red_intermediate_candidate():
    current = copy.deepcopy(SPEC)

    def reducer(working: dict[str, Any], operations: list[str]) -> dict[str, Any]:
        return {"spec": _floating_panel(working)}

    decision = run_operation_repair_cycle(
        current,
        [["bad"], ["bad"], ["bad"]],
        reducer,
        max_attempts=1,
    )

    assert not decision.report.ok
    assert decision.accepted_spec is None
    assert current == SPEC
