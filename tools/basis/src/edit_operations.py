"""Typed, deterministic patches for AI edits of ParamSpec v1.

The language model chooses high-level operations.  This module is the only
place that applies them to a ParamSpec; generators remain responsible for all
derived geometry.  Application is copy-on-write and therefore atomic.
"""

from __future__ import annotations

import copy
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, RootModel, TypeAdapter, model_validator

from .paramspec import Section, validate_paramspec


class _OperationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ValueEquals(_OperationModel):
    kind: Literal["value_equals"]
    path: str = Field(min_length=1)
    value: Any


class TargetExists(_OperationModel):
    kind: Literal["target_exists"]
    target_id: str = Field(min_length=1)


class TargetMissing(_OperationModel):
    kind: Literal["target_missing"]
    target_id: str = Field(min_length=1)


OperationPrecondition: TypeAlias = Annotated[
    ValueEquals | TargetExists | TargetMissing,
    Field(discriminator="kind"),
]


class _EditOperation(_OperationModel):
    target_id: str = Field(min_length=1)
    preconditions: list[OperationPrecondition] = Field(min_length=1)


DimensionName = Literal["width", "depth", "height", "depth_carcass", "tolerance"]
MaterialField = Literal[
    "board_thickness", "back_thickness", "board_material", "back_material",
    "facade_material", "edge_band_thickness", "color", "color_code",
    "facade_color", "facade_color_code", "board_article", "facade_article",
    "top_thickness", "texture_direction",
]
Archetype = Literal[
    "corpus", "shelving", "drawer_unit", "door_unit", "cabinet", "wardrobe",
    "desk", "table", "round_table", "composite",
]


class SetDimension(_EditOperation):
    op: Literal["SetDimension"]
    dimension: DimensionName
    value: float

    @model_validator(mode="after")
    def target_matches_dimension(self) -> "SetDimension":
        expected = f"dimensions.{self.dimension}"
        if self.target_id != expected:
            raise ValueError(f"target_id must be {expected!r}")
        return self


class SetMaterial(_EditOperation):
    op: Literal["SetMaterial"]
    field: MaterialField
    value: str | float | None

    @model_validator(mode="after")
    def target_matches_material(self) -> "SetMaterial":
        expected = f"materials.{self.field}"
        if self.target_id != expected:
            raise ValueError(f"target_id must be {expected!r}")
        return self


class ChangeArchetype(_EditOperation):
    op: Literal["ChangeArchetype"]
    archetype: Archetype

    @model_validator(mode="after")
    def target_is_archetype(self) -> "ChangeArchetype":
        if self.target_id != "archetype":
            raise ValueError("target_id must be 'archetype'")
        return self


class AddSection(_EditOperation):
    op: Literal["AddSection"]
    section: Section
    position: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def target_is_new_section_id(self) -> "AddSection":
        section_id = self.section.id
        if not section_id:
            raise ValueError("section.id is required for AddSection")
        if self.target_id not in (section_id, f"section:{section_id}"):
            raise ValueError("target_id must identify the new section.id")
        return self


