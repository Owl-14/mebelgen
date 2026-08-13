from __future__ import annotations

import json
import copy
import importlib
from pathlib import Path

from src.generators import generate_from_paramspec
from src.generators.base import Carcass, read_carcass
from scripts.audit_archetype_coverage import (
    DIRECT_PARAMETER_CONSUMERS,
    REPORT_PATH,
    READ_CARCASS_ARCHETYPES,
    READ_CARCASS_CONSUMER,
    READ_CARCASS_PARAMETER_REGISTRY,
    ROOT,
    VARIANT_PARAMETER_CONSUMERS,
    build_report,
    golden_exact,
)


PROBE_FIXTURES = {
    "cabinet": "kashpo.json",
    "corpus": "tz_stol_kofeyny_cube.json",
    "desk": "desk_demo.json",
    "door_unit": "cabinet_700x400x500.json",
    "drawer_unit": "tz_tumba_dokumenty.json",
    "round_table": "tz_stol_kofeyny_round.json",
    "shelving": "shelf_800x300x1800.json",
    "table": "tz_zhurnalny_stol.json",
    "wardrobe": "wardrobe_demo.json",
}


class _ReadRecordingDict(dict):
    def __init__(self, value: dict, path: str = "", reads: set[str] | None = None):
        self.reads = reads if reads is not None else set()
        self.path = path
        super().__init__({
            key: _ReadRecordingDict(
                child, self._child_path(key), self.reads
            ) if isinstance(child, dict) else child
            for key, child in value.items()
        })

    def _child_path(self, key: str) -> str:
        return f"{self.path}.{key}" if self.path else key

    def __getitem__(self, key):
        self.reads.add(self._child_path(key))
        return super().__getitem__(key)

    def get(self, key, default=None):
        self.reads.add(self._child_path(key))
        return super().get(key, default)


def _set_path(value: dict, path: str, replacement) -> None:
    target = value
    keys = path.split(".")
    for key in keys[:-1]:
        target = target.setdefault(key, {})
    target[keys[-1]] = replacement


def _delete_path(value: dict, path: str) -> None:
    target = value
    keys = path.split(".")
    for key in keys[:-1]:
        child = target.get(key)
        if not isinstance(child, dict):
            return
        target = child
    target.pop(keys[-1], None)


def test_public_archetypes_have_fixture_and_exact_panel_golden() -> None:
    report = build_report()
    summary = report["summary"]
    assert report["schema_version"] == "archetype-coverage-v3"
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
        "src.generators.columns.rod_in_column",
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
        assert shared_drawer[parameter] == ("src.generators.columns.drawer_stack",)

    for consumers in VARIANT_PARAMETER_CONSUMERS.values():
        for dotted_consumers in consumers.values():
            for dotted_consumer in dotted_consumers:
                module_name, function_name = dotted_consumer.rsplit(".", 1)
                assert callable(getattr(importlib.import_module(module_name), function_name))


def test_reverse_registry_exhausts_carcass_fields_and_every_archetype_alias() -> None:
    assert set(Carcass._fields) == {
        item["carcass_field"] for item in READ_CARCASS_PARAMETER_REGISTRY.values()
    }
    assert set(PROBE_FIXTURES) == set(READ_CARCASS_ARCHETYPES)
    assert set(READ_CARCASS_PARAMETER_REGISTRY["legs.as_panel"]["archetypes"]) == {
        "cabinet", "corpus", "door_unit", "drawer_unit", "shelving", "wardrobe",
    }

    for archetype, direct_consumers in DIRECT_PARAMETER_CONSUMERS.items():
        expected = {
            parameter: ((consumer,) if isinstance(consumer, str) else tuple(consumer))
            for parameter, consumer in direct_consumers.items()
        }
        for parameter, descriptor in READ_CARCASS_PARAMETER_REGISTRY.items():
            if archetype in descriptor["archetypes"]:
                expected[parameter] = tuple(dict.fromkeys(
                    (*expected.get(parameter, ()), READ_CARCASS_CONSUMER)
                ))
        assert VARIANT_PARAMETER_CONSUMERS[archetype] == expected


