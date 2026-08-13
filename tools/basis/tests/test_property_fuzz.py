"""MEB-150: bounded property tests for ParamSpec and typed edit batches."""

from __future__ import annotations

import copy
import json
import math

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st
from jsonschema import Draft202012Validator

from src.edit_operations import EditApplicationError, apply_edit_operations
from src.generators import generate_from_paramspec
from src.paramspec import paramspec_json_schema, parse_paramspec, validate_paramspec


PROPERTY_SETTINGS = settings(
    max_examples=50,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow],
)

BOUNDARY_DIMENSIONS = st.one_of(
    st.sampled_from([50, 51, 99, 100, 9_999, 10_000]),
    st.integers(min_value=50, max_value=10_000),
)


@st.composite
def section_strategy(draw):
    kind = draw(st.sampled_from(["shelves", "drawers", "door", "open"]))
    section = {
        "id": draw(st.from_regex(r"s[0-9]{1,3}", fullmatch=True)),
        "kind": kind,
        "width_share": draw(st.integers(min_value=1, max_value=100)) / 100,
    }
    if kind == "shelves":
        section["shelves"] = draw(st.integers(min_value=0, max_value=20))
        section["shelf_levels"] = draw(
            st.lists(st.integers(min_value=1, max_value=9_999), max_size=6, unique=True)
        )
    elif kind == "drawers":
        count = draw(st.integers(min_value=1, max_value=8))
        section.update({
            "drawers": count,
            "drawer_heights": draw(
                st.lists(st.integers(min_value=1, max_value=2_000), min_size=count, max_size=count)
            ),
            "box_depth": draw(st.integers(min_value=1, max_value=10_000)),
        })
    elif kind == "door":
        section["door"] = draw(st.integers(min_value=1, max_value=2))
        section["door_swing"] = draw(st.sampled_from(["left", "right", "up", "down"]))
    return section


@st.composite
def rich_non_composite_spec(draw):
    width = draw(BOUNDARY_DIMENSIONS)
    depth = draw(BOUNDARY_DIMENSIONS)
    height = draw(BOUNDARY_DIMENSIONS)
    thickness = draw(st.integers(min_value=1, max_value=min(width, depth, height)))
    sections = draw(st.lists(section_strategy(), max_size=4, unique_by=lambda item: item["id"]))
    gap = draw(st.integers(min_value=0, max_value=50))
    x1 = draw(st.integers(min_value=0, max_value=width - 1))
    x2 = draw(st.integers(min_value=x1 + 1, max_value=width))
    y1 = draw(st.integers(min_value=0, max_value=height - 1))
    y2 = draw(st.integers(min_value=y1 + 1, max_value=height))
    z1 = draw(st.integers(min_value=0, max_value=depth - 1))
    z2 = draw(st.integers(min_value=z1 + 1, max_value=depth))
    return {
        "schemaVersion": "paramspec-v1",
        "project_name": "MEB-150 generated",
        "archetype": draw(st.sampled_from([
            "corpus", "shelving", "drawer_unit", "door_unit", "cabinet",
            "wardrobe", "desk", "table", "round_table",
        ])),
        "dimensions": {"width": width, "depth": depth, "height": height},
        "materials": {"board_thickness": thickness, "back_thickness": 3},
        "gaps": {"default": gap, "facade": gap},
        "facade_reveal": gap,
        "sections": sections,
        "overrides": [{
            "panel": "Generated override",
            "action": "add",
            "type": "shelf",
            "orientation": "horizont",
            "thickness": y2 - y1,
            "placement": {"x1": x1, "x2": x2, "y1": y1, "y2": y2, "z1": z1, "z2": z2},
        }],
    }


@PROPERTY_SETTINGS
@given(rich_non_composite_spec())
def test_valid_paramspec_round_trip_is_stable_for_boundary_rich_payloads(raw):
    """Sections, drawer fields, overrides, gaps and boundary sizes survive both contracts."""
    assert not validate_paramspec(raw)
    typed = parse_paramspec(raw)
    serialized = json.loads(typed.model_dump_json(by_alias=True, exclude_none=True, exclude_unset=True))
    assert serialized == raw
    assert parse_paramspec(serialized).to_generator_dict() == raw
    assert not list(Draft202012Validator(paramspec_json_schema()).iter_errors(serialized))