class SectionPatch(_OperationModel):
    kind: Literal["shelves", "drawers", "door", "open"] | None = None
    width_share: float | None = Field(default=None, gt=0)
    shelves: int | None = Field(default=None, ge=0, le=20)
    shelf_levels: list[float] | None = None
    shelf_label: str | None = None
    drawers: int | None = Field(default=None, ge=0, le=20)
    drawer_heights: list[float] | None = None
    door: int | None = Field(default=None, ge=0, le=2)
    door_name: str | list[str] | None = None
    door_names: list[str] | None = None
    door_below_shelf: bool | None = None
    door_swing: Literal["left", "right", "up", "down"] | None = None
    door_inset: bool | None = None
    door_z: Literal["overlay", "front", "inset"] | None = None
    rod: bool | dict[str, Any] | None = None
    front_bottom: float | None = None
    front_top: float | None = None
    open_top: bool | None = None
    open_top_height: float | None = None
    cover_top: bool | None = None
    prefix: str | None = None
    niche_z_front: float | None = None
    guide_gap: float | None = Field(default=None, ge=0)
    guide_type: str | None = None
    box_z1: float | None = None
    box_depth: float | None = Field(default=None, gt=0)
    box_y_offset: float | None = None
    box_height: float | None = Field(default=None, gt=0)
    box_back_thickness: float | None = Field(default=None, gt=0)
    box_bottom_thickness: float | None = Field(default=None, gt=0)
    box_bottom_mode: Literal["between", "under"] | None = None
    box_back_mode: Literal["beyond", "inset"] | None = None
    box_sides_on_bottom: bool | None = None
    boxes: bool | None = None
    back_limit: float | None = None

    @model_validator(mode="after")
    def is_not_empty(self) -> "SectionPatch":
        if not self.model_fields_set:
            raise ValueError("changes must name at least one section field")
        return self


class UpdateSection(_EditOperation):
    op: Literal["UpdateSection"]
    changes: SectionPatch
    unset_fields: list[str] = Field(default_factory=list)


class DeleteSection(_EditOperation):
    op: Literal["DeleteSection"]


class AddShelf(_EditOperation):
    op: Literal["AddShelf"]
    level: float | None = None
    count: int = Field(default=1, ge=1, le=20)


class MovePart(_EditOperation):
    op: Literal["MovePart"]
    delta: tuple[float, float, float]


class PlacementPatch(_OperationModel):
    x1: float | None = None
    x2: float | None = None
    y1: float | None = None
    y2: float | None = None
    z1: float | None = None
    z2: float | None = None

    @model_validator(mode="after")
    def is_not_empty(self) -> "PlacementPatch":
        if not self.model_fields_set:
            raise ValueError("placement must name at least one edge")
        return self


class ResizePart(_EditOperation):
    op: Literal["ResizePart"]
    placement: PlacementPatch


class DeletePart(_EditOperation):
    op: Literal["DeletePart"]


class QueryModel(_EditOperation):
    op: Literal["QueryModel"]
    query: Literal["summary", "dimensions", "materials", "sections", "part", "cost"]


class DiagnoseModel(_EditOperation):
    op: Literal["DiagnoseModel"]
    scope: Literal["all", "schema", "consistency", "geometry", "cfrn", "holes", "drilling", "base"] = "all"


EditOperation: TypeAlias = Annotated[
    SetDimension | SetMaterial | ChangeArchetype | AddSection | UpdateSection
    | DeleteSection | AddShelf | MovePart | ResizePart | DeletePart
    | QueryModel | DiagnoseModel,
    Field(discriminator="op"),
]


class EditOperationBatch(RootModel[list[EditOperation]]):
    pass


_EDIT_OPERATIONS = TypeAdapter(list[EditOperation])
_MISSING = object()


class EditApplicationError(ValueError):
    """An operation batch failed; no caller-owned data was changed."""


def parse_edit_operations(data: Any) -> list[EditOperation]:
    return _EDIT_OPERATIONS.validate_python(data)


def edit_operation_json_schema() -> dict[str, Any]:
    return EditOperationBatch.model_json_schema(mode="validation")


def _path_value(spec: dict[str, Any], path: str) -> Any:
    current: Any = spec
    for part in path.split("."):
        if isinstance(current, list) and part.isdigit():
            index = int(part)
            if index >= len(current):
                return _MISSING
            current = current[index]
        elif isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return _MISSING
    return current


def _section_index(spec: dict[str, Any], target_id: str) -> int | None:
    sections = spec.get("sections") or []
    raw = target_id.removeprefix("section:")
    if target_id.startswith("sections.") and target_id[9:].isdigit():
        index = int(target_id[9:])
        return index if index < len(sections) else None
    if raw.isdigit() and int(raw) < len(sections):
        return int(raw)
    return next((i for i, section in enumerate(sections)
                 if str(section.get("id") or "") == raw), None)


