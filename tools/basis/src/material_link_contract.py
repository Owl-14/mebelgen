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
from math import isfinite
from typing import Any, Iterable, Mapping, Sequence


CAD_MODEL_PLATE = 0
CUTTING_ITEM_PLATE = 1
MATBASE_PLATE = 0
_LINK_KEYS = {
    "originalMaterialFullName",
    "materialType",
    "linkedMaterialFullName",
}
_EXPECTED_SHEET_KEYS = {"height", "width", "count", "production", "materialType"}


class MaterialLinkContractError(ValueError):
    """The offline material-link contract is malformed or ambiguous."""


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MaterialLinkContractError(f"{field} must be a non-empty string")
    return value.strip()


def _normal_name(value: str) -> str:
    return " ".join(value.split()).casefold()


def _strict_positive_number(value: Any) -> bool:
    return (
        not isinstance(value, bool) and isinstance(value, (int, float))
        and isfinite(value) and value > 0
    )


def _strict_positive_int(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value > 0


def _strict_nonnegative_int(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0


def _expected_sheets(raw: Any, field: str) -> list[dict[str, Any]]:
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence) or not raw:
        raise MaterialLinkContractError(f"{field} must be a non-empty array")
    result: list[dict[str, Any]] = []
    for index, sheet in enumerate(raw):
        if not isinstance(sheet, Mapping) or set(sheet) != _EXPECTED_SHEET_KEYS:
            raise MaterialLinkContractError(
                f"{field}[{index}] must contain exactly {sorted(_EXPECTED_SHEET_KEYS)}"
            )
        if not all(_strict_positive_number(sheet.get(key)) for key in ("height", "width", "count")):
            raise MaterialLinkContractError(f"{field}[{index}] dimensions/count must be positive numbers")
        if sheet.get("production") is not True:
            raise MaterialLinkContractError(f"{field}[{index}].production must be true")
        material_type = sheet.get("materialType")
        if isinstance(material_type, bool) or not isinstance(material_type, int) \
                or material_type != CUTTING_ITEM_PLATE:
            raise MaterialLinkContractError(
                f"{field}[{index}].materialType must be integer {CUTTING_ITEM_PLATE} (Plate)"
            )
        result.append({key: sheet[key] for key in sorted(_EXPECTED_SHEET_KEYS)})
    return result


def _sheet_signature(sheet: Mapping[str, Any]) -> tuple[Any, ...] | None:
    if not all(_strict_positive_number(sheet.get(key)) for key in ("height", "width", "count")):
        return None
    if sheet.get("production") is not True:
        return None
    material_type = sheet.get("materialType")
    if isinstance(material_type, bool) or not isinstance(material_type, int) \
            or material_type != CUTTING_ITEM_PLATE:
        return None
    return (
        sheet["height"], sheet["width"], sheet["count"],
        sheet["production"], sheet["materialType"],
    )


def serialize_link_payload(links: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Validate and copy the exact OpenAPI ``CuttingMaterialLinkDTO`` shape."""
    if isinstance(links, (str, bytes)) or not isinstance(links, Sequence):
        raise MaterialLinkContractError("links must be a sequence of objects")
    payload: list[dict[str, Any]] = []
    seen: dict[tuple[str, int], str] = {}
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
        key = (_normal_name(source), material_type)
        if key in seen:
            if seen[key] != target:
                raise MaterialLinkContractError(
                    f"conflicting duplicate material link: {source!r}, type={material_type}"
                )
            raise MaterialLinkContractError(
                f"duplicate material link: {source!r}, type={material_type}"
            )
        seen[key] = target
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
            width, height, thickness = size.get("x"), size.get("y"), panel.get("thickness")
            if not all(_strict_positive_number(value) for value in (width, height, thickness)):
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
    if len(cad_names) != len({_normal_name(name) for name in cad_names}):
        raise MaterialLinkContractError("cad_model_materials contains duplicate normalized names")
    manifest = sheet_manifest(cfrn)

    by_exact: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_normal: dict[str, list[dict[str, Any]]] = defaultdict(list)
    cad_by_normal: dict[str, list[str]] = defaultdict(list)
    for item in manifest:
        by_exact[item["source_name"]].append(item)
        by_normal[_normal_name(item["source_name"])].append(item)
    for name in cad_names:
        cad_by_normal[_normal_name(name)].append(name)

    confirmed: dict[str, Mapping[str, Any]] = {}
    for raw in confirmations:
        source = _text(raw.get("originalMaterialFullName"), "confirmation.originalMaterialFullName")
        normalized_source = _normal_name(source)
        if normalized_source in confirmed:
            raise MaterialLinkContractError(f"duplicate confirmation for {source!r}")
        _text(raw.get("linkedMaterialFullName"), "confirmation.linkedMaterialFullName")
        _text(raw.get("article"), "confirmation.article")
        _expected_sheets(raw.get("sheets"), "confirmation.sheets")
        confirmed[normalized_source] = raw

    payload: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    for cad_name in cad_names:
        exact_matches = by_exact.get(cad_name, [])
        local = exact_matches[0] if len(exact_matches) == 1 else None
        match = "exact_name"
        if not exact_matches:
            key = _normal_name(cad_name)
            local_matches = by_normal.get(key, [])
            if len(local_matches) == 1 and len(cad_by_normal[key]) == 1:
                local = local_matches[0]
                match = "unique_normalized_name"
        if local is None:
            decisions.append({"source_name": cad_name, "status": "unresolved",
                              "reason": "no unique exact/normalized sheet material in exported CFRN"})
            continue

        confirmation = confirmed.get(_normal_name(cad_name)) \
            or confirmed.get(_normal_name(local["source_name"]))
        if confirmation is None:
            decisions.append({"source_name": cad_name, "status": "blocked",
                              "article": local["article"], "match": match,
                              "reason": "exact linkedMaterialFullName is not externally confirmed"})
            continue
        confirmed_article = _text(confirmation.get("article"), "confirmation.article")
        if not local["article"] or confirmed_article != local["article"]:
            decisions.append({"source_name": cad_name, "status": "blocked",
                              "article": local["article"], "match": match,
                              "reason": "confirmed article differs from exported CFRN article"})
            continue
        expected_sheets = _expected_sheets(confirmation.get("sheets"), "confirmation.sheets")
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
                          "panel_count": local["panel_count"],
                          "expected_sheets": expected_sheets})

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
    decisions = plan.get("decisions")
    decision_by_source = {
        item.get("source_name"): item for item in decisions
        if isinstance(item, Mapping) and item.get("status") == "ready"
    } if isinstance(decisions, list) else {}
    full_plan_ready = (
        plan.get("ready") is True
        and bool(payload)
        and isinstance(decisions, list)
        and len(decisions) == len(payload)
        and len(decision_by_source) == len(payload)
        and set(decision_by_source) == {link["originalMaterialFullName"] for link in payload}
    )
    results: list[dict[str, Any]] = []
    for link in payload:
        decision = decision_by_source.get(link["originalMaterialFullName"], {})
        expected_article = decision.get("article")
        try:
            expected_sheets = _expected_sheets(
                decision.get("expected_sheets"), "plan.decision.expected_sheets"
            )
        except MaterialLinkContractError:
            expected_sheets = []
        expected_signatures = sorted(_sheet_signature(sheet) for sheet in expected_sheets)
        target = link["linkedMaterialFullName"]
        candidates = [item for item in cutting_materials
                      if isinstance(item, Mapping) and item.get("name") == target]
        item = candidates[0] if len(candidates) == 1 else None
        base_id = item.get("inMaterialBaseId") if item else None
        article_matches = bool(
            item and isinstance(item.get("article"), str)
            and item["article"] == expected_article
        )
        raw_sheets = item.get("items") if item else None
        actual_signatures = []
        sheets_schema_valid = isinstance(raw_sheets, list) and bool(raw_sheets)
        if sheets_schema_valid:
            for sheet in raw_sheets:
                signature = _sheet_signature(sheet) if isinstance(sheet, Mapping) else None
                if signature is None:
                    sheets_schema_valid = False
                    break
                actual_signatures.append(signature)
        sheet_mapping_matches = (
            sheets_schema_valid and bool(expected_signatures)
            and sorted(actual_signatures) == expected_signatures
        )
        cutted = [row for row in cutted_materials if isinstance(row, Mapping)
                  and row.get("name") == target]
        cutted_evidence = False
        if len(cutted) == 1 and expected_sheets:
            row = cutted[0]
            statistic = row.get("statistic")
            dimensions_match = any(
                row.get("height") == sheet["height"] and row.get("width") == sheet["width"]
                for sheet in expected_sheets
            )
            cutted_evidence = (
                isinstance(row.get("article"), str) and row["article"] == expected_article
                and isinstance(row.get("type"), int) and not isinstance(row.get("type"), bool)
                and row["type"] == MATBASE_PLATE
                and _strict_positive_int(row.get("inMaterialBaseId"))
                and row.get("inMaterialBaseId") == base_id
                and _strict_positive_number(row.get("height"))
                and _strict_positive_number(row.get("width"))
                and dimensions_match
                and isinstance(statistic, Mapping)
                and _strict_nonnegative_int(statistic.get("boardsCount"))
                and _strict_nonnegative_int(statistic.get("countPlate"))
                and (
                    statistic["boardsCount"] > 0 or statistic["countPlate"] > 0
                )
            )
        results.append({
            "source_name": link["originalMaterialFullName"],
            "linked_name": target,
            "unique_result": item is not None,
            "matbase_reference_present": _strict_positive_int(base_id),
            "article_matches": article_matches,
            "sheet_mapping_matches": sheet_mapping_matches,
            "cutting_result_present": cutted_evidence,
        })
    return {
        "results": results,
        "offline_contract_passed": full_plan_ready and bool(results) and all(
            row["unique_result"] and row["matbase_reference_present"]
            and row["article_matches"] and row["sheet_mapping_matches"]
            and row["cutting_result_present"]
            for row in results
        ),
        "real_environment_verified": False,
        "blocker": (
            "Visual MatBase identity and real production output require a licensed "
            "Basis/Cutting environment and cannot be proven by offline fixtures."
        ),
    }