@st.composite
def producible_drawer_spec(draw):
    count = draw(st.integers(min_value=1, max_value=5))
    heights = draw(
        st.lists(st.integers(min_value=60, max_value=260), min_size=count, max_size=count)
    )
    gap = draw(st.integers(min_value=0, max_value=5))
    thickness = draw(st.sampled_from([16, 18, 25]))
    height = sum(heights) + gap * (count - 1) + 2 * thickness + draw(
        st.integers(min_value=60, max_value=400)
    )
    depth = draw(st.integers(min_value=250, max_value=800))
    box_z1 = draw(st.integers(min_value=0, max_value=20))
    max_box_depth = depth - 3 - box_z1 - thickness
    return {
        "schemaVersion": "paramspec-v1",
        "project_name": "MEB-150 drawer fuzz",
        "archetype": "drawer_unit",
        "dimensions": {
            "width": draw(st.integers(min_value=350, max_value=1_200)),
            "depth": depth,
            "height": min(height, 10_000),
        },
        "materials": {"board_thickness": thickness, "back_thickness": 3},
        "gaps": {"default": gap, "facade": gap},
        "facade_reveal": draw(st.integers(min_value=0, max_value=8)),
        "sections": [{
            "id": "drawers",
            "kind": "drawers",
            "drawers": count,
            "drawer_heights": heights,
            "box_depth": draw(st.integers(min_value=50, max_value=max_box_depth)),
            "box_z1": box_z1,
        }],
    }


def _assert_panel_contract(panel):
    placement = panel["placement"]
    assert all(math.isfinite(float(placement[edge])) for edge in ("x1", "x2", "y1", "y2", "z1", "z2"))
    spans = {axis: placement[f"{axis}2"] - placement[f"{axis}1"] for axis in "xyz"}
    assert all(span > 0 for span in spans.values())
    width_axis, height_axis, thickness_axis = {
        "horizont": ("x", "z", "y"),
        "horizontal": ("x", "z", "y"),
        "vertical": ("z", "y", "x"),
        "front": ("x", "y", "z"),
    }[panel["basis_orientation"]]
    assert spans[width_axis] == pytest.approx(panel["dimensions"]["width"], abs=0.01)
    assert spans[height_axis] == pytest.approx(panel["dimensions"]["height"], abs=0.01)
    assert spans[thickness_axis] == pytest.approx(panel["thickness"], abs=0.01)


@PROPERTY_SETTINGS
@given(producible_drawer_spec())
def test_generated_drawers_keep_positive_consistent_placement_inside_product(spec):
    assert not validate_paramspec(spec)
    project = generate_from_paramspec(spec)
    width, depth, height = (spec["dimensions"][key] for key in ("width", "depth", "height"))
    back = spec["materials"]["back_thickness"]
    thickness = spec["materials"]["board_thickness"]
    for panel in project["panels"]:
        _assert_panel_contract(panel)
        placement = panel["placement"]
        assert 0 <= placement["x1"] < placement["x2"] <= width
        assert 0 <= placement["y1"] < placement["y2"] <= height
        assert -thickness <= placement["z1"] < placement["z2"] <= depth + back


@PROPERTY_SETTINGS
@given(
    origin=st.tuples(
        st.integers(min_value=-2_000, max_value=2_000),
        st.integers(min_value=-2_000, max_value=2_000),
        st.integers(min_value=-2_000, max_value=2_000),
    )
)
def test_composite_origin_is_a_pure_translation(origin):
    child = {
        "schemaVersion": "paramspec-v1",
        "project_name": "child",
        "archetype": "corpus",
        "dimensions": {"width": 600, "depth": 400, "height": 800},
        "materials": {"board_thickness": 16, "back_thickness": 3},
    }
    composite = {
        "schemaVersion": "paramspec-v1",
        "project_name": "composite",
        "archetype": "composite",
        "dimensions": {"width": 600, "depth": 400, "height": 800},
        "materials": {"board_thickness": 16, "back_thickness": 3},
        "blocks": [{
            "name": "block",
            "origin": dict(zip(("x", "y", "z"), origin)),
            "spec": child,
        }],
    }
    assert parse_paramspec(composite).to_generator_dict() == composite
    child_panels = generate_from_paramspec(child)["panels"]
    composite_panels = generate_from_paramspec(composite)["panels"]
    assert len(child_panels) == len(composite_panels)
    for child_panel, shifted_panel in zip(child_panels, composite_panels):
        _assert_panel_contract(shifted_panel)
        for axis, offset in zip("xyz", origin):
            assert shifted_panel["placement"][f"{axis}1"] == pytest.approx(
                child_panel["placement"][f"{axis}1"] + offset
            )
            assert shifted_panel["placement"][f"{axis}2"] == pytest.approx(
                child_panel["placement"][f"{axis}2"] + offset
            )