def _panel_exists(spec: dict[str, Any], target_id: str, context: dict[str, Any]) -> bool:
    raw = target_id.removeprefix("part:")
    selected = context.get("selected_part") or {}
    state = str(selected.get("name") or "") == raw
    state = state or any(
        str(panel.get("name") or panel.get("n") or "") == raw
        for panel in context.get("panels") or []
    )
    for override in spec.get("overrides") or []:
        if str(override.get("panel") or "") != raw:
            continue
        if override.get("action") == "add":
            state = True
        elif override.get("action") == "delete":
            state = False
    return state


def _target_exists(spec: dict[str, Any], target_id: str, context: dict[str, Any]) -> bool:
    if target_id == "model":
        return True
    if target_id in {"archetype", "dimensions", "materials", "sections"}:
        return target_id in spec
    if _path_value(spec, target_id) is not _MISSING:
        return True
    if _section_index(spec, target_id) is not None:
        return True
    return _panel_exists(spec, target_id, context)


def _check_preconditions(spec: dict[str, Any], operation: _EditOperation,
                         context: dict[str, Any]) -> None:
    for condition in operation.preconditions:
        if isinstance(condition, ValueEquals):
            actual = _path_value(spec, condition.path)
            if actual is _MISSING or actual != condition.value:
                shown = "(missing)" if actual is _MISSING else repr(actual)
                raise EditApplicationError(
                    f"precondition failed: {condition.path} is {shown}, expected {condition.value!r}"
                )
        elif isinstance(condition, TargetExists):
            if not _target_exists(spec, condition.target_id, context):
                raise EditApplicationError(f"precondition failed: target {condition.target_id!r} is missing")
        elif isinstance(condition, TargetMissing):
            if _target_exists(spec, condition.target_id, context):
                raise EditApplicationError(f"precondition failed: target {condition.target_id!r} already exists")


