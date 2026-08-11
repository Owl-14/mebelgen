"""MEB-144: semantic panel edits resolve without coordinates from the LLM."""

from __future__ import annotations

import copy

from src.edit_engine import apply_geometry_operations
from src.generators import generate_from_paramspec


def _open_cabinet(*, shelves: list[int] | None = None) -> dict:
    section = {"id": "main", "kind": "open"}
    if shelves:
        section.update({"shelf_levels": shelves, "shelf_label": "Shelf"})
    return {
        "schemaVersion": "paramspec-v1",
        "project_name": "EditEngine fixture",
        "furniture_type": "cabinet",
        "archetype": "cabinet",
        "dimensions": {"width": 800, "depth": 450, "height": 900, "tolerance": 5},
        "materials": {
            "board_thickness": 16,
            "back_thickness": 3,
            "board_material": "board",
            "back_material": "back",
            "edge_band_thickness": 0.4,
        },
        "legs": {"type": "none", "height": 0},
        "gaps": {"default": 2, "facade": 2},
        "sections": [section],
    }


def _failed(result, code: str) -> None:
    assert not result.ok and result.spec is None
    assert result.failure is not None
    assert result.failure.code == code, result.failure.as_dict()


def test_add_shelf_in_section_middle_passes_all_quality_gates():
    spec = _open_cabinet()
    before = copy.deepcopy(spec)
    result = apply_geometry_operations(spec, [{
        "kind": "add_panel",
        "panel_type": "shelf",
        "panel_id": "User shelf",
        "section_id": "main",
        "middle": True,
        "align_front": True,
        "align_back": True,
    }])

    assert result.ok, result.failure
    assert spec == before  # pure interface
    assert all(not messages for messages in result.quality_gates.values())
    override = result.resolved_overrides[0]
    assert override["placement"] == {
        "x1": 16, "x2": 784, "y1": 442, "y2": 458, "z1": 0, "z2": 450,
    }
    project = generate_from_paramspec(result.spec)
    panel = next(panel for panel in project["panels"] if panel["name"] == "User shelf")
    assert panel["basis_orientation"] == "horizont" and panel["thickness"] == 16


def test_add_and_then_move_user_partition_uses_semantic_delta():
    first = apply_geometry_operations(_open_cabinet(), [{
        "kind": "add_panel",
        "panel_type": "vertical_partition",
        "panel_id": "User partition",
        "section_id": "main",
        "middle": True,
        "align_front": True,
        "align_back": True,
    }])
    assert first.ok

    moved = apply_geometry_operations(first.spec, [{
        "kind": "move_panel",
        "panel_type": "vertical_partition",
        "panel_id": "User partition",
        "section_id": "main",
        "middle": True,
        "delta_mm": 80,
    }])
    assert moved.ok, moved.failure
    overrides = [item for item in moved.spec["overrides"]
                 if item["panel"] == "User partition"]
    assert len(overrides) == 1
    assert overrides[0]["placement"]["x1"] == 472
    assert all(not messages for messages in moved.quality_gates.values())


def test_move_shelf_between_neighbors_is_resolved_from_current_project():
    result = apply_geometry_operations(_open_cabinet(shelves=[200, 400, 700]), [{
        "kind": "move_panel",
        "panel_type": "shelf",
        "panel_id": "Shelf 1",
        "section_id": "main",
        "between": ["Shelf 2", "Shelf 3"],
    }])

    assert result.ok, result.failure
    assert result.resolved_overrides[0]["placement"]["y1"] == 550
    assert all(not messages for messages in result.quality_gates.values())


def test_partition_above_and_below_resolves_but_refuses_unfastened_joint():
    result = apply_geometry_operations(_open_cabinet(shelves=[250, 600]), [{
        "kind": "add_panel",
        "panel_type": "vertical_partition",
        "panel_id": "Upper divider",
        "section_id": "main",
        "middle": True,
        "above": "Shelf 1",
        "below": "Shelf 2",
        "align_front": True,
        "align_back": True,
    }])

    _failed(result, "quality_gate_failed")
    placement = result.resolved_overrides[0]["placement"]
    assert placement["y1"] == 266 and placement["y2"] == 600
    assert any("Upper divider" in message
               for message in result.quality_gates["completeness"])


def test_structured_refusals_cover_targets_orientation_bounds_and_overrides():
    base = _open_cabinet(shelves=[250, 600])
    _failed(apply_geometry_operations(base, [{
        "kind": "move_panel", "panel_type": "shelf", "panel_id": "missing",
        "section_id": "main", "middle": True,
    }]), "target_not_found")

    _failed(apply_geometry_operations(base, [{
        "kind": "move_panel", "panel_type": "shelf", "panel_id": "Боковина левая",
        "section_id": "main", "middle": True,
    }]), "orientation_not_allowed")

    _failed(apply_geometry_operations(base, [{
        "kind": "add_panel", "panel_type": "shelf", "panel_id": "Outside",
        "section_id": "main", "middle": True, "delta_mm": 1000,
    }]), "product_bounds_exceeded")

    conflicted = copy.deepcopy(base)
    conflicted["overrides"] = [{"panel": "Shelf 1", "move": [0, 10, 0]}]
    _failed(apply_geometry_operations(conflicted, [{
        "kind": "move_panel", "panel_type": "shelf", "panel_id": "Shelf 1",
        "section_id": "main", "middle": True,
    }]), "override_conflict")


def test_coordinates_and_non_butt_partition_are_rejected_before_mutation():
    spec = _open_cabinet()
    _failed(apply_geometry_operations(spec, [{
        "kind": "add_panel", "panel_type": "shelf", "panel_id": "Raw",
        "section_id": "main", "middle": True,
        "placement": {"x1": 0},
    }]), "coordinates_forbidden")

    # A partition above an inexistent horizontal support cannot be guessed.
    _failed(apply_geometry_operations(spec, [{
        "kind": "add_panel", "panel_type": "vertical_partition",
        "panel_id": "Floating", "section_id": "main", "middle": True,
        "above": "missing",
    }]), "target_not_found")


def test_quality_gate_failure_is_structured_and_atomic():
    # A second shelf at the exact same anchored position passes semantic parsing,
    # then is rejected by the real geometry/consistency gates.
    spec = _open_cabinet(shelves=[442])
    result = apply_geometry_operations(spec, [{
        "kind": "add_panel", "panel_type": "shelf", "panel_id": "Collision",
        "section_id": "main", "middle": True,
    }])
    _failed(result, "quality_gate_failed")
    assert "issues" in result.failure.details
    assert "overrides" not in spec
