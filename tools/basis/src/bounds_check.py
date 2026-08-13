"""Model-envelope checks shared by production gate and engine matrix."""

from __future__ import annotations

from typing import Any


_OUTSIDE_ALLOWED_TYPES = frozenset(
    {"door_front", "drawer_front", "facade", "front_panel", "front", "handle", "hardware"}
)


def check_model_bounds(project: dict[str, Any], *, tolerance: float = 0.5) -> list[str]:
    """Return structural panels that leave the declared W×D×H envelope.

    Overlay facades and other explicitly decorative parts may sit outside the
    carcass envelope. Structural front-oriented panels such as the back remain
    checked; orientation alone is never an exemption.
    """

    overall = project.get("overall_dimensions")
    if not isinstance(overall, dict):
        return ["overall_dimensions отсутствует или не является объектом"]
    limits = {
        "x": overall.get("width"),
        "y": overall.get("height"),
        "z": overall.get("depth"),
    }
    if any(not isinstance(value, (int, float)) or value <= 0 for value in limits.values()):
        return ["overall_dimensions должен содержать положительные width/depth/height"]

    issues: list[str] = []
    for index, panel in enumerate(project.get("panels") or []):
        if not isinstance(panel, dict):
            continue
        panel_type = str(panel.get("type") or "").lower()
        if panel_type in _OUTSIDE_ALLOWED_TYPES:
            continue
        placement = panel.get("placement")
        if not isinstance(placement, dict):
            continue
        name = str(panel.get("name") or f"panel_{index}")
        for axis, limit in limits.items():
            low = placement.get(f"{axis}1")
            high = placement.get(f"{axis}2")
            if not isinstance(low, (int, float)) or not isinstance(high, (int, float)):
                issues.append(f"{name}: placement не содержит числовую ось {axis}")
                continue
            # Накладной задник конструктивно начинается на заднем габарите и
            # выступает наружу ровно на свою толщину; по X/Y он остаётся
            # структурной панелью и проверяется без исключений.
            overlay_back = panel_type == "back" and axis == "z" and low >= float(limit) - tolerance
            if not overlay_back and (low < -tolerance or high > float(limit) + tolerance):
                issues.append(f"{name}: {axis}=[{low:g},{high:g}] вне [0,{float(limit):g}]")
    return issues
