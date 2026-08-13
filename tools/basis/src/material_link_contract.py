"""Offline contract for Cutting ``set-link-materials`` (MEB-139).

The public endpoint links a material name parsed from the uploaded CAD model to
an already known full MatBase name.  It does *not* accept an article or sheet
dimensions.  Those facts can only be checked in the subsequent Cutting DTOs.

This module deliberately never derives a MatBase name from a local article or
from a fuzzy search.  A target name must come from an explicit confirmation
(for example, a separately captured licensed-environment fixture).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Mapping, Sequence


CAD_MODEL_PLATE = 0
CUTTING_ITEM_PLATE = 1
_LINK_KEYS = {
    "originalMaterialFullName",
    "materialType",
    "linkedMaterialFullName",
}


class MaterialLinkContractError(ValueError):
    """The offline material-link contract is malformed or ambiguous."""


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MaterialLinkContractError(f"{field} must be a non-empty string")
    return value.strip()


def _normal_name(value: str) -> str:
    return " ".join(value.split()).casefold()


def _positive_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def serialize_link_payload(links: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Validate and copy the exact OpenAPI ``CuttingMaterialLinkDTO`` shape."""
    if isinstance(links, (str, bytes)) or not isinstance(links, Sequence):
        raise MaterialLinkContractError("links must be a sequence of objects")
    payload: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for index, raw in enumerate(links):
        if not isinstance(raw, Mapping):
            raise MaterialLinkContractError(f"links[{index}] must be an object")
        extra = set(raw) - _LINK_KEYS
        missing = _LINK_KEYS - set(raw)
        if extra or missing:
            raise MaterialLinkContractError(
                f"links[{index}] keys mismatch: missing={sorted(missing)}, extra={sorted(extra)}"
            )
        source = _text(raw["originalMaterialFullName"], "originalMaterialFullName")
        target = _text(raw["linkedMaterialFullName"], "linkedMaterialFullName")
        material_type = raw["materialType"]
        if isinstance(material_type, bool) or not isinstance(material_type, int) \
                or material_type not in range(6):
            raise MaterialLinkContractError("materialType must be a CadModelMaterialType integer 0..5")
        key = (source, material_type)
        if key in seen:
            raise MaterialLinkContractError(f"duplicate material link: {source!r}, type={material_type}")
        seen.add(key)
        payload.append({
            "originalMaterialFullName": source,
            "materialType": material_type,
            "linkedMaterialFullName": target,
        })
    return payload