def _set_path(spec: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    current: dict[str, Any] = spec
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = value


def _query_reply(spec: dict[str, Any], operation: QueryModel,
                 context: dict[str, Any]) -> str:
    dims = spec.get("dimensions") or {}
    materials = spec.get("materials") or {}
    if operation.query == "dimensions":
        return f"Габариты: {dims.get('width', '?')}×{dims.get('depth', '?')}×{dims.get('height', '?')} мм."
    if operation.query == "materials":
        return f"Материал: {materials.get('board_material') or 'не указан'}, декор: {materials.get('color') or 'не выбран'}."
    if operation.query == "sections":
        return f"Секций: {len(spec.get('sections') or [])}."
    if operation.query == "cost":
        total = context.get("estimate_total")
        return (f"Материалы по смете ≈ {total:,.0f} ₽ (закупка, без работы).".replace(",", " ")
                if isinstance(total, (int, float)) else "Смета ещё не посчитана.")
    if operation.query == "part":
        part = context.get("selected_part") or {}
        return f"Выбрана деталь «{part.get('name')}»." if part.get("name") else "Деталь не выбрана."
    return (f"{spec.get('project_name', 'Изделие')}: {spec.get('archetype', 'без архетипа')}, "
            f"{dims.get('width', '?')}×{dims.get('depth', '?')}×{dims.get('height', '?')} мм.")


def _diagnose_reply(operation: DiagnoseModel, context: dict[str, Any]) -> str:
    if operation.scope == "base":
        unresolved = context.get("base_unresolved")
        return ("Все позиции производственной базы подобраны." if not unresolved or unresolved == "все позиции подобраны"
                else f"Не подобраны позиции базы: {unresolved}.")
    errors = context.get("check_errors")
    if not errors or errors == "нет — все проверки зелёные":
        return "Все инженерные проверки зелёные."
    if isinstance(errors, dict) and operation.scope != "all":
        errors = {operation.scope: errors.get(operation.scope)} if errors.get(operation.scope) else {}
        if not errors:
            return f"Проверка «{operation.scope}» зелёная."
    return f"Диагностика: {errors}."


def apply_edit_operations(spec: dict[str, Any], operations: Any,
                          context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Apply a batch atomically and return the new spec plus read-only replies."""
    parsed = operations if all(isinstance(item, _EditOperation) for item in operations) \
        else parse_edit_operations(operations)
    original = copy.deepcopy(spec)
    working = copy.deepcopy(spec)
    ctx = context or {}
    replies: list[str] = []

    for index, operation in enumerate(parsed):
        try:
            _check_preconditions(working, operation, ctx)
            if isinstance(operation, SetDimension):
                _set_path(working, operation.target_id, operation.value)
            elif isinstance(operation, SetMaterial):
                _set_path(working, operation.target_id, operation.value)
            elif isinstance(operation, ChangeArchetype):
                working["archetype"] = operation.archetype
            elif isinstance(operation, AddSection):
                if _section_index(working, operation.target_id) is not None:
                    raise EditApplicationError(f"section target {operation.target_id!r} already exists")
                sections = working.setdefault("sections", [])
                section = operation.section.model_dump(mode="json", exclude_none=True, exclude_unset=True)
                position = len(sections) if operation.position is None else min(operation.position, len(sections))
                sections.insert(position, section)
            elif isinstance(operation, (UpdateSection, DeleteSection, AddShelf)):
                section_index = _section_index(working, operation.target_id)
                if section_index is None:
                    raise EditApplicationError(f"section target {operation.target_id!r} is missing")
                sections = working.get("sections") or []
                if isinstance(operation, DeleteSection):
                    sections.pop(section_index)
                elif isinstance(operation, UpdateSection):
                    patch = operation.changes.model_dump(mode="json", exclude_unset=True)
                    sections[section_index].update(patch)
                    allowed = set(SectionPatch.model_fields)
                    for field in operation.unset_fields:
                        if field not in allowed:
                            raise EditApplicationError(f"cannot unset unknown section field {field!r}")
                        sections[section_index].pop(field, None)
                else:
                    section = sections[section_index]
                    if operation.level is None:
                        section["shelves"] = int(section.get("shelves") or 0) + operation.count
                    else:
                        if section.get("shelves") and "shelf_levels" not in section:
                            raise EditApplicationError(
                                "cannot add an explicit shelf level without existing shelf_levels"
                            )
                        levels = list(section.get("shelf_levels") or [])
                        levels.extend([operation.level] * operation.count)
                        section["shelf_levels"] = sorted(levels)
                        section["shelves"] = len(levels)
            elif isinstance(operation, (MovePart, ResizePart, DeletePart)):
                if not _panel_exists(working, operation.target_id, ctx):
                    raise EditApplicationError(f"part target {operation.target_id!r} is missing")
                panel = operation.target_id.removeprefix("part:")
                override: dict[str, Any] = {"panel": panel}
                if isinstance(operation, MovePart):
                    override.update({"action": "transform", "move": list(operation.delta)})
                elif isinstance(operation, ResizePart):
                    override.update({"action": "transform", "placement":
                        operation.placement.model_dump(mode="json", exclude_unset=True)})
                else:
                    override["action"] = "delete"
                working.setdefault("overrides", []).append(override)
            elif isinstance(operation, QueryModel):
                replies.append(_query_reply(working, operation, ctx))
            elif isinstance(operation, DiagnoseModel):
                replies.append(_diagnose_reply(operation, ctx))
        except EditApplicationError as error:
            raise EditApplicationError(f"operation {index} ({operation.op}): {error}") from error

    errors = validate_paramspec(working)
    if errors:
        raise EditApplicationError("resulting ParamSpec is invalid: " + "; ".join(errors[:5]))
    return {
        "spec": working,
        "changed": working != original,
        "replies": replies,
        "operations": [item.model_dump(mode="json") for item in parsed],
    }
