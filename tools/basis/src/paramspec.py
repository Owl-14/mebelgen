"""Typed ParamSpec v1 contract and Studio-friendly validation errors.

The Pydantic models in this module are the source of truth.  The checked-in
``schema/paramspec.schema.json`` is generated from :class:`ParamSpec` for LLM
providers and other JSON-Schema consumers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    RootModel,
    Tag,
    ValidationError,
)

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "paramspec.schema.json"


class _ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Dimensions(_ContractModel):
    width: float = Field(ge=50, le=10_000)
    depth: float = Field(ge=50, le=10_000)
    height: float = Field(ge=50, le=10_000)
    depth_carcass: float | None = Field(default=None, gt=0)
    tolerance: float | None = None


class Materials(_ContractModel):
    board_thickness: float = Field(gt=0)
    back_thickness: float | None = Field(default=None, gt=0)
    board_material: str | None = None
    back_material: str | None = None
    facade_material: str | None = None
    edge_band_thickness: float | None = Field(default=None, ge=0)
    color: str | None = None
    color_code: str | None = None
    facade_color: str | None = None
    facade_color_code: str | None = None
    board_article: str | None = None
    facade_article: str | None = None
    top_thickness: float | None = Field(default=None, ge=3, le=60)
    texture_direction: Literal["along", "across"] | None = None


class Legs(_ContractModel):
    type: str | None = None
    height: float | None = Field(default=None, ge=0)
    adjustable: bool | None = None
    color: str | None = None
    count: int | None = Field(default=None, ge=0)
    as_panel: bool | None = None


class Gaps(_ContractModel):
    facade: float | None = Field(default=None, ge=0)
    default: float | None = Field(default=None, ge=0)


class Handles(_ContractModel):
    type: str | None = None
    material: str | None = None
    color: str | None = None
    size: float | None = Field(default=None, ge=0)
    count: int | None = Field(default=None, ge=0)
    offset_from_top: float | None = Field(default=None, ge=0)
    furniture_encoded: str | None = None


class CatalogMetadata(_ContractModel):
    """Strict server-owned catalog identity stored with a ParamSpec."""

    creator_user_id: str | None = None
    responsible_user_id: str | None = None
    author: str | None = None
    responsible: str | None = None


class DrawerGuides(_ContractModel):
    type: str | None = None
    length_mm: float | None = Field(default=None, gt=0)
    soft_close: bool | None = None
    with_closer: bool | None = Field(
        default=None,
        json_schema_extra={"deprecated": True},
        description="Legacy alias for soft_close; normalized before generation.",
    )
    furniture_encoded: str | None = None
    cabinet_left_panel: str | None = None
    cabinet_right_panel: str | None = None


class Hinges(_ContractModel):
    type: str | None = None
    color: str | None = None
    adjustable: bool | None = None
    furniture_encoded: str | None = None


class Lock(_ContractModel):
    type: str | None = None
    color: str | None = None
    target: str | None = None
    furniture_encoded: str | None = None


class Hardware(_ContractModel):
    handles: Handles | None = None
    drawer_guides: DrawerGuides | None = None
    hinges: Hinges | None = None
    locks: list[Lock] | None = None
    selection: dict[str, str] | None = None


class Rod(_ContractModel):
    axis: Literal["x", "z"] | None = None
    height: float | None = None
    diameter: float | None = Field(default=None, gt=0)
    length: float | None = Field(default=None, gt=0)


class _SectionFields(_ContractModel):
    id: str | None = None
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
    rod: bool | Rod | None = None
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


class ShelvesSection(_SectionFields):
    kind: Literal["shelves"]


class DrawersSection(_SectionFields):
    kind: Literal["drawers"]


class DoorSection(_SectionFields):
    kind: Literal["door"]


class OpenSection(_SectionFields):
    kind: Literal["open"]


Section: TypeAlias = Annotated[
    ShelvesSection | DrawersSection | DoorSection | OpenSection,
    Field(discriminator="kind"),
]


class Placement(_ContractModel):
    x1: float | None = None
    x2: float | None = None
    y1: float | None = None
    y2: float | None = None
    z1: float | None = None
    z2: float | None = None


class Override(_ContractModel):
    panel: str
    action: Literal["transform", "delete", "rename", "add"] | None = None
    placement: Placement | None = None
    move: tuple[float, float, float] | None = None
    to: str | None = None
    type: str | None = None
    orientation: str | None = None
    thickness: float | None = Field(default=None, gt=0)
    material: str | None = None


class _ParamSpecFields(_ContractModel):
    schema_version: Literal["paramspec-v1"] = Field(alias="schemaVersion")
    project_name: str = Field(min_length=1)
    furniture_type: str | None = None
    dimensions: Dimensions
    materials: Materials
    legs: Legs | None = None
    gaps: Gaps | None = None
    sections: list[Section] | None = None
    hardware: Hardware | None = None
    features: list[str] | None = None
    warnings: list[str] | None = None
    estimated_values: list[str] | None = None
    catalog: CatalogMetadata | None = None
    overrides: list[Override] | None = None
    draft: bool | None = None
    created: bool | None = None
    back_mount: Literal["inset", "overlay"] | None = None
    sides_over_top: bool | None = None
    rod: bool | Rod | None = None
    interior_z_front: float | None = None
    carcass_z_front: float | None = None
    top_overhang: tuple[float, float] | None = None
    socle_full: bool | None = None
    socle_recess: float | None = Field(default=None, ge=0)
    facade_reveal: float | None = Field(default=None, ge=0)
    apron: bool | None = None
    apron_height: float | None = Field(default=None, gt=0)
    frame: str | None = None
    screen: bool | None = None
    screen_height: float | None = Field(default=None, gt=0)
    screen_thickness: float | None = Field(default=None, gt=0)
    screen_margin: float | None = Field(default=None, ge=0)
    screen_z: float | None = None
    top_thickness: float | None = Field(default=None, gt=0)
    pedestal_diameter: float | None = Field(default=None, gt=0)
    base: bool | None = None
    base_thickness: float | None = Field(default=None, gt=0)
    base_diameter: float | None = Field(default=None, gt=0)


class CorpusParamSpec(_ParamSpecFields):
    archetype: Literal["corpus"]


class ShelvingParamSpec(_ParamSpecFields):
    archetype: Literal["shelving"]


class DrawerUnitParamSpec(_ParamSpecFields):
    archetype: Literal["drawer_unit"]


class DoorUnitParamSpec(_ParamSpecFields):
    archetype: Literal["door_unit"]


class CabinetParamSpec(_ParamSpecFields):
    archetype: Literal["cabinet", "wardrobe"]


class DeskParamSpec(_ParamSpecFields):
    archetype: Literal["desk", "table"]


class RoundTableParamSpec(_ParamSpecFields):
    archetype: Literal["round_table"]


class LegacyDraftParamSpec(_ContractModel):
    """Persisted placeholder created before archetype selection.

    Studio saves this exact minimal shape for a newly created product.  It is a
    lifecycle placeholder rather than a generatable furniture specification,
    so dimensions, materials and an archetype are intentionally absent.
    """

    schema_version: Literal["paramspec-v1"] = Field(alias="schemaVersion")
    project_name: str = Field(min_length=1)
    draft: Literal[True]
    catalog: CatalogMetadata | None = None


NonCompositeParamSpec: TypeAlias = Annotated[
    CorpusParamSpec
    | ShelvingParamSpec
    | DrawerUnitParamSpec
    | DoorUnitParamSpec
    | CabinetParamSpec
    | DeskParamSpec
    | RoundTableParamSpec,
    Field(discriminator="archetype"),
]


class Origin(_ContractModel):
    x: float = 0
    y: float = 0
    z: float = 0


class CompositeBlock(_ContractModel):
    name: str | None = None
    origin: Origin | None = None
    spec: NonCompositeParamSpec


class CompositeParamSpec(_ParamSpecFields):
    archetype: Literal["composite"]
    blocks: list[CompositeBlock]


_LEGACY_DRAFT_TAG = "__legacy_draft__"


def _paramspec_tag(value: Any) -> str | None:
    """Choose a union branch without inventing fields in persisted drafts."""
    if isinstance(value, dict):
        archetype = value.get("archetype")
        if archetype is None and value.get("draft") is True:
            return _LEGACY_DRAFT_TAG
        return archetype if isinstance(archetype, str) else None
    if isinstance(value, LegacyDraftParamSpec):
        return _LEGACY_DRAFT_TAG
    archetype = getattr(value, "archetype", None)
    return archetype if isinstance(archetype, str) else None


ParamSpecValue: TypeAlias = Annotated[
    Annotated[CorpusParamSpec, Tag("corpus")]
    | Annotated[ShelvingParamSpec, Tag("shelving")]
    | Annotated[DrawerUnitParamSpec, Tag("drawer_unit")]
    | Annotated[DoorUnitParamSpec, Tag("door_unit")]
    | Annotated[CabinetParamSpec, Tag("cabinet")]
    | Annotated[CabinetParamSpec, Tag("wardrobe")]
    | Annotated[DeskParamSpec, Tag("desk")]
    | Annotated[DeskParamSpec, Tag("table")]
    | Annotated[RoundTableParamSpec, Tag("round_table")]
    | Annotated[CompositeParamSpec, Tag("composite")]
    | Annotated[LegacyDraftParamSpec, Tag(_LEGACY_DRAFT_TAG)],
    Discriminator(_paramspec_tag),
]


class ParamSpec(RootModel[ParamSpecValue]):
    """Top-level paramspec-v1 model, discriminated by ``archetype``."""

    def to_generator_dict(self) -> dict[str, Any]:
        """Return the plain dict shape consumed by the existing generators."""
        return self.model_dump(
            mode="json", by_alias=True, exclude_none=True, exclude_unset=True
        )


_ARCHETYPE_TAGS = {
    "corpus", "shelving", "drawer_unit", "door_unit", "cabinet", "wardrobe",
    "desk", "table", "round_table", "composite",
}
_SECTION_TAGS = {"shelves", "drawers", "door", "open"}

_PARAMSPEC_DEFINITIONS = (
    "CorpusParamSpec", "ShelvingParamSpec", "DrawerUnitParamSpec",
    "DoorUnitParamSpec", "CabinetParamSpec", "DeskParamSpec",
    "RoundTableParamSpec", "CompositeParamSpec",
)


def _format_errors(exc: ValidationError) -> list[str]:
    errors: list[str] = []
    for detail in exc.errors(include_url=False):
        loc = [str(part) for part in detail["loc"] if part != "root"]
        if loc and loc[0] in _ARCHETYPE_TAGS:
            loc.pop(0)
        loc = [
            part for index, part in enumerate(loc)
            if not (
                part in _SECTION_TAGS
                and index > 0
                and loc[index - 1].isdigit()
            )
        ]
        path = ".".join(loc) or "(root)"
        errors.append(f"{path}: {detail['msg']} [{detail['type']}]")
    return errors


def parse_paramspec(data: Any) -> ParamSpec:
    """Validate untrusted AI/API input and return its typed representation."""
    return ParamSpec.model_validate(data)


def validate_paramspec(data: Any, schema_path: Path | None = None) -> list[str]:
    """Return Studio-ready validation errors (empty means valid).

    ``schema_path`` remains supported for callers/tests that explicitly supply
    an alternate JSON Schema.  Normal validation always uses the typed model.
    """
    if schema_path is not None:
        from jsonschema import Draft202012Validator

        validator = Draft202012Validator(load_schema(schema_path))
        errors: list[str] = []
        for err in sorted(validator.iter_errors(data), key=lambda item: list(item.path)):
            path = ".".join(str(part) for part in err.path) or "(root)"
            errors.append(f"{path}: {err.message}")
        return errors
    try:
        parse_paramspec(data)
    except ValidationError as exc:
        return _format_errors(exc)
    return []


def validate_paramspec_file(path: str | Path, schema_path: Path | None = None) -> list[str]:
    with Path(path).open(encoding="utf-8") as file:
        return validate_paramspec(json.load(file), schema_path)


def paramspec_json_schema() -> dict[str, Any]:
    """Generate the canonical JSON Schema used by AI and API clients."""
    schema = ParamSpec.model_json_schema(by_alias=True, mode="validation")
    definitions = schema["$defs"]

    # Pydantic deliberately expands inherited fields into every union branch.
    # ParamSpec has a large common surface, so that raw form is needlessly
    # expensive when embedded in AI prompts.  Factor identical model-derived
    # fields into one Draft 2020-12 definition without changing semantics.
    corpus = definitions["CorpusParamSpec"]
    common_properties = {
        key: value for key, value in corpus["properties"].items()
        if key != "archetype"
    }
    common_required = [
        key for key in corpus.get("required", []) if key != "archetype"
    ]
    definitions["ParamSpecFields"] = {
        "properties": common_properties,
        "required": common_required,
        "type": "object",
    }
    for name in _PARAMSPEC_DEFINITIONS:
        expanded = definitions[name]
        variant_properties = {"archetype": expanded["properties"]["archetype"]}
        variant_required = ["archetype"]
        if name == "CompositeParamSpec":
            variant_properties["blocks"] = expanded["properties"]["blocks"]
            variant_required.append("blocks")
        definitions[name] = {
            "allOf": [{"$ref": "#/$defs/ParamSpecFields"}],
            "properties": variant_properties,
            "required": variant_required,
            "title": expanded["title"],
            "type": "object",
            "unevaluatedProperties": False,
        }
    schema.update({
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://bazis-converter.local/schema/paramspec.schema.json",
        "title": "BasisParamSpec",
        "description": (
            "Высокоуровневое описание изделия параметрами (без координат). "
            "Сгенерировано из Pydantic-моделей src.paramspec."
        ),
        # Callable discriminators let Pydantic accept the historical draft
        # placeholder without injecting an archetype into persisted JSON.  Keep
        # the standard discriminator hint for LLM/API consumers of the schema.
        "discriminator": {"propertyName": "archetype"},
    })
    return schema


def load_schema(path: Path | None = None) -> dict[str, Any]:
    """Load a JSON-Schema artifact; defaults to the checked-in generated copy."""
    with (path or SCHEMA_PATH).open(encoding="utf-8") as file:
        return json.load(file)