def sheet_manifest(cfrn: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return plate materials actually referenced by panel objects in CFRN JSON.

    The manifest is the local bridge between model material name, local article
    and sheet geometry.  It is evidence about the exported model only; it is not
    evidence that MatBase contains the same name/article.
    """
    table = cfrn.get("table")
    if not isinstance(table, Mapping):
        raise MaterialLinkContractError("cfrn.table must be an object")
    materials = table.get("materials")
    objects = table.get("objects")
    if not isinstance(materials, list) or not isinstance(objects, list):
        raise MaterialLinkContractError("cfrn.table materials/objects must be arrays")

    panels: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for obj in objects:
        if not isinstance(obj, Mapping) or obj.get("objType") != 2:
            continue
        material_index = obj.get("materialIndex")
        if isinstance(material_index, bool) or not isinstance(material_index, int):
            raise MaterialLinkContractError("panel materialIndex must be an integer")
        if material_index < 0 or material_index >= len(materials):
            raise MaterialLinkContractError(f"panel materialIndex out of range: {material_index}")
        panels[material_index].append(obj)

    manifest: list[dict[str, Any]] = []
    for material_index, material_panels in sorted(panels.items()):
        material = materials[material_index]
        if not isinstance(material, Mapping):
            raise MaterialLinkContractError(f"material[{material_index}] must be an object")
        name = _text(material.get("name"), f"material[{material_index}].name")
        article = str(material.get("art") or "").strip() or None
        sheets = []
        for panel in material_panels:
            contour = panel.get("contour")
            size = contour.get("size") if isinstance(contour, Mapping) else None
            if not isinstance(size, Mapping):
                raise MaterialLinkContractError("panel contour.size must be an object")
            width = float(size.get("x", 0))
            height = float(size.get("y", 0))
            thickness = float(panel.get("thickness", 0))
            if min(width, height, thickness) <= 0:
                raise MaterialLinkContractError("panel sheet dimensions must be positive")
            sheets.append({"width": width, "height": height, "thickness": thickness})
        manifest.append({
            "material_index": material_index,
            "source_name": name,
            "article": article,
            "panel_count": len(sheets),
            "sheets": sheets,
        })
    return manifest


def plan_sheet_links(
    cad_model_materials: Sequence[str],
    cfrn: Mapping[str, Any],
    confirmations: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build plate links only from explicit MatBase confirmations.

    Matching uses an exact source name first.  A case/whitespace-normalized
    fallback is allowed only when it is unique on both sides.  A local article
    may cross-check the confirmation but can never supply the target name.
    """
    if isinstance(cad_model_materials, (str, bytes)):
        raise MaterialLinkContractError("cad_model_materials must be an array of strings")
    cad_names = [_text(name, "cad_model_materials[]") for name in cad_model_materials]
    if len(cad_names) != len(set(cad_names)):
        raise MaterialLinkContractError("cad_model_materials contains duplicate exact names")
    manifest = sheet_manifest(cfrn)

    by_exact = {item["source_name"]: item for item in manifest}
    by_normal: dict[str, list[dict[str, Any]]] = defaultdict(list)
    cad_by_normal: dict[str, list[str]] = defaultdict(list)
    for item in manifest:
        by_normal[_normal_name(item["source_name"])].append(item)
    for name in cad_names:
        cad_by_normal[_normal_name(name)].append(name)

    confirmed: dict[str, Mapping[str, Any]] = {}
    for raw in confirmations:
        source = _text(raw.get("originalMaterialFullName"), "confirmation.originalMaterialFullName")
        if source in confirmed:
            raise MaterialLinkContractError(f"duplicate confirmation for {source!r}")
        _text(raw.get("linkedMaterialFullName"), "confirmation.linkedMaterialFullName")
        confirmed[source] = raw

    payload: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    for cad_name in cad_names:
        local = by_exact.get(cad_name)
        match = "exact_name"
        if local is None:
            key = _normal_name(cad_name)
            local_matches = by_normal.get(key, [])
            if len(local_matches) == 1 and len(cad_by_normal[key]) == 1:
                local = local_matches[0]
                match = "unique_normalized_name"
        if local is None:
            decisions.append({"source_name": cad_name, "status": "unresolved",
                              "reason": "no unique sheet material in exported CFRN"})
            continue

        confirmation = confirmed.get(cad_name) or confirmed.get(local["source_name"])
        if confirmation is None:
            decisions.append({"source_name": cad_name, "status": "blocked",
                              "article": local["article"], "match": match,
                              "reason": "exact linkedMaterialFullName is not externally confirmed"})
            continue
        confirmed_article = str(confirmation.get("article") or "").strip() or None
        if confirmed_article and local["article"] and confirmed_article != local["article"]:
            decisions.append({"source_name": cad_name, "status": "blocked",
                              "article": local["article"], "match": match,
                              "reason": "confirmed article differs from exported CFRN article"})
            continue
        link = {
            "originalMaterialFullName": cad_name,
            "materialType": CAD_MODEL_PLATE,
            "linkedMaterialFullName": _text(
                confirmation.get("linkedMaterialFullName"),
                "confirmation.linkedMaterialFullName",
            ),
        }
        payload.append(link)
        decisions.append({"source_name": cad_name, "status": "ready",
                          "article": local["article"], "match": match,
                          "panel_count": local["panel_count"]})

    return {
        "payload": serialize_link_payload(payload),
        "decisions": decisions,
        "ready": bool(decisions) and all(item["status"] == "ready" for item in decisions),
    }


def audit_sheet_link_result(
    plan: Mapping[str, Any],
    cutting_materials: Sequence[Mapping[str, Any]],
    cutted_materials: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Audit captured Cutting DTOs without claiming a live/visual verification."""
    payload = serialize_link_payload(plan.get("payload", []))
    results: list[dict[str, Any]] = []
    for link in payload:
        target = link["linkedMaterialFullName"]
        candidates = [item for item in cutting_materials
                      if isinstance(item, Mapping) and item.get("name") == target]
        item = candidates[0] if len(candidates) == 1 else None
        base_id = item.get("inMaterialBaseId") if item else None
        plate_items = [sheet for sheet in (item.get("items") or []) if isinstance(sheet, Mapping)
                       and sheet.get("materialType") == CUTTING_ITEM_PLATE] if item else []
        valid_sheets = [sheet for sheet in plate_items
                        if all(_positive_number(sheet.get(key)) for key in ("height", "width", "count"))]
        production = bool(valid_sheets) and all(sheet.get("production") is True for sheet in valid_sheets)
        cutted = [row for row in cutted_materials if isinstance(row, Mapping)
                  and row.get("name") == target]
        cutted_evidence = bool(cutted) and any(
            _positive_number((row.get("statistic") or {}).get("boardsCount"))
            or _positive_number((row.get("statistic") or {}).get("countPlate"))
            for row in cutted if isinstance(row.get("statistic"), Mapping)
        )
        results.append({
            "source_name": link["originalMaterialFullName"],
            "linked_name": target,
            "unique_result": item is not None,
            "matbase_reference_present": isinstance(base_id, int) and not isinstance(base_id, bool) and base_id > 0,
            "sheet_mapping_present": bool(valid_sheets),
            "production_enabled": production,
            "cutting_result_present": cutted_evidence,
        })
    return {
        "results": results,
        "offline_contract_passed": bool(results) and all(
            row["unique_result"] and row["matbase_reference_present"]
            and row["sheet_mapping_present"] and row["production_enabled"]
            and row["cutting_result_present"]
            for row in results
        ),
        "real_environment_verified": False,
        "blocker": (
            "Visual MatBase identity and real production output require a licensed "
            "Basis/Cutting environment and cannot be proven by offline fixtures."
        ),
    }