def test_reverse_registry_exhausts_executed_read_carcass_parameter_reads() -> None:
    tracked = _ReadRecordingDict({
        "dimensions": {
            "width": 700, "depth": 400, "depth_carcass": 390, "height": 500,
        },
        "materials": {
            "board_thickness": 16, "back_thickness": 4,
            "board_material": "BOARD", "back_material": "BACK",
            "top_thickness": 22,
        },
        "legs": {"height": 80, "as_panel": True, "type": "PLINTH"},
        "gaps": {"default": 2, "facade": 3},
    })
    read_carcass(tracked)
    leaf_reads = {path for path in tracked.reads if "." in path}
    assert leaf_reads == set(READ_CARCASS_PARAMETER_REGISTRY)


def test_every_shared_carcass_binding_has_an_executable_probe() -> None:
    for archetype, fixture_name in PROBE_FIXTURES.items():
        original = json.loads(
            (ROOT / "paramspecs" / fixture_name).read_text(encoding="utf-8")
        )
        for parameter, descriptor in READ_CARCASS_PARAMETER_REGISTRY.items():
            if archetype not in descriptor["archetypes"]:
                continue
            baseline = copy.deepcopy(original)
            for setup_path, setup_value in descriptor.get("setup", {}).items():
                _set_path(baseline, setup_path, setup_value)
            for shadowing_path in descriptor.get("clear", ()):
                _delete_path(baseline, shadowing_path)
            before = read_carcass(baseline)
            candidate = copy.deepcopy(baseline)
            probe = descriptor["probe"]
            if probe == getattr(before, descriptor["carcass_field"]):
                if isinstance(probe, bool):
                    probe = not probe
                elif isinstance(probe, (int, float)):
                    probe += 1
                else:
                    probe += "_ALT"
            _set_path(candidate, parameter, probe)
            after = read_carcass(candidate)
            field = descriptor["carcass_field"]
            assert getattr(after, field) != getattr(before, field), (
                archetype, parameter, field,
            )


def test_every_registered_binding_changes_production_generator_output() -> None:
    for archetype, fixture_name in PROBE_FIXTURES.items():
        original = json.loads(
            (ROOT / "paramspecs" / fixture_name).read_text(encoding="utf-8")
        )
        for parameter, descriptor in READ_CARCASS_PARAMETER_REGISTRY.items():
            if archetype not in descriptor["archetypes"]:
                continue
            baseline = copy.deepcopy(original)
            for setup_path, setup_value in descriptor.get("setup", {}).items():
                _set_path(baseline, setup_path, setup_value)
            for shadowing_path in descriptor.get("clear", ()):
                _delete_path(baseline, shadowing_path)
            candidate = copy.deepcopy(baseline)
            _set_path(candidate, parameter, descriptor["probe"])

            assert generate_from_paramspec(candidate) != generate_from_paramspec(baseline), (
                archetype, parameter,
            )


def test_door_unit_shared_parameters_change_generated_geometry() -> None:
    spec = json.loads(
        (ROOT / "paramspecs" / "cabinet_700x400x500.json").read_text(encoding="utf-8")
    )
    spec["legs"] = {"type": "цоколь", "height": 80, "as_panel": False}
    without_plinth = generate_from_paramspec(copy.deepcopy(spec))
    assert not [panel for panel in without_plinth["panels"] if panel["type"] == "plinth"]

    spec["legs"]["as_panel"] = True
    with_plinth = generate_from_paramspec(copy.deepcopy(spec))
    plinths = [panel for panel in with_plinth["panels"] if panel["type"] == "plinth"]
    assert len(plinths) == 1 and plinths[0]["name"] == "Цоколь"

    default_top = next(panel for panel in with_plinth["panels"] if panel["type"] == "top")
    spec["materials"]["top_thickness"] = 37
    thick_top_project = generate_from_paramspec(spec)
    thick_top = next(panel for panel in thick_top_project["panels"] if panel["type"] == "top")
    assert thick_top["thickness"] == 37
    assert thick_top["placement"]["y1"] == spec["dimensions"]["height"] - 37
    assert thick_top["placement"] != default_top["placement"]


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
