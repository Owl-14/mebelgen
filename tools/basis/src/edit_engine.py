"""Deterministic geometry resolver for semantic panel edits.

The public interface deliberately accepts plain operation mappings.  MEB-143's
typed EditOperation reducer can delegate its geometry subset here without this
module becoming a second general-purpose reducer.  The engine is atomic: it
returns either a ParamSpec v1 copy containing resolved legacy-compatible
overrides, or one structured refusal.  LLM callers never provide placement.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field as dataclass_field
from typing import Any, Mapping, Sequence


_EDGES = ("x1", "x2", "y1", "y2", "z1", "z2")
_SUPPORTED_TYPES = {
    "shelf": "horizont",
    "vertical_partition": "vertical",
}
_ADD_KINDS = {"add", "add_panel", "panel.add"}
_MOVE_KINDS = {"move", "move_panel", "panel.move"}
_TOL = 0.6


@dataclass(frozen=True)
class EditFailure:
    code: str
    message: str
    operation_index: int | None = None
    field: str | None = None
    details: dict[str, Any] = dataclass_field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.operation_index is not None:
            out["operation_index"] = self.operation_index
        if self.field:
            out["field"] = self.field
        if self.details:
            out["details"] = self.details
        return out


@dataclass(frozen=True)
class EditEngineResult:
    spec: dict[str, Any] | None
    resolved_overrides: tuple[dict[str, Any], ...] = ()
    quality_gates: dict[str, list[str]] = dataclass_field(default_factory=dict)
    failure: EditFailure | None = None

    @property
    def ok(self) -> bool:
        return self.spec is not None and self.failure is None


class _Refusal(Exception):
    def __init__(self, code: str, message: str, *, field: str | None = None,
                 details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field
        self.details = details or {}


def _r(value: float) -> float:
    value = round(float(value), 2)
    return int(value) if value == int(value) else value


def _operation_kind(operation: Mapping[str, Any]) -> str:
    return str(operation.get("kind") or operation.get("operation")
               or operation.get("action") or "").strip().lower()


def _panel_type(operation: Mapping[str, Any]) -> str:
    value = operation.get("panel_type") or operation.get("detail_type")
    if value is None and operation.get("type") in _SUPPORTED_TYPES:
        value = operation.get("type")
    return str(value or "").strip().lower()


def _panel_id(operation: Mapping[str, Any]) -> str:
    return str(operation.get("panel_id") or operation.get("panel")
               or operation.get("name") or "").strip()


def _placement(panel: Mapping[str, Any]) -> dict[str, float]:
    placement = panel.get("placement")
    if not isinstance(placement, dict) or any(edge not in placement for edge in _EDGES):
        raise _Refusal("target_geometry_missing", "У детали нет полной геометрии placement")
    return {edge: float(placement[edge]) for edge in _EDGES}


def _find_panel(project: Mapping[str, Any], panel_id: str, *, field: str) -> dict[str, Any]:
    matches = [panel for panel in project.get("panels", [])
               if str(panel.get("id") or "") == panel_id
               or str(panel.get("name") or "") == panel_id]
    if not matches:
        raise _Refusal("target_not_found", f"Деталь «{panel_id}» не найдена", field=field,
                       details={"panel_id": panel_id})
    if len(matches) > 1:
        raise _Refusal("target_ambiguous", f"Идентификатор «{panel_id}» не уникален",
                       field=field, details={"panel_id": panel_id, "matches": len(matches)})
    return matches[0]


def _section_ids(project: Mapping[str, Any]) -> list[str]:
    return [str(section.get("id")) for section in project.get("sections", [])
            if isinstance(section, dict) and section.get("id") is not None]


def _resolve_section_id(project: Mapping[str, Any], requested: Any,
                        target: Mapping[str, Any] | None) -> str:
    section_id = str(requested or target and target.get("section_id") or "").strip()
    ids = _section_ids(project)
    if not section_id:
        if target is not None:
            sections = [section for section in project.get("sections", [])
                        if isinstance(section, dict)]
            columns = ((project.get("carcass_calculation") or {}).get("columns") or [])
            center = (_placement(target)["x1"] + _placement(target)["x2"]) / 2
            inferred = [str(sections[index].get("id"))
                        for index, column in enumerate(columns)
                        if index < len(sections) and isinstance(column, dict)
                        and isinstance(column.get("x"), (list, tuple))
                        and len(column["x"]) == 2
                        and float(column["x"][0]) - _TOL <= center
                        <= float(column["x"][1]) + _TOL]
            if len(inferred) == 1:
                return inferred[0]
        if len(ids) == 1:
            return ids[0]
        raise _Refusal("section_required", "Для геометрической правки нужен section_id",
                       field="section_id", details={"available": ids})
    if section_id not in ids:
        raise _Refusal("section_not_found", f"Секция «{section_id}» не найдена",
                       field="section_id", details={"section_id": section_id,
                                                    "available": ids})
    if target is not None and target.get("section_id") not in (None, section_id):
        raise _Refusal("target_section_mismatch",
                       f"Деталь «{target.get('name')}» не принадлежит секции «{section_id}»",
                       field="section_id")
    return section_id


def _overlap(a1: float, a2: float, b1: float, b2: float) -> float:
    return min(a2, b2) - max(a1, b1)


def _section_bounds(project: Mapping[str, Any], section_id: str) -> dict[str, float]:
    sections = [section for section in project.get("sections", []) if isinstance(section, dict)]
    index = next((i for i, section in enumerate(sections)
                  if str(section.get("id")) == section_id), None)
    columns = ((project.get("carcass_calculation") or {}).get("columns") or [])
    x1 = x2 = None
    if index is not None and index < len(columns):
        raw = columns[index].get("x") if isinstance(columns[index], dict) else None
        if isinstance(raw, (list, tuple)) and len(raw) == 2:
            x1, x2 = map(float, raw)

    panels = [panel for panel in project.get("panels", [])
              if isinstance(panel, dict) and isinstance(panel.get("placement"), dict)]
    section_panels = [panel for panel in panels if str(panel.get("section_id") or "") == section_id]
    if x1 is None and section_panels:
        spans = [_placement(panel) for panel in section_panels
                 if panel.get("type") in ("shelf", "door_front", "drawer_front")]
        if spans:
            x1 = max(min(span["x1"] for span in spans), 0.0)
            x2 = min(max(span["x2"] for span in spans),
                     max(_placement(panel)["x2"] for panel in panels))

    left = next((panel for panel in panels if panel.get("type") == "side_left"), None)
    right = next((panel for panel in panels if panel.get("type") == "side_right"), None)
    bottom = next((panel for panel in panels if panel.get("type") == "bottom"), None)
    top = next((panel for panel in panels if panel.get("type") == "top"), None)
    back = next((panel for panel in panels if panel.get("type") == "back"), None)
    if x1 is None and left is not None and right is not None:
        x1, x2 = _placement(left)["x2"], _placement(right)["x1"]
    if None in (x1, x2) or bottom is None or top is None:
        raise _Refusal("section_geometry_missing",
                       f"Не удалось определить конструктивные границы секции «{section_id}»",
                       field="section_id")

    analog = next((panel for panel in section_panels
                   if panel.get("type") in _SUPPORTED_TYPES), None)
    if analog is not None:
        analog_pl = _placement(analog)
        z1, z2 = analog_pl["z1"], analog_pl["z2"]
    else:
        all_pl = [_placement(panel) for panel in panels]
        z1 = min((_placement(left)["z1"] if left else min(p["z1"] for p in all_pl)),
                 _placement(bottom)["z1"])
        z2 = _placement(back)["z1"] if back is not None else max(p["z2"] for p in all_pl)
    return {"x1": float(x1), "x2": float(x2),
            "y1": _placement(bottom)["y2"], "y2": _placement(top)["y1"],
            "z1": float(z1), "z2": float(z2)}


def _check_orientation(panel: Mapping[str, Any], expected_type: str) -> None:
    actual_type = str(panel.get("type") or "")
    actual_orientation = str(panel.get("basis_orientation") or "").lower()
    if actual_type != expected_type or actual_orientation != _SUPPORTED_TYPES[expected_type]:
        raise _Refusal(
            "orientation_not_allowed",
            f"Деталь «{panel.get('name')}» имеет тип/orientation "
            f"{actual_type}/{actual_orientation}; ожидается "
            f"{expected_type}/{_SUPPORTED_TYPES[expected_type]}",
            field="panel_id",
        )


def _anchor(project: Mapping[str, Any], value: Any, *, field: str,
            section_id: str, expected_orientation: str) -> dict[str, Any]:
    panel_id = str(value or "").strip()
    if not panel_id:
        raise _Refusal("anchor_required", f"Привязка {field} пуста", field=field)
    panel = _find_panel(project, panel_id, field=field)
    orientation = str(panel.get("basis_orientation") or "").lower()
    if orientation != expected_orientation:
        raise _Refusal("anchor_orientation_invalid",
                       f"Привязка {field} должна указывать на деталь ориентации "
                       f"{expected_orientation}, получено {orientation or 'неизвестно'}",
                       field=field, details={"panel_id": panel_id})
    if panel.get("section_id") not in (None, section_id):
        raise _Refusal("anchor_section_mismatch",
                       f"Привязка «{panel_id}» находится в другой секции", field=field)
    return panel


def _between(operation: Mapping[str, Any]) -> tuple[Any, Any] | None:
    value = operation.get("between")
    if value is None:
        return None
    if isinstance(value, dict):
        value = [value.get("from") or value.get("left") or value.get("below"),
                 value.get("to") or value.get("right") or value.get("above")]
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise _Refusal("between_invalid", "between должен содержать ровно две привязки",
                       field="between")
    return value[0], value[1]


def _delta(operation: Mapping[str, Any]) -> float:
    value = operation.get("delta_mm", 0)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _Refusal("delta_invalid", "delta_mm должен быть числом в миллиметрах",
                       field="delta_mm")
    return float(value)


def _neighbor_gap(project: Mapping[str, Any], anchor: Mapping[str, Any], *, direction: str,
                  axis: str, section_id: str, exclude: Mapping[str, Any] | None) -> tuple[float, float]:
    anchor_pl = _placement(anchor)
    candidates = []
    for panel in project.get("panels", []):
        if panel is exclude or not isinstance(panel, dict):
            continue
        if str(panel.get("basis_orientation") or "").lower() != ("horizont" if axis == "y" else "vertical"):
            continue
        if panel.get("section_id") not in (None, section_id):
            continue
        pl = _placement(panel)
        if axis == "y" and _overlap(pl["x1"], pl["x2"], anchor_pl["x1"], anchor_pl["x2"]) <= 1:
            continue
        if axis == "x" and _overlap(pl["y1"], pl["y2"], anchor_pl["y1"], anchor_pl["y2"]) <= 1:
            continue
        if direction == "above" and pl[f"{axis}1"] >= anchor_pl[f"{axis}2"] - _TOL:
            candidates.append(pl)
        elif direction == "below" and pl[f"{axis}2"] <= anchor_pl[f"{axis}1"] + _TOL:
            candidates.append(pl)
    if not candidates:
        raise _Refusal("neighbor_not_found",
                       f"Для привязки {direction} не найден сосед по оси {axis}",
                       field=direction)
    if direction == "above":
        neighbor = min(candidates, key=lambda pl: pl[f"{axis}1"])
        return anchor_pl[f"{axis}2"], neighbor[f"{axis}1"]
    neighbor = max(candidates, key=lambda pl: pl[f"{axis}2"])
    return neighbor[f"{axis}2"], anchor_pl[f"{axis}1"]


def _shelf_placement(project: Mapping[str, Any], operation: Mapping[str, Any],
                     section_id: str, thickness: float,
                     target: Mapping[str, Any] | None) -> dict[str, float]:
    bounds = _section_bounds(project, section_id)
    if target is None:
        placement = dict(bounds)
    else:
        placement = _placement(target)

    pair = _between(operation)
    if pair is not None:
        lower = _anchor(project, pair[0], field="between", section_id=section_id,
                        expected_orientation="horizont")
        upper = _anchor(project, pair[1], field="between", section_id=section_id,
                        expected_orientation="horizont")
        low, high = _placement(lower)["y2"], _placement(upper)["y1"]
    elif operation.get("above") is not None:
        anchor = _anchor(project, operation.get("above"), field="above",
                         section_id=section_id, expected_orientation="horizont")
        low, high = _neighbor_gap(project, anchor, direction="above", axis="y",
                                  section_id=section_id, exclude=target)
    elif operation.get("below") is not None:
        anchor = _anchor(project, operation.get("below"), field="below",
                         section_id=section_id, expected_orientation="horizont")
        low, high = _neighbor_gap(project, anchor, direction="below", axis="y",
                                  section_id=section_id, exclude=target)
    elif operation.get("middle") is True:
        low, high = bounds["y1"], bounds["y2"]
    elif target is None:
        raise _Refusal("placement_binding_required",
                       "Для новой полки укажите between, above, below или middle",
                       field="middle")
    else:
        low = placement["y1"]
        high = placement["y2"]

    if pair is not None or operation.get("above") is not None \
            or operation.get("below") is not None or operation.get("middle") is True:
        if high - low < thickness - _TOL:
            raise _Refusal("anchor_gap_too_small", "Между привязками не помещается полка")
        placement["y1"] = (low + high - thickness) / 2
        placement["y2"] = placement["y1"] + thickness
    delta = _delta(operation)
    placement["y1"] += delta
    placement["y2"] += delta

    if target is None:
        placement["x1"], placement["x2"] = bounds["x1"], bounds["x2"]
    if operation.get("align_front", target is None) is True:
        placement["z1"] = bounds["z1"]
    if operation.get("align_back", target is None) is True:
        placement["z2"] = bounds["z2"]
    return {edge: _r(placement[edge]) for edge in _EDGES}


def _partition_placement(project: Mapping[str, Any], operation: Mapping[str, Any],
                         section_id: str, thickness: float,
                         target: Mapping[str, Any] | None) -> dict[str, float]:
    bounds = _section_bounds(project, section_id)
    placement = dict(bounds) if target is None else _placement(target)
    pair = _between(operation)
    if pair is not None:
        left = _anchor(project, pair[0], field="between", section_id=section_id,
                       expected_orientation="vertical")
        right = _anchor(project, pair[1], field="between", section_id=section_id,
                        expected_orientation="vertical")
        low, high = _placement(left)["x2"], _placement(right)["x1"]
    elif operation.get("middle") is True:
        low, high = bounds["x1"], bounds["x2"]
    elif target is None:
        raise _Refusal("placement_binding_required",
                       "Для новой перегородки укажите between или middle", field="middle")
    else:
        low, high = placement["x1"], placement["x2"]
    if pair is not None or operation.get("middle") is True:
        if high - low < thickness - _TOL:
            raise _Refusal("anchor_gap_too_small", "Между привязками не помещается перегородка")
        placement["x1"] = (low + high - thickness) / 2
        placement["x2"] = placement["x1"] + thickness
    delta = _delta(operation)
    placement["x1"] += delta
    placement["x2"] += delta

    if operation.get("above") is not None:
        lower = _anchor(project, operation.get("above"), field="above",
                        section_id=section_id, expected_orientation="horizont")
        placement["y1"] = _placement(lower)["y2"]
    elif target is None:
        placement["y1"] = bounds["y1"]
    if operation.get("below") is not None:
        upper = _anchor(project, operation.get("below"), field="below",
                        section_id=section_id, expected_orientation="horizont")
        placement["y2"] = _placement(upper)["y1"]
    elif target is None:
        placement["y2"] = bounds["y2"]
    if operation.get("align_front", target is None) is True:
        placement["z1"] = bounds["z1"]
    if operation.get("align_back", target is None) is True:
        placement["z2"] = bounds["z2"]
    return {edge: _r(placement[edge]) for edge in _EDGES}


def _within_product(project: Mapping[str, Any], placement: Mapping[str, float]) -> None:
    panels = [panel for panel in project.get("panels", [])
              if isinstance(panel, dict) and isinstance(panel.get("placement"), dict)]
    mins = {axis: min(_placement(panel)[f"{axis}1"] for panel in panels) for axis in "xyz"}
    maxs = {axis: max(_placement(panel)[f"{axis}2"] for panel in panels) for axis in "xyz"}
    for axis in "xyz":
        if placement[f"{axis}1"] < mins[axis] - _TOL or placement[f"{axis}2"] > maxs[axis] + _TOL:
            raise _Refusal("product_bounds_exceeded",
                           f"Деталь выходит за границы изделия по оси {axis.upper()}",
                           details={"axis": axis, "allowed": [mins[axis], maxs[axis]],
                                    "actual": [placement[f'{axis}1'], placement[f'{axis}2']]})
        if placement[f"{axis}2"] - placement[f"{axis}1"] <= 0:
            raise _Refusal("invalid_extent", f"Размер детали по оси {axis.upper()} не положительный")


def _butt_joint_ok(project: Mapping[str, Any], placement: Mapping[str, float],
                   panel_type: str, target: Mapping[str, Any] | None) -> bool:
    panels = [panel for panel in project.get("panels", [])
              if panel is not target and isinstance(panel, dict)
              and isinstance(panel.get("placement"), dict)]
    if panel_type == "shelf":
        yc = (placement["y1"] + placement["y2"]) / 2
        left = any(abs(_placement(panel)["x2"] - placement["x1"]) <= _TOL
                   and _placement(panel)["y1"] - _TOL <= yc <= _placement(panel)["y2"] + _TOL
                   and _overlap(_placement(panel)["z1"], _placement(panel)["z2"],
                                placement["z1"], placement["z2"]) > 20
                   for panel in panels if str(panel.get("basis_orientation")) == "vertical")
        right = any(abs(_placement(panel)["x1"] - placement["x2"]) <= _TOL
                    and _placement(panel)["y1"] - _TOL <= yc <= _placement(panel)["y2"] + _TOL
                    and _overlap(_placement(panel)["z1"], _placement(panel)["z2"],
                                 placement["z1"], placement["z2"]) > 20
                    for panel in panels if str(panel.get("basis_orientation")) == "vertical")
        return left and right
    xc = (placement["x1"] + placement["x2"]) / 2
    bottom = any(abs(_placement(panel)["y2"] - placement["y1"]) <= _TOL
                 and _placement(panel)["x1"] - _TOL <= xc <= _placement(panel)["x2"] + _TOL
                 and _overlap(_placement(panel)["z1"], _placement(panel)["z2"],
                              placement["z1"], placement["z2"]) > 20
                 for panel in panels if str(panel.get("basis_orientation")) == "horizont")
    top = any(abs(_placement(panel)["y1"] - placement["y2"]) <= _TOL
              and _placement(panel)["x1"] - _TOL <= xc <= _placement(panel)["x2"] + _TOL
              and _overlap(_placement(panel)["z1"], _placement(panel)["z2"],
                           placement["z1"], placement["z2"]) > 20
              for panel in panels if str(panel.get("basis_orientation")) == "horizont")
    return bottom and top


def _matching_overrides(spec: Mapping[str, Any], panel_id: str) -> list[dict[str, Any]]:
    return [override for override in (spec.get("overrides") or [])
            if isinstance(override, dict) and str(override.get("panel") or "") == panel_id]


def _quality_gates(spec: dict[str, Any]) -> tuple[dict[str, list[str]], dict[str, Any] | None]:
    from .paramspec import validate_paramspec

    issues: dict[str, list[str]] = {"schema": list(validate_paramspec(spec) or [])}
    if issues["schema"]:
        return issues, None
    try:
        from .generators import generate_from_paramspec
        project = generate_from_paramspec(spec)
    except Exception as error:
        issues["generator"] = [str(error)]
        return issues, None

    from .cfrn import check_cfrn_encoding, check_cfrn_holes
    from .completeness_check import check_completeness
    from .consistency_check import check_consistency
    from .drilling_check import check_drilling_geometry
    from .geometry_check import check_placement_geometry
    from .validate import validate_furniture

    issues["project_schema"] = list(validate_furniture(project) or [])
    issues["consistency"] = [f"{item.panel}: {item.message}"
                             for item in check_consistency(project)]
    geometry = check_placement_geometry(project)
    issues["geometry"] = ([] if geometry.get("ok", True)
                          else [str(item) for item in geometry.get("issues", [])])
    issues["completeness"] = list(check_completeness(project, spec) or [])
    try:
        issues["cfrn"] = list(check_cfrn_encoding(project) or [])
        issues["holes"] = list(check_cfrn_holes(project) or [])
    except Exception as error:
        issues["cfrn"] = [str(error)]
        issues["holes"] = []
    issues["drilling"] = list(check_drilling_geometry(project).get("errors") or [])
    return issues, project


def apply_geometry_operations(spec: Mapping[str, Any],
                              operations: Sequence[Mapping[str, Any]]) -> EditEngineResult:
    """Resolve semantic shelf/partition operations into atomic ParamSpec overrides."""
    candidate = copy.deepcopy(dict(spec))
    resolved: list[dict[str, Any]] = []
    if not isinstance(operations, (list, tuple)) or not operations:
        failure = EditFailure("operations_required", "Не переданы геометрические операции")
        return EditEngineResult(None, failure=failure)

    for index, raw_operation in enumerate(operations):
        try:
            if not isinstance(raw_operation, Mapping):
                raise _Refusal("operation_invalid", "Операция должна быть объектом")
            coordinate_fields = {"placement", "move", "x", "y", "z", *_EDGES}
            forbidden = sorted(coordinate_fields.intersection(raw_operation))
            if forbidden:
                raise _Refusal("coordinates_forbidden",
                               "Геометрическая операция не принимает координаты от LLM",
                               field=forbidden[0], details={"fields": forbidden})
            kind = _operation_kind(raw_operation)
            if kind not in _ADD_KINDS | _MOVE_KINDS:
                raise _Refusal("operation_not_supported",
                               f"EditEngine не поддерживает операцию «{kind or '(пусто)'}»",
                               field="kind")
            panel_id = _panel_id(raw_operation)
            if not panel_id:
                raise _Refusal("panel_id_required", "Нужен panel_id", field="panel_id")

            from .generators import generate_from_paramspec
            project = generate_from_paramspec(candidate)
            target = None if kind in _ADD_KINDS else _find_panel(
                project, panel_id, field="panel_id")
            panel_type = _panel_type(raw_operation) or str(target and target.get("type") or "")
            if panel_type not in _SUPPORTED_TYPES:
                raise _Refusal("orientation_not_allowed",
                               "Допустимы только shelf и vertical_partition",
                               field="panel_type", details={"panel_type": panel_type})
            if target is not None:
                _check_orientation(target, panel_type)
            section_id = _resolve_section_id(project, raw_operation.get("section_id"), target)

            existing = _matching_overrides(candidate, panel_id)
            added_override = next((override for override in existing
                                   if override.get("action") == "add"), None)
            if kind in _ADD_KINDS:
                if any(str(panel.get("name") or "") == panel_id
                       or str(panel.get("id") or "") == panel_id
                       for panel in project.get("panels", [])) or existing:
                    raise _Refusal("override_conflict",
                                   f"Деталь или пользовательский override «{panel_id}» уже существует",
                                   field="panel_id")
            elif existing and added_override is None:
                raise _Refusal("override_conflict",
                               f"У детали «{panel_id}» уже есть пользовательский override",
                               field="panel_id", details={"count": len(existing)})
            elif len(existing) > 1:
                raise _Refusal("override_conflict",
                               f"У детали «{panel_id}» несколько конфликтующих overrides",
                               field="panel_id", details={"count": len(existing)})

            thickness = float((candidate.get("materials") or {}).get("board_thickness") or 0)
            if thickness <= 0:
                raise _Refusal("thickness_missing", "Не задана толщина плиты корпуса")
            placement = (_shelf_placement(project, raw_operation, section_id, thickness, target)
                         if panel_type == "shelf"
                         else _partition_placement(project, raw_operation, section_id,
                                                   thickness, target))
            _within_product(project, placement)
            if not _butt_joint_ok(project, placement, panel_type, target):
                raise _Refusal("butt_joint_missing",
                               "Деталь не примыкает встык к двум несущим соседям",
                               details={"panel_id": panel_id, "placement": placement})

            if kind in _ADD_KINDS:
                override = {
                    "panel": panel_id,
                    "action": "add",
                    "type": panel_type,
                    "orientation": _SUPPORTED_TYPES[panel_type],
                    "thickness": _r(thickness),
                    "material": str((candidate.get("materials") or {}).get("board_material") or "ЛДСП"),
                    "placement": placement,
                }
                candidate.setdefault("overrides", []).append(override)
            elif added_override is not None:
                override = added_override
                override["placement"] = placement
                override["type"] = panel_type
                override["orientation"] = _SUPPORTED_TYPES[panel_type]
            else:
                override = {"panel": panel_id, "action": "transform", "placement": placement}
                candidate.setdefault("overrides", []).append(override)
            resolved.append(copy.deepcopy(override))
        except _Refusal as refusal:
            failure = EditFailure(refusal.code, refusal.message, index,
                                  refusal.field, refusal.details)
            return EditEngineResult(None, tuple(resolved), failure=failure)
        except Exception as error:
            failure = EditFailure("engine_error", str(error), index)
            return EditEngineResult(None, tuple(resolved), failure=failure)

    quality, _project = _quality_gates(candidate)
    failed = {name: messages for name, messages in quality.items() if messages}
    if failed:
        failure = EditFailure("quality_gate_failed",
                              "Геометрическая правка отклонена инженерными проверками",
                              len(operations) - 1, details={"issues": failed})
        return EditEngineResult(None, tuple(resolved), quality, failure)
    return EditEngineResult(candidate, tuple(resolved), quality)
