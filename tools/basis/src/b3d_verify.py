"""Fail-closed offline semantic evidence for a BAZIS BZ85 candidate.

The verifier compares typed B3D nodes with the deterministic project: panel
identity/count, per-panel material nodes and thickness, plus drilling template
signatures expanded through furniture instances.  It still does not prove that
BAZIS opens the file; only a licensed BAZIS round-trip can do that.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any


def _child(node: tuple | None, name: str) -> tuple | None:
    if node and node[1] == "obj":
        return next((item for item in node[2] if item[0] == name), None)
    return None


def _value(node: tuple | None, name: str, default: Any = None) -> Any:
    item = _child(node, name)
    return item[2] if item else default


def _walk(node: tuple | None):
    if not node:
        return
    yield node
    if node[1] == "obj":
        for item in node[2]:
            yield from _walk(item)


def _signature(diameter: Any, depth: Any) -> tuple[float, float]:
    return round(float(diameter), 2), round(float(depth), 2)


def _expand(counter: Counter[tuple[float, float]]) -> list[dict[str, Any]]:
    return [
        {"diameter": diameter, "depth": depth, "count": count}
        for (diameter, depth), count in sorted(counter.items())
    ]


def _expected_panel_materials(project: dict[str, Any]) -> dict[str, str]:
    """Use the same deterministic CFRN mapping that feeds both B3D paths."""
    from .cfrn import project_to_cfrn_json

    encoded = project_to_cfrn_json(project)
    materials = encoded["table"]["materials"]
    result: dict[str, str] = {}
    for obj in encoded["table"]["objects"]:
        if obj.get("objType") != 2 or not obj.get("name"):
            continue
        index = obj.get("materialIndex")
        if isinstance(index, int) and 0 <= index < len(materials):
            result[str(obj["name"])] = str(materials[index].get("name", ""))
    return result


def _material_matches(expected: str, actual: str) -> bool:
    expected_norm = " ".join(expected.casefold().split())
    actual_norm = " ".join(actual.casefold().split())
    return bool(expected_norm and actual_norm and (expected_norm in actual_norm or actual_norm in expected_norm))


def _b3d_evidence(doc: dict[str, Any]) -> dict[str, Any]:
    roots = [root for _flag, root in doc.get("sections", [])]
    document = next((root for root in roots if root[0] == "Document"), None)
    model = _child(document, "Model")
    explicit_objs = _child(model, "Objs")
    model_objs = explicit_objs or model
    has_model_objs = bool(
        model_objs and model_objs[1] == "obj"
        and any(item[0] == "Obj" for item in model_objs[2])
    )
    furn_list = _child(document, "FurnList")

    panels: list[dict[str, Any]] = []
    instances: list[int] = []
    for node in _walk(model_objs):
        node_type = _value(node, "Type")
        if node_type == 4002:
            panels.append({
                "name": str(_value(node, "Name", "")).split("\r")[0],
                "material": str(_value(node, "Mat", "")),
                "thickness": _value(node, "Thick"),
            })
        elif node_type == 3001:
            fast_id = _value(node, "FastID")
            if isinstance(fast_id, int):
                instances.append(fast_id)

    library: dict[int, list[tuple[float, float]]] = {}
    hole_nodes = 0
    for furniture in furn_list[2] if furn_list and furn_list[1] == "obj" else []:
        fast_id = _value(furniture, "FastID")
        holes = _child(furniture, "Holes")
        signatures: list[tuple[float, float]] = []
        for hole in holes[2] if holes and holes[1] == "obj" else []:
            radius = _value(hole, "Radius")
            depth = _value(hole, "Depth")
            if radius is not None and depth is not None:
                signatures.append(_signature(float(radius) * 2, depth))
                hole_nodes += 1
        if isinstance(fast_id, int):
            library[fast_id] = signatures

    drilling = Counter(signature for fast_id in instances for signature in library.get(fast_id, []))
    return {
        "nodes": {
            "document": document is not None,
            "model": model is not None,
            "model_objs": has_model_objs,
            "furn_list": furn_list is not None,
            "hole_templates": hole_nodes,
        },
        "panels": panels,
        "furniture_instances": len(instances),
        "drilling": drilling,
    }


def verify_b3d_parity(b3d_path: str | Path, project: dict[str, Any]) -> dict[str, Any]:
    """Require non-empty typed semantic evidence; never accept an empty container."""
    from .b3d_format import parse_b3d
    from .hardware import compute_drilling

    expected_panels = [panel for panel in project.get("panels", []) if isinstance(panel, dict)]
    if not expected_panels:
        return {
            "ok": False,
            "errors": [{"code": "project.panels_required", "detail": "project.panels должен быть непустым"}],
            "nodes": {},
            "panels": {"expected": 0, "actual": 0, "missing": [], "unexpected": []},
            "materials": {"expected": 0, "actual": 0, "missing": [], "thickness_mismatch": []},
            "drilling": {"expected": 0, "actual": 0, "expected_signatures": [], "actual_signatures": []},
        }

    doc = parse_b3d(Path(b3d_path).read_bytes())
    evidence = _b3d_evidence(doc)
    errors: list[dict[str, Any]] = []
    for name in ("document", "model", "model_objs"):
        if not evidence["nodes"][name]:
            errors.append({"code": f"b3d.node.{name}_missing", "detail": f"Обязательный узел {name} не найден"})

    expected_by_name = {str(panel.get("name", "")): panel for panel in expected_panels if panel.get("name")}
    expected_materials = _expected_panel_materials(project)
    actual_by_name = {panel["name"]: panel for panel in evidence["panels"] if panel["name"]}
    missing_panels = sorted(set(expected_by_name) - set(actual_by_name))
    unexpected_panels = sorted(set(actual_by_name) - set(expected_by_name))
    if len(evidence["panels"]) != len(expected_panels):
        errors.append({
            "code": "b3d.panels.count_mismatch",
            "detail": f"ожидалось {len(expected_panels)}, найдено {len(evidence['panels'])}",
        })
    if missing_panels or unexpected_panels:
        errors.append({"code": "b3d.panels.identity_mismatch", "detail": "Имена панелей не совпадают"})

    missing_materials: list[str] = []
    material_mismatch: list[dict[str, str]] = []
    thickness_mismatch: list[dict[str, Any]] = []
    material_signatures: list[dict[str, Any]] = []
    for name, expected in expected_by_name.items():
        actual = actual_by_name.get(name)
        if actual is None:
            continue
        material = actual["material"].strip()
        if not material:
            missing_materials.append(name)
        else:
            expected_material = expected_materials.get(name, "")
            material_signatures.append({
                "panel": name,
                "expected": expected_material,
                "actual": material,
            })
            if not _material_matches(expected_material, material):
                material_mismatch.append({
                    "panel": name,
                    "expected": expected_material,
                    "actual": material,
                })
        expected_thickness = expected.get("thickness")
        actual_thickness = actual.get("thickness")
        if expected_thickness is not None and (
            actual_thickness is None or abs(float(expected_thickness) - float(actual_thickness)) > 0.1
        ):
            thickness_mismatch.append({
                "panel": name,
                "expected": expected_thickness,
                "actual": actual_thickness,
            })
    if missing_materials:
        errors.append({"code": "b3d.materials.missing", "detail": "У панелей отсутствуют Mat-узлы"})
    if material_mismatch:
        errors.append({"code": "b3d.materials.signature_mismatch", "detail": "Материалы панелей не совпадают с CFRN mapping"})
    if thickness_mismatch:
        errors.append({"code": "b3d.materials.thickness_mismatch", "detail": "Толщина панелей не совпадает"})

    expected_holes = compute_drilling(project)
    expected_drilling = Counter(_signature(hole["diameter"], hole["depth"]) for hole in expected_holes)
    actual_drilling = evidence["drilling"]
    if expected_drilling and not evidence["nodes"]["furn_list"]:
        errors.append({"code": "b3d.node.furn_list_missing", "detail": "Ожидалась FurnList для присадок"})
    if sum(actual_drilling.values()) != sum(expected_drilling.values()):
        errors.append({
            "code": "b3d.drilling.count_mismatch",
            "detail": f"ожидалось {sum(expected_drilling.values())}, найдено {sum(actual_drilling.values())}",
        })
    if actual_drilling != expected_drilling:
        errors.append({"code": "b3d.drilling.signature_mismatch", "detail": "Диаметры/глубины присадок не совпадают"})

    return {
        "ok": not errors,
        "errors": errors,
        "nodes": evidence["nodes"],
        "panels": {
            "expected": len(expected_panels),
            "actual": len(evidence["panels"]),
            "missing": missing_panels,
            "unexpected": unexpected_panels,
        },
        "materials": {
            "expected": len(expected_by_name),
            "actual": len(material_signatures),
            "missing": missing_materials,
            "signature_mismatch": material_mismatch,
            "thickness_mismatch": thickness_mismatch,
            "signatures": material_signatures,
        },
        "drilling": {
            "expected": sum(expected_drilling.values()),
            "actual": sum(actual_drilling.values()),
            "expected_signatures": _expand(expected_drilling),
            "actual_signatures": _expand(actual_drilling),
        },
    }
