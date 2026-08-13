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

# Branches advertised by rules/generators.md or implemented directly in generators/*.py.
# The report subtracts observed fixture evidence from this inventory.
SUPPORTED_VARIANTS = {
    "corpus": ["plain_carcass", "sides_over_top", "socle_panel"],
    "shelving": ["explicit_shelf_levels", "shelf_count"],
    "drawer_unit": [
        "boxes_disabled", "custom_box_geometry", "drawer_count",
        "explicit_drawer_heights", "front_top_niche",
    ],
    "door_unit": [
        "double_door", "explicit_door_swing", "explicit_shelf_levels",
        "inset_door", "shelf_count", "single_door",
    ],
    "cabinet": [
        "door_section", "drawers_section", "mixed_sections", "multi_column",
        "open_section", "rods", "shelves_section", "width_share",
    ],
    "wardrobe": [
        "door_section", "drawers_section", "mixed_sections", "multi_column",
        "open_section", "rods", "shelves_section", "width_share",
    ],
    "desk": [
        "apron_disabled", "apron_enabled", "metal_frame", "modesty_screen",
        "panel_supports", "top_overhang",
    ],
    "table": [
        "apron_disabled", "apron_enabled", "metal_frame", "modesty_screen",
        "panel_supports", "top_overhang",
    ],
    "round_table": ["custom_diameters", "no_base", "pedestal_base"],
    "composite": [
        "horizontal_offsets", "mixed_block_archetypes", "single_level_blocks",
        "vertical_offsets",
    ],
}


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


def _variants(spec: dict[str, Any]) -> set[str]:
    archetype = spec["archetype"]
    sections = spec.get("sections") or []
    variants: set[str] = set()

    if archetype == "corpus":
        variants.add("plain_carcass")
        if spec.get("sides_over_top"):
            variants.add("sides_over_top")
        if (spec.get("legs") or {}).get("as_panel"):
            variants.add("socle_panel")
    elif archetype == "shelving":
        if any(section.get("shelves") is not None for section in sections):
            variants.add("shelf_count")
        if any(section.get("shelf_levels") for section in sections):
            variants.add("explicit_shelf_levels")
    elif archetype == "drawer_unit":
        section = sections[0] if sections else {}
        if section.get("drawers") is not None:
            variants.add("drawer_count")
        if section.get("drawer_heights"):
            variants.add("explicit_drawer_heights")
        if section.get("boxes") is False:
            variants.add("boxes_disabled")
        if section.get("front_top") is not None:
            variants.add("front_top_niche")
        if any(key in section for key in (
            "guide_gap", "box_z1", "box_depth", "box_y_offset", "box_height",
            "box_back_thickness", "box_bottom_thickness", "box_bottom_mode",
            "box_back_mode", "box_sides_on_bottom",
        )):
            variants.add("custom_box_geometry")
    elif archetype == "door_unit":
        section = sections[0] if sections else {}
        door_count = section.get("door", 1)
        variants.add("double_door" if door_count == 2 else "single_door")
        if section.get("shelves") is not None:
            variants.add("shelf_count")
        if section.get("shelf_levels"):
            variants.add("explicit_shelf_levels")
        if str(section.get("door_z", "")).lower() == "inset":
            variants.add("inset_door")
        if section.get("door_swing"):
            variants.add("explicit_door_swing")
    elif archetype in {"cabinet", "wardrobe"}:
        if len(sections) > 1:
            variants.add("multi_column")
        kinds = {str(section.get("kind", "open")) for section in sections}
        variants.update(f"{kind}_section" for kind in kinds)
        if len(kinds) > 1:
            variants.add("mixed_sections")
        if any(section.get("width_share") is not None for section in sections):
            variants.add("width_share")
        if spec.get("rod") or any(section.get("rod") for section in sections):
            variants.add("rods")
    elif archetype in {"desk", "table"}:
        variants.add("metal_frame" if spec.get("frame") == "metal" else "panel_supports")
        if spec.get("screen"):
            variants.add("modesty_screen")
        variants.add("apron_enabled" if spec.get("apron", True) else "apron_disabled")
        if spec.get("top_overhang"):
            variants.add("top_overhang")
    elif archetype == "round_table":
        variants.add("pedestal_base" if spec.get("base") else "no_base")
        if any(spec.get(key) is not None for key in (
            "top_thickness", "pedestal_diameter", "base_thickness", "base_diameter",
        )):
            variants.add("custom_diameters")
    elif archetype == "composite":
        blocks = spec.get("blocks") or []
        variants.add("single_level_blocks")
        if any((block.get("origin") or {}).get("x", 0) for block in blocks):
            variants.add("horizontal_offsets")
        if any((block.get("origin") or {}).get("y", 0) for block in blocks):
            variants.add("vertical_offsets")
        if len({(block.get("spec") or {}).get("archetype") for block in blocks}) > 1:
            variants.add("mixed_block_archetypes")
    return variants


def build_report() -> dict[str, Any]:
    from src.generators.registry import _REGISTRY
    from src.paramspec import _ARCHETYPE_TAGS

    specs_by_archetype: dict[str, list[str]] = defaultdict(list)
    evidence: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for path in _spec_paths():
        spec = json.loads(path.read_text(encoding="utf-8"))
        archetype = spec.get("archetype")
        if archetype not in _ARCHETYPE_TAGS:
            continue
        specs_by_archetype[archetype].append(path.stem)
        for variant in _variants(spec):
            evidence[archetype][variant].append(path.stem)

    project_names = {path.name for path in (ROOT / "projects").glob("*.json")}
    rows = []
    for archetype in sorted(_ARCHETYPE_TAGS):
        fixtures = sorted(specs_by_archetype[archetype])
        goldens = sorted(name for name in fixtures if f"{name}.json" in project_names)
        observed = {
            variant: sorted(names)
            for variant, names in sorted(evidence[archetype].items())
        }
        rows.append({
            "archetype": archetype,
            "paramspec_model": MODEL_BY_ARCHETYPE[archetype],
            "generator": f"{_REGISTRY[archetype].__module__}.{_REGISTRY[archetype].__name__}",
            "paramspec_fixtures": fixtures,
            "exact_panel_goldens": goldens,
            "supported_variants": SUPPORTED_VARIANTS[archetype],
            "observed_variant_evidence": observed,
            "uncovered_variants": sorted(set(SUPPORTED_VARIANTS[archetype]) - set(observed)),
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
    return {
        "schema_version": "archetype-coverage-v1",
        "sources": [
            "src/paramspec.py", "schema/paramspec.schema.json",
            "src/generators/registry.py", "src/generators/*.py",
            "paramspecs/*.json", "projects/*.json", "tests/regression.py",
        ],
        "summary": {
            "public_archetype_count": len(_ARCHETYPE_TAGS),
            "generator_implementation_count": len(implementations),
            "paramspec_fixture_count": sum(len(names) for names in specs_by_archetype.values()),
            "matched_exact_panel_golden_count": len(matched_projects),
            "archetypes_with_paramspec_fixture": sum(bool(row["paramspec_fixtures"]) for row in rows),
            "archetypes_with_exact_panel_golden": sum(bool(row["exact_panel_goldens"]) for row in rows),
            "implementations_with_exact_panel_golden": sum(
                bool(row["exact_panel_goldens"]) for row in implementation_rows
            ),
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
                "regression_exact_goldens": "15/15",
            },
        },
        "remaining_gaps": [
            {
                "id": "MEB-133-G02",
                "status": "open",
                "title": "Variant branches do not all have ParamSpec evidence",
                "evidence": "See archetypes[].uncovered_variants",
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
