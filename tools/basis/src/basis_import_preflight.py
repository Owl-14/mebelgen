"""Source preflight expectations for the desktop BAZIS JSON importer.

This module never observes an imported BAZIS model. It validates the input and
computes expectations that a separately observed importer run must be compared
against. Native edge banding, drilling and ``.b3d`` remain blocked.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .cfrn import check_cfrn_encoding, check_cfrn_holes
from .consistency_check import check_consistency
from .drilling_check import check_drilling_geometry
from .geometry_check import check_placement_geometry
from .hardware import compute_drilling, drilling_summary
from .materials import check_project_materials
from .validate import validate_furniture


ORIENTATION_AXES: dict[str, tuple[str, str, str]] = {
    "horizont": ("x", "z", "y"),
    "horizontal": ("x", "z", "y"),
    "vertical": ("z", "y", "x"),
    "front": ("x", "y", "z"),
}

LICENSED_BASIS_CHECKS = [
    "run scripts/detect_basis_api.js in the target licensed BAZIS version",
    "run ImportFurnitureFromJSON.js with the pinned fixture and save a native .b3d",
    "compare native panel AABBs and orientations with source_expectations.panel_calls",
    "confirm MaterialName resolves to the intended MatBase entries",
    "confirm edge banding is assigned to the intended native panel edges",
    "confirm native drilling/fixtures and .b3d round-trip match source_expectations.drilling_computed_by_python",
]

_BLOCKED_RESULT_STATUS = "blocked_not_observed"


def _span(placement: dict[str, Any], axis: str) -> float:
    return round(float(placement[f"{axis}2"]) - float(placement[f"{axis}1"]), 4)


def _material_name(project: dict[str, Any]) -> str:
    materials = project.get("materials") or {}
    color = str(materials.get("color") or "")
    code = str(materials.get("color_code") or "")
    return f"{color} ({code})" if code else color


def source_panel_call_expectations(project: dict[str, Any]) -> list[dict[str, Any]]:
    """Compute expected JS calls from source JSON; this is not runtime evidence."""
    projected: list[dict[str, Any]] = []
    default_thickness = float((project.get("materials") or {}).get("board_thickness") or 16)
    material_name = _material_name(project)
    for panel in project.get("panels") or []:
        placement = panel.get("placement") or {}
        orientation = str(panel.get("basis_orientation") or "").lower()
        axes = ORIENTATION_AXES.get(orientation)
        if not axes or not placement:
            continue
        width_axis, height_axis, thickness_axis = axes
        projected.append(
            {
                "name": panel.get("name"),
                "orientation": "horizont" if orientation == "horizontal" else orientation,
                "new_panel": {
                    "width": _span(placement, width_axis),
                    "height": _span(placement, height_axis),
                    "thickness": float(panel.get("thickness") or default_thickness),
                },
                "thickness_axis": thickness_axis,
                "gab_min": {
                    "x": float(placement["x1"]),
                    "y": float(placement["y1"]),
                    "z": float(placement["z1"]),
                },
                "expected_gab_max": {
                    "x": float(placement["x2"]),
                    "y": float(placement["y2"]),
                    "z": float(placement["z2"]),
                },
                "material_name": material_name,
            }
        )
    return projected


def _hole_fingerprint(holes: list[dict[str, Any]]) -> str:
    normalized = [
        {
            key: hole.get(key)
            for key in ("panel", "purpose", "x", "y", "z", "diameter", "depth", "axis", "dir")
        }
        for hole in holes
    ]
    normalized.sort(key=lambda row: json.dumps(row, ensure_ascii=False, sort_keys=True))
    raw = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _edge_summary(project: dict[str, Any]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    missing: list[str] = []
    for panel in project.get("panels") or []:
        edges = panel.get("edge_banding")
        if not isinstance(edges, dict):
            missing.append(str(panel.get("name") or "<unnamed>"))
            continue
        for side in ("top", "bottom", "left", "right"):
            value = edges.get(side)
            if value is None:
                missing.append(f"{panel.get('name')}: {side}")
            else:
                counts[f"{float(value):g}"] += 1
    return {
        "assignments_by_thickness_mm": dict(sorted(counts.items())),
        "missing": missing,
    }


def validate_evidence_taxonomy(report: dict[str, Any]) -> list[str]:
    """Reject reports that present source/mock data as a native import result."""
    errors: list[str] = []
    taxonomy = report.get("evidence_taxonomy") or {}
    kind = taxonomy.get("kind")
    if kind not in {"source_preflight_expectations", "mock_observed_importer_calls"}:
        errors.append("evidence_taxonomy.kind is missing or unknown")
    if taxonomy.get("native_basis_observed") is not False:
        errors.append("offline evidence must set native_basis_observed=false")

    imported = report.get("imported_result") or {}
    if imported.get("status") != _BLOCKED_RESULT_STATUS:
        errors.append("imported_result must remain blocked_not_observed")
    for domain in ("panels", "materials", "edge_banding", "drilling", "native_b3d"):
        value = imported.get(domain)
        if not isinstance(value, str) or value not in {_BLOCKED_RESULT_STATUS, "not_observed"}:
            errors.append(f"imported_result.{domain} is a misleading offline claim")

    if kind == "source_preflight_expectations" and "source_expectations" not in report:
        errors.append("source preflight report must nest computed data under source_expectations")
    if kind == "mock_observed_importer_calls":
        observed = report.get("observed_importer_calls") or {}
        for domain in ("edge_banding", "drilling", "native_b3d"):
            item = observed.get(domain) or {}
            raw_count = item.get("call_count")
            if not isinstance(raw_count, int) or raw_count < 0:
                errors.append(f"observed_importer_calls.{domain}.call_count must be a non-negative integer")
                continue
            count = raw_count
            status = item.get("status")
            if count == 0 and status != "not_observed":
                errors.append(f"observed_importer_calls.{domain} must be not_observed when call_count=0")
            if count > 0 and status != "observed_in_mock":
                errors.append(f"observed_importer_calls.{domain} must be observed_in_mock when calls exist")
    return errors


def build_preflight_report(project: dict[str, Any], *, source: str = "<memory>") -> dict[str, Any]:
    schema_errors = validate_furniture(project)
    consistency = check_consistency(project)
    geometry = check_placement_geometry(project)
    holes = compute_drilling(project)
    drilling = check_drilling_geometry(project, holes)
    cfrn_encoding = check_cfrn_encoding(project)
    cfrn_holes = check_cfrn_holes(project)
    edges = _edge_summary(project)
    material_notes = check_project_materials(project)

    errors = list(schema_errors)
    # The general consistency checker labels some dimension drift as a warning
    # because Studio can repair it. The desktop importer cannot: it builds from
    # placement immediately, so every consistency issue is fatal here.
    errors.extend(issue.message for issue in consistency)
    errors.extend(overlap["detail"] for overlap in geometry["overlaps"])
    errors.extend(drilling["errors"])
    errors.extend(cfrn_encoding)
    errors.extend(cfrn_holes)
    errors.extend(f"edge_banding: {item}" for item in edges["missing"])

    warnings = list(drilling["warnings"])
    warnings.extend(material_notes)

    report = {
        "report_version": 2,
        "evidence_taxonomy": {
            "kind": "source_preflight_expectations",
            "native_basis_observed": False,
            "description": "computed from source JSON and Python engine; not an imported result",
        },
        "source": source,
        "project_name": project.get("project_name"),
        "source_preflight_status": "pass" if not errors else "fail",
        "source_expectations": {
            "schema": {"ok": not schema_errors, "errors": schema_errors},
            "dimensions_and_geometry": {
                "overall_dimensions": project.get("overall_dimensions"),
                "panel_count": len(project.get("panels") or []),
                "consistency_issues": [
                    {
                        "severity": issue.severity,
                        "panel": issue.panel,
                        "code": issue.code,
                        "message": issue.message,
                    }
                    for issue in consistency
                ],
                "geometry_ok": geometry["ok"],
                "orientation_counts": geometry["by_orientation"],
            },
            "panel_calls": source_panel_call_expectations(project),
            "materials_input": {
                "project": project.get("materials"),
                "panel_material_counts": dict(
                    sorted(
                        Counter(str(p.get("material") or "") for p in project.get("panels") or []).items()
                    )
                ),
                "material_refs_present": bool(project.get("material_refs")),
                "notes": material_notes,
            },
            "edge_banding_input": {
                "source_assignments_by_thickness_mm": edges["assignments_by_thickness_mm"],
                "missing_in_source": edges["missing"],
                "importer_api_calls_observed": False,
            },
            "drilling_computed_by_python": {
                "count": len(holes),
                "by_purpose": drilling_summary(holes),
                "geometry_errors": drilling["errors"],
                "geometry_warnings": drilling["warnings"],
                "fingerprint_sha256": _hole_fingerprint(holes),
                "importer_api_calls_observed": False,
            },
            "cfrn_computed_by_python": {
                "encoding_errors": cfrn_encoding,
                "hole_parity_errors": cfrn_holes,
            },
        },
        "errors": errors,
        "warnings": warnings,
        "imported_result": {
            "status": _BLOCKED_RESULT_STATUS,
            "panels": _BLOCKED_RESULT_STATUS,
            "materials": _BLOCKED_RESULT_STATUS,
            "edge_banding": _BLOCKED_RESULT_STATUS,
            "drilling": _BLOCKED_RESULT_STATUS,
            "native_b3d": _BLOCKED_RESULT_STATUS,
        },
        "licensed_basis": {
            "status": "blocked_not_run",
            "required_checks": LICENSED_BASIS_CHECKS,
        },
    }
    taxonomy_errors = validate_evidence_taxonomy(report)
    if taxonomy_errors:
        raise AssertionError("invalid evidence taxonomy: " + "; ".join(taxonomy_errors))
    return report


def preflight_file(path: Path, *, source: str | None = None) -> dict[str, Any]:
    project = json.loads(path.read_text(encoding="utf-8"))
    return build_preflight_report(project, source=source or path.as_posix())
