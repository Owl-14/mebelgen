from __future__ import annotations

import json
import importlib
import inspect
from pathlib import Path

from src.generators import generate_from_paramspec
from scripts.audit_archetype_coverage import (
    REPORT_PATH,
    ROOT,
    VARIANT_PARAMETER_CONSUMERS,
    build_report,
    golden_exact,
)


def test_public_archetypes_have_fixture_and_exact_panel_golden() -> None:
    report = build_report()
    summary = report["summary"]
    assert report["schema_version"] == "archetype-coverage-v2"
    assert report["contract_parity"]["all_match"] is True
    assert summary["public_archetype_count"] == 10
    assert summary["archetypes_with_paramspec_fixture"] == 10
    assert summary["archetypes_with_exact_panel_golden"] == 10
    assert summary["implementations_with_exact_panel_golden"] == 8
    assert summary["mismatched_panel_golden_count"] == 0


def test_consumer_map_matches_generator_ownership_and_source() -> None:
    report = build_report()
    assert all(row["variant_parameter_consumers"] for row in report["archetypes"])

    assert VARIANT_PARAMETER_CONSUMERS["door_unit"]["sections[].rod"] == (
        "src.generators.columns.rod_in_column"
    )
    for archetype in ("corpus", "shelving", "drawer_unit", "door_unit"):
        assert "sides_over_top" in VARIANT_PARAMETER_CONSUMERS[archetype]

    standalone_drawer = VARIANT_PARAMETER_CONSUMERS["drawer_unit"]
    shared_drawer = VARIANT_PARAMETER_CONSUMERS["cabinet"]
    for parameter in (
        "sections[].boxes", "sections[].box_bottom_mode",
        "sections[].box_back_mode", "sections[].box_sides_on_bottom",
    ):
        assert parameter not in standalone_drawer
        assert shared_drawer[parameter] == "src.generators.columns.drawer_stack"

    for consumers in VARIANT_PARAMETER_CONSUMERS.values():
        for parameter, dotted_consumer in consumers.items():
            module_name, function_name = dotted_consumer.rsplit(".", 1)
            function = getattr(importlib.import_module(module_name), function_name)
            source = inspect.getsource(function)
            field = parameter.rsplit(".", 1)[-1].removesuffix("[]")
            assert f'"{field}"' in source or f"'{field}'" in source, (
                parameter, dotted_consumer
            )


def test_coverage_inventory_has_explicit_remaining_gap_decomposition() -> None:
    report = build_report()
    assert all("uncovered_variant_parameters" in row for row in report["archetypes"])
    assert {gap["status"] for gap in report["remaining_gaps"]} == {
        "open", "blocked_external",
    }


def test_audit_does_not_count_corrupted_golden_as_exact(tmp_path: Path) -> None:
    spec = json.loads(
        (ROOT / "paramspecs" / "tz_stol_kofeyny_cube.json").read_text(encoding="utf-8")
    )
    project = generate_from_paramspec(spec)
    golden_path = tmp_path / "golden.json"
    golden_path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    assert golden_exact(spec, golden_path) is True

    project["panels"][0]["placement"]["x1"] += 1_000
    golden_path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    assert golden_exact(spec, golden_path) is False


def test_checked_in_machine_readable_report_is_current() -> None:
    assert json.loads(REPORT_PATH.read_text(encoding="utf-8")) == build_report()