def _base_edit_spec():
    return {
        "schemaVersion": "paramspec-v1",
        "project_name": "protected",
        "archetype": "cabinet",
        "dimensions": {"width": 800, "depth": 500, "height": 1_000},
        "materials": {"board_thickness": 16, "color": "oak"},
        "catalog": {"creator_user_id": "owner-1", "responsible_user_id": "owner-2"},
        "sections": [{"id": "main", "kind": "shelves", "shelves": 2}],
    }


@st.composite
def idempotent_operation_batches(draw):
    operations = []
    for kind, value in draw(st.lists(
        st.one_of(
            st.tuples(st.just("width"), st.integers(min_value=100, max_value=10_000)),
            st.tuples(st.just("depth"), st.integers(min_value=100, max_value=10_000)),
            st.tuples(st.just("height"), st.integers(min_value=100, max_value=10_000)),
            st.tuples(st.just("color"), st.sampled_from(["oak", "white", "black"])),
            st.tuples(st.just("share"), st.integers(min_value=1, max_value=100)),
        ),
        min_size=1,
        max_size=12,
    )):
        if kind in {"width", "depth", "height"}:
            operations.append({
                "op": "SetDimension",
                "target_id": f"dimensions.{kind}",
                "preconditions": [{"kind": "target_exists", "target_id": f"dimensions.{kind}"}],
                "dimension": kind,
                "value": value,
            })
        elif kind == "color":
            operations.append({
                "op": "SetMaterial",
                "target_id": "materials.color",
                "preconditions": [{"kind": "target_exists", "target_id": "materials"}],
                "field": "color",
                "value": value,
            })
        else:
            operations.append({
                "op": "UpdateSection",
                "target_id": "section:main",
                "preconditions": [{"kind": "target_exists", "target_id": "section:main"}],
                "changes": {"width_share": value / 100},
            })
    return operations


@PROPERTY_SETTINGS
@given(idempotent_operation_batches())
def test_idempotent_edit_sequences_preserve_protected_fields_and_round_trip(operations):
    original = _base_edit_spec()
    first = apply_edit_operations(original, operations)
    second = apply_edit_operations(first["spec"], operations)
    assert original == _base_edit_spec()
    for protected in ("schemaVersion", "project_name", "archetype", "catalog"):
        assert first["spec"][protected] == original[protected]
    assert second["spec"] == first["spec"]
    assert second["changed"] is False
    serialized = json.loads(json.dumps(second["spec"], ensure_ascii=False))
    assert parse_paramspec(serialized).to_generator_dict() == serialized


@PROPERTY_SETTINGS
@given(
    good_value=st.integers(min_value=100, max_value=10_000),
    impossible_expected=st.integers(min_value=-10_000, max_value=-1),
)
def test_invalid_late_operation_never_mutates_revision(good_value, impossible_expected):
    original = _base_edit_spec()
    before = copy.deepcopy(original)
    operations = [
        {
            "op": "SetDimension",
            "target_id": "dimensions.width",
            "preconditions": [{"kind": "target_exists", "target_id": "dimensions.width"}],
            "dimension": "width",
            "value": good_value,
        },
        {
            "op": "SetDimension",
            "target_id": "dimensions.depth",
            "preconditions": [{
                "kind": "value_equals", "path": "dimensions.depth", "value": impossible_expected,
            }],
            "dimension": "depth",
            "value": 600,
        },
    ]
    with pytest.raises(EditApplicationError, match=r"operation 1 .*precondition failed"):
        apply_edit_operations(original, operations)
    assert original == before
