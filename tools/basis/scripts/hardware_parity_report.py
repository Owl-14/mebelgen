"""Offline golden/parity report for production drilling connections (MEB-152).

The report never calls Basis Cloud.  It compares deterministic drilling with
reviewed golden signatures, the Studio viewer payload, CFRN reconstruction and
the checked-in production B3D reference.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GOLDEN = ROOT / "qa" / "fixtures" / "hardware_connections_golden.json"
DEFAULT_APPROVAL = ROOT / "qa" / "approvals" / "hardware_connections_approval.json"
sys.path.insert(0, str(ROOT))


def _json_digest(value: Any) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _hole_signature(hole: dict[str, Any]) -> dict[str, Any]:
    return {
        "panel": str(hole.get("panel") or ""),
        "purpose": str(hole["purpose"]),
        "x": round(float(hole["x"]), 3),
        "y": round(float(hole["y"]), 3),
        "z": round(float(hole["z"]), 3),
        "diameter": round(float(hole["diameter"]), 3),
        "depth": round(float(hole["depth"]), 3),
        "axis": str(hole["axis"]),
        "dir": int(hole["dir"]),
    }


def _viewer_hole_signature(hole: dict[str, Any]) -> dict[str, Any]:
    return _hole_signature({
        **hole,
        "diameter": hole["d"],
    })


def _sorted_holes(holes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = [_hole_signature(hole) for hole in holes]
    return sorted(normalized, key=lambda item: json.dumps(
        item, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ))


def _sorted_viewer_holes(holes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = [_viewer_hole_signature(hole) for hole in holes]
    return sorted(normalized, key=lambda item: json.dumps(
        item, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ))


def _sorted_bom(bom: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        ({"name": str(row["name"]), "qty": int(row["qty"])} for row in bom),
        key=lambda row: row["name"],
    )


def _actual_case(case: dict[str, Any], holes: list[dict[str, Any]]) -> dict[str, Any]:
    from src.hardware import fastener_bom

    purposes = set(case["purposes"])
    selected = [hole for hole in holes if hole["purpose"] in purposes]
    signature = _sorted_holes(selected)
    return {
        "count": len(signature),
        "sha256": _json_digest(signature),
        "bom": _sorted_bom(fastener_bom(selected)),
    }


def _baseline_digest(config: dict[str, Any]) -> str:
    """Digest only the computed baseline; approval metadata lives elsewhere."""
    return _json_digest({
        "version": config["version"],
        "cases": config["cases"],
    })


def _topology_signature(
    spec: dict[str, Any],
    project: dict[str, Any],
    holes: list[dict[str, Any]],
    purposes: set[str],
) -> dict[str, Any]:
    """Human-reviewable class topology, independent from the coordinate digest."""
    selected = [hole for hole in holes if hole["purpose"] in purposes]
    panels = {str(panel.get("name") or ""): panel for panel in project.get("panels", [])}

    def role(hole: dict[str, Any]) -> str:
        panel = panels.get(str(hole.get("panel") or ""), {})
        panel_type = str(panel.get("type") or "unknown")
        if panel_type == "shelf":
            return "fixed_shelf" if panel.get("fixed") else "removable_shelf"
        return panel_type

    purpose_counts: dict[str, int] = {}
    role_counts: dict[str, int] = {}
    axis_direction_counts: dict[str, int] = {}
    for hole in selected:
        purpose = str(hole["purpose"])
        purpose_counts[purpose] = purpose_counts.get(purpose, 0) + 1
        panel_role = role(hole)
        role_counts[panel_role] = role_counts.get(panel_role, 0) + 1
        axis_key = f"{purpose}|{hole['axis']}|{int(hole['dir']):+d}"
        axis_direction_counts[axis_key] = axis_direction_counts.get(axis_key, 0) + 1
    return {
        "archetype": str(spec.get("archetype") or ""),
        "purpose_counts": dict(sorted(purpose_counts.items())),
        "panel_role_counts": dict(sorted(role_counts.items())),
        "axis_direction_counts": dict(sorted(axis_direction_counts.items())),
    }


def _b3d_actual(path: Path) -> dict[str, Any]:
    sys.path.insert(0, str(ROOT / "scripts"))
    from extract_b3d_hardware import extract

    data = extract(str(path))
    world_holes = sorted(
        data["world_holes"],
        key=lambda item: json.dumps(
            item, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ),
    )
    library = sorted(data["library"].values(), key=lambda item: item["name"])
    return {
        "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "panels": len(data["panels"]),
        "instances": len(data["instances"]),
        "world_holes": len(world_holes),
        "world_holes_sha256": _json_digest(world_holes),
        "library_sha256": _json_digest(library),
    }


def candidate_golden(config: dict[str, Any]) -> dict[str, Any]:
    """Return reviewed-input metadata plus freshly calculated candidate values."""
    from src.generators import generate_from_paramspec
    from src.hardware import compute_drilling

    result = {"version": config["version"], "cases": {}}
    projects: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
    for name, case in config["cases"].items():
        paramspec = str(case["paramspec"])
        if paramspec not in projects:
            spec = json.loads((ROOT / "paramspecs" / paramspec).read_text(encoding="utf-8"))
            project = generate_from_paramspec(spec)
            projects[paramspec] = (project, compute_drilling(project))
        result["cases"][name] = {
            "paramspec": paramspec,
            "purposes": list(case["purposes"]),
            "expected": _actual_case(case, projects[paramspec][1]),
        }
    return result


def build_report(
    config: dict[str, Any], approval: dict[str, Any] | None = None
) -> dict[str, Any]:
    from src.cfrn import check_cfrn_holes
    from src.generators import generate_from_paramspec
    from src.hardware import compute_drilling
    from src.studio import build_payload

    approval = approval or json.loads(DEFAULT_APPROVAL.read_text(encoding="utf-8"))
    approved_classes = approval["classes"]
    projects: dict[
        str, tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], dict[str, Any]]
    ] = {}
    cfrn_by_paramspec: dict[str, list[str]] = {}
    cases: dict[str, Any] = {}
    for name, case in config["cases"].items():
        paramspec = str(case["paramspec"])
        if paramspec not in projects:
            spec = json.loads((ROOT / "paramspecs" / paramspec).read_text(encoding="utf-8"))
            project = generate_from_paramspec(spec)
            projects[paramspec] = (
                spec, project, compute_drilling(project), build_payload(spec)
            )
            cfrn_by_paramspec[paramspec] = check_cfrn_holes(project)
        spec, project, holes, studio = projects[paramspec]
        actual = _actual_case(case, holes)
        expected = case["expected"]
        purposes = set(case["purposes"])
        viewer = [
            hole for hole in studio.get("viewer", {}).get("holes", [])
            if hole.get("purpose") in purposes
        ]
        engine_selected = [hole for hole in holes if hole["purpose"] in purposes]
        studio_sha = _json_digest(_sorted_viewer_holes(viewer))
        engine_sha = _json_digest(_sorted_holes(engine_selected))
        differences = []
        for field in ("count", "sha256", "bom"):
            if actual[field] != expected[field]:
                differences.append({
                    "field": field, "expected": expected[field], "actual": actual[field]
                })
        if studio_sha != engine_sha:
            differences.append({
                "field": "studio_payload",
                "expected": engine_sha,
                "actual": studio_sha,
            })
        if cfrn_by_paramspec[paramspec]:
            differences.append({
                "field": "cfrn_holes", "issues": cfrn_by_paramspec[paramspec]
            })
        class_approval = approved_classes.get(name)
        topology = _topology_signature(spec, project, holes, purposes)
        if class_approval is None:
            differences.append({"field": "approval", "issue": "class is not approved"})
        else:
            if paramspec != class_approval.get("paramspec"):
                differences.append({
                    "field": "approved_paramspec",
                    "expected": class_approval.get("paramspec"),
                    "actual": paramspec,
                })
            if topology != class_approval.get("topology"):
                differences.append({
                    "field": "topology",
                    "expected": class_approval.get("topology"),
                    "actual": topology,
                })
        cases[name] = {
            "ok": not differences,
            "paramspec": paramspec,
            "holes": actual["count"],
            "differences": differences,
        }

    provenance_differences = []
    baseline_sha = _baseline_digest(config)
    if approval.get("approval", {}).get("status") != "approved":
        provenance_differences.append({
            "field": "status", "expected": "approved",
            "actual": approval.get("approval", {}).get("status"),
        })
    if approval.get("approval", {}).get("baseline_sha256") != baseline_sha:
        provenance_differences.append({
            "field": "baseline_sha256",
            "expected": approval.get("approval", {}).get("baseline_sha256"),
            "actual": baseline_sha,
        })

    b3d_approval = approval["b3d_evidence"]
    b3d_path = ROOT / str(b3d_approval["path"])
    b3d_actual = _b3d_actual(b3d_path)
    b3d_expected = b3d_approval["expected"]
    b3d_differences = [
        {"field": field, "expected": b3d_expected.get(field), "actual": value}
        for field, value in b3d_actual.items()
        if b3d_expected.get(field) != value
    ]
    report = {
        "ok": (
            all(case["ok"] for case in cases.values())
            and not provenance_differences
            and not b3d_differences
        ),
        "computed_parity": {
            "scope": "engine golden ↔ Studio payload ↔ CFRN holes",
            "cases": cases,
        },
        "approval_provenance": {
            "ok": not provenance_differences,
            "approval_ref": approval["approval"].get("approval_ref"),
            "approved_by": approval["approval"].get("approved_by"),
            "differences": provenance_differences,
        },
        "approved_b3d_evidence": {
            "ok": not b3d_differences,
            "evidence_kind": "approved_fixture",
            "scope": "fixture integrity and independently extracted B3D inventory",
            "not_computed_parity": True,
            "path": str(b3d_approval["path"]),
            "provenance": b3d_approval.get("provenance"),
            "differences": b3d_differences,
        },
        "cloud_calls": 0,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--golden",
        type=Path,
        default=DEFAULT_GOLDEN,
    )
    parser.add_argument(
        "--candidate",
        action="store_true",
        help="print candidate values for deliberate review; never writes the golden",
    )
    args = parser.parse_args()
    config = json.loads(args.golden.read_text(encoding="utf-8"))
    result = candidate_golden(config) if args.candidate else build_report(config)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if args.candidate or result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
