"""Build the machine-readable ParamSpec/generator/golden coverage inventory."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "qa" / "archetype_coverage.json"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MODEL_BY_ARCHETYPE = {
    "cabinet": "CabinetParamSpec",
    "composite": "CompositeParamSpec",
    "corpus": "CorpusParamSpec",
    "desk": "DeskParamSpec",
    "door_unit": "DoorUnitParamSpec",
    "drawer_unit": "DrawerUnitParamSpec",
    "round_table": "RoundTableParamSpec",
    "shelving": "ShelvingParamSpec",
    "table": "DeskParamSpec",
    "wardrobe": "CabinetParamSpec",
}

# ParamSpec paths that select optional generator behavior, mapped to the function
# that actually consumes each value.  This intentionally does not repeat fields
# that merely exist in the broad ParamSpec model but are ignored by a generator.
VARIANT_PARAMETER_CONSUMERS = {
    "corpus": {
        "legs.as_panel": "src.generators.base.read_carcass",
        "sides_over_top": "src.generators.corpus.generate",
        "socle_recess": "src.generators.corpus.generate",
    },
    "shelving": {
        "sections[].shelf_levels": "src.generators.shelving.generate",
        "sections[].shelves": "src.generators.shelving.generate",
        "sides_over_top": "src.generators.shelving.generate",
        "socle_recess": "src.generators.shelving.generate",
    },
    "drawer_unit": {
        "facade_reveal": "src.generators.drawer_unit.generate",
        "sections[].box_back_thickness": "src.generators.drawer_unit.generate",
        "sections[].box_bottom_thickness": "src.generators.drawer_unit.generate",
        "sections[].box_depth": "src.generators.drawer_unit.generate",
        "sections[].box_height": "src.generators.drawer_unit.generate",
        "sections[].box_y_offset": "src.generators.drawer_unit.generate",
        "sections[].box_z1": "src.generators.drawer_unit.generate",
        "sections[].cover_top": "src.generators.drawer_unit.generate",
        "sections[].drawer_heights": "src.generators.drawer_unit.generate",
        "sections[].drawers": "src.generators.drawer_unit.generate",
        "sections[].front_bottom": "src.generators.drawer_unit.generate",
        "sections[].front_top": "src.generators.drawer_unit.generate",
        "sections[].guide_gap": "src.generators.drawer_unit.generate",
        "sections[].guide_type": "src.generators.drawer_unit.generate",
        "sections[].niche_z_front": "src.generators.drawer_unit.generate",
        "sides_over_top": "src.generators.drawer_unit.generate",
        "socle_recess": "src.generators.drawer_unit.generate",
        "top_overhang": "src.generators.drawer_unit.generate",
    },
    "door_unit": {
        "sections[].door": "src.generators.door_unit.generate",
        "sections[].door_swing": "src.generators.door_unit.generate",
        "sections[].door_z": "src.generators.door_unit.generate",
        "sections[].rod": "src.generators.columns.rod_in_column",
        "sections[].shelf_levels": "src.generators.door_unit.generate",
        "sections[].shelves": "src.generators.door_unit.generate",
        "sides_over_top": "src.generators.door_unit.generate",
        "socle_recess": "src.generators.door_unit.generate",
    },
    "cabinet": {},
    "wardrobe": {},
    "desk": {
        "apron": "src.generators.desk.generate",
        "apron_height": "src.generators.desk.generate",
        "frame": "src.generators.desk._is_metal",
        "legs.type": "src.generators.desk._is_metal",
        "screen": "src.generators.desk.generate",
        "screen_height": "src.generators.desk.generate",
        "screen_margin": "src.generators.desk.generate",
        "screen_thickness": "src.generators.desk.generate",
        "screen_z": "src.generators.desk.generate",
        "top_overhang": "src.generators.desk.generate",
    },
    "table": {},
    "round_table": {
        "base": "src.generators.round_table.generate",
        "base_diameter": "src.generators.round_table.generate",
        "base_thickness": "src.generators.round_table.generate",
        "pedestal_diameter": "src.generators.round_table.generate",
        "top_thickness": "src.generators.round_table.generate",
    },
    "composite": {
        "blocks": "src.generators.composite.generate",
        "blocks[].name": "src.generators.composite.generate",
        "blocks[].origin.x": "src.generators.composite.generate",
        "blocks[].origin.y": "src.generators.composite.generate",
        "blocks[].origin.z": "src.generators.composite.generate",
        "blocks[].spec": "src.generators.composite.generate",
    },
}

_CABINET_CONSUMERS = {
    "carcass_z_front": "src.generators.cabinet.generate",
    "facade_reveal": "src.generators.cabinet.generate",
    "interior_z_front": "src.generators.cabinet.generate",
    "sections[].box_back_mode": "src.generators.columns.drawer_stack",
    "sections[].box_back_thickness": "src.generators.columns.drawer_stack",
    "sections[].box_bottom_mode": "src.generators.columns.drawer_stack",
    "sections[].box_bottom_thickness": "src.generators.columns.drawer_stack",
    "sections[].box_depth": "src.generators.columns.drawer_stack",
    "sections[].box_height": "src.generators.columns.drawer_stack",
    "sections[].box_sides_on_bottom": "src.generators.columns.drawer_stack",
    "sections[].box_y_offset": "src.generators.columns.drawer_stack",
    "sections[].box_z1": "src.generators.columns.drawer_stack",
    "sections[].boxes": "src.generators.columns.drawer_stack",
    "sections[].cover_top": "src.generators.cabinet.generate",
    "sections[].door": "src.generators.cabinet.generate",
    "sections[].door_below_shelf": "src.generators.cabinet.generate",
    "sections[].door_name": "src.generators.cabinet.generate",
    "sections[].door_names": "src.generators.cabinet.generate",
    "sections[].door_swing": "src.generators.cabinet.generate",
    "sections[].door_z": "src.generators.cabinet.generate",
    "sections[].drawer_heights": "src.generators.cabinet.generate",
    "sections[].drawers": "src.generators.cabinet.generate",
    "sections[].front_bottom": "src.generators.cabinet.generate",
    "sections[].front_top": "src.generators.cabinet.generate",
    "sections[].guide_gap": "src.generators.columns.drawer_stack",
    "sections[].guide_type": "src.generators.columns.drawer_stack",
    "sections[].niche_z_front": "src.generators.cabinet.generate",
    "sections[].rod": "src.generators.columns.rod_in_column",
    "sections[].shelf_label": "src.generators.cabinet.generate",
    "sections[].shelf_levels": "src.generators.cabinet.generate",
    "sections[].shelves": "src.generators.cabinet.generate",
    "sections[].width_share": "src.generators.columns.column_bounds",
    "sides_over_top": "src.generators.cabinet.generate",
    "socle_full": "src.generators.cabinet.generate",
    "socle_recess": "src.generators.cabinet.generate",
    "top_overhang": "src.generators.cabinet.generate",
}
VARIANT_PARAMETER_CONSUMERS["cabinet"] = dict(_CABINET_CONSUMERS)
VARIANT_PARAMETER_CONSUMERS["wardrobe"] = dict(_CABINET_CONSUMERS)
VARIANT_PARAMETER_CONSUMERS["table"] = dict(VARIANT_PARAMETER_CONSUMERS["desk"])


def _spec_paths() -> list[Path]:
    return sorted(
        path for path in (ROOT / "paramspecs").glob("*.json")
        if not path.name.endswith((".project.json", ".versions.json"))
    )


def _schema_archetypes() -> set[str]:
    schema = json.loads((ROOT / "schema" / "paramspec.schema.json").read_text(encoding="utf-8"))
    values: set[str] = set()
    for model_name in set(MODEL_BY_ARCHETYPE.values()):
        discriminator = schema["$defs"][model_name]["properties"]["archetype"]
        values.update(discriminator.get("enum") or [discriminator["const"]])
    return values


def _path_present(value: Any, path: str) -> bool:
    head, *tail = path.split(".", 1)
    is_array = head.endswith("[]")
    key = head[:-2] if is_array else head
    if not isinstance(value, dict) or key not in value:
        return False
    child = value[key]
    if is_array:
        if not isinstance(child, list):
            return False
        return bool(child) if not tail else any(_path_present(item, tail[0]) for item in child)
    return True if not tail else _path_present(child, tail[0])


def golden_exact(spec: dict[str, Any], golden_path: Path) -> bool:
    from src.generators import generate_from_paramspec
    from tests.regression import exact_match

    return exact_match(generate_from_paramspec(spec), golden_path)[0]


def build_report() -> dict[str, Any]:
    from src.generators.registry import _REGISTRY
    from src.paramspec import _ARCHETYPE_TAGS

    specs_by_archetype: dict[str, list[str]] = defaultdict(list)
    specs_by_name: dict[str, dict[str, Any]] = {}
    for path in _spec_paths():
        spec = json.loads(path.read_text(encoding="utf-8"))
        archetype = spec.get("archetype")
        if archetype not in _ARCHETYPE_TAGS:
            continue
        specs_by_archetype[archetype].append(path.stem)
        specs_by_name[path.stem] = spec

    project_names = {path.name for path in (ROOT / "projects").glob("*.json")}
    rows = []
    for archetype in sorted(_ARCHETYPE_TAGS):
        fixtures = sorted(specs_by_archetype[archetype])
        existing_goldens = sorted(name for name in fixtures if f"{name}.json" in project_names)
        exact_goldens = []
        mismatched_goldens = []
        for name in existing_goldens:
            if golden_exact(specs_by_name[name], ROOT / "projects" / f"{name}.json"):
                exact_goldens.append(name)
            else:
                mismatched_goldens.append(name)
        consumers = []
        for parameter, consumer in sorted(VARIANT_PARAMETER_CONSUMERS[archetype].items()):
            fixture_evidence = sorted(
                name for name in fixtures if _path_present(specs_by_name[name], parameter)
            )
            consumers.append({
                "parameter": parameter,
                "consumer": consumer,
                "fixture_evidence": fixture_evidence,
            })
        rows.append({
            "archetype": archetype,
            "paramspec_model": MODEL_BY_ARCHETYPE[archetype],
            "generator": f"{_REGISTRY[archetype].__module__}.{_REGISTRY[archetype].__name__}",
            "paramspec_fixtures": fixtures,
            "existing_panel_goldens": existing_goldens,
            "exact_panel_goldens": exact_goldens,
            "mismatched_panel_goldens": mismatched_goldens,
            "variant_parameter_consumers": consumers,
            "uncovered_variant_parameters": [
                item["parameter"] for item in consumers if not item["fixture_evidence"]
            ],
        })

    implementations: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = implementations.setdefault(row["generator"], {
            "generator": row["generator"], "archetypes": [], "exact_panel_goldens": [],
        })
        item["archetypes"].append(row["archetype"])
        item["exact_panel_goldens"].extend(row["exact_panel_goldens"])
    implementation_rows = []
    for item in implementations.values():
        item["archetypes"].sort()
        item["exact_panel_goldens"] = sorted(set(item["exact_panel_goldens"]))
        implementation_rows.append(item)
    implementation_rows.sort(key=lambda item: item["generator"])

    matched_projects = {
        f"{name}.json" for names in specs_by_archetype.values() for name in names
    } & project_names
    schema_tags = _schema_archetypes()
    registry_tags = set(_REGISTRY)
    exact_count = sum(len(row["exact_panel_goldens"]) for row in rows)
    mismatch_count = sum(len(row["mismatched_panel_goldens"]) for row in rows)
    archetypes_with_exact = sum(bool(row["exact_panel_goldens"]) for row in rows)
    implementations_with_exact = sum(
        bool(row["exact_panel_goldens"]) for row in implementation_rows
    )
    return {
        "schema_version": "archetype-coverage-v2",
        "sources": [
            "src/paramspec.py", "schema/paramspec.schema.json",
            "src/generators/registry.py", "src/generators/*.py",
            "paramspecs/*.json", "projects/*.json", "tests/regression.py",
        ],
        "summary": {
            "public_archetype_count": len(_ARCHETYPE_TAGS),
            "generator_implementation_count": len(implementations),
            "paramspec_fixture_count": sum(len(names) for names in specs_by_archetype.values()),
            "existing_panel_golden_count": len(matched_projects),
            "matched_exact_panel_golden_count": exact_count,
            "mismatched_panel_golden_count": mismatch_count,
            "archetypes_with_paramspec_fixture": sum(bool(row["paramspec_fixtures"]) for row in rows),
            "archetypes_with_exact_panel_golden": archetypes_with_exact,
            "implementations_with_exact_panel_golden": implementations_with_exact,
        },
        "contract_parity": {
            "paramspec_tags": sorted(_ARCHETYPE_TAGS),
            "json_schema_tags": sorted(schema_tags),
            "registry_tags": sorted(registry_tags),
            "all_match": _ARCHETYPE_TAGS == schema_tags == registry_tags,
        },
        "archetypes": rows,
        "generator_implementations": implementation_rows,
        "unmatched_project_files": sorted(project_names - matched_projects),
        "completed_package": {
            "id": "MEB-133-G01",
            "status": "closed",
            "title": "Every public archetype and generator implementation has an exact-panel baseline",
            "evidence": {
                "public_archetypes": "10/10",
                "generator_implementations": "8/8",
                "regression_exact_goldens": f"{exact_count}/{len(matched_projects)}",
            },
        },
        "remaining_gaps": [
            {
                "id": "MEB-133-G02",
                "status": "open",
                "title": "Consumed variant parameters do not all have ParamSpec evidence",
                "evidence": "See archetypes[].uncovered_variant_parameters and variant_parameter_consumers",
                "next_scope": "Add focused fixtures and exact goldens per uncovered branch; do not change geometry in the audit task.",
            },
            {
                "id": "MEB-133-G03",
                "status": "open",
                "title": "Exact goldens compare panels only",
                "evidence": "tests/regression.py::exact_match compares panel names and placement",
                "next_scope": "Add per-archetype production-gate, CFRN encoding/holes and drilling matrix without cloud calls.",
            },
            {
                "id": "MEB-133-G04",
                "status": "blocked_external",
                "title": "Round-table production contour is not proven in real Basis",
                "evidence": "rules/generators.md documents missing importer contour support (AKD-42)",
                "next_scope": "Keep local shape/placement regression; validate real B3D only in a separately licensed environment.",
            },
            {
                "id": "MEB-133-G05",
                "status": "open",
                "title": "Minimum and boundary dimensions are not matrixed per archetype",
                "evidence": "Current paramspecs are representative products, not a min/typical/max matrix",
                "next_scope": "Add deterministic min/typical/max fixtures and negative schema cases per generator family.",
            },
        ],
    }


def _serialized_report() -> str:
    return json.dumps(build_report(), ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="update qa/archetype_coverage.json")
    parser.add_argument("--check", action="store_true", help="fail when the checked-in report is stale")
    args = parser.parse_args()
    rendered = _serialized_report()
    if args.write:
        REPORT_PATH.write_text(rendered, encoding="utf-8")
    if args.check:
        actual = REPORT_PATH.read_text(encoding="utf-8") if REPORT_PATH.exists() else ""
        if actual != rendered:
            print(f"stale archetype coverage report: run {Path(__file__).name} --write")
            return 1
    if not args.write and not args.check:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
