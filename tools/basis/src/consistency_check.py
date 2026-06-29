"""
Проверка внутренней согласованности модели (помимо пересечений из geometry_check).

Ловит классы ошибок, которые не видит проверка пересечений:
- placement-пролёт не совпадает с dimensions.width/height;
- толщина по нормали панели не совпадает с thickness;
- нулевой/отрицательный пролёт;
- position не равен минимуму placement;
- габарит из панелей не сходится с overall_dimensions с учётом ножек/цоколя
  (низ корпуса должен стоять на y = legs.height, верх — на overall.height).

Источник истины в этом конвейере — placement (по нему импортёр строит панель).
Поэтому dimensions трактуется как производная величина.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# orientation -> (ось ширины NewPanel, ось высоты NewPanel, ось толщины)
ORIENTATION_AXES: dict[str, tuple[str, str, str]] = {
    "horizont": ("x", "z", "y"),
    "horizontal": ("x", "z", "y"),
    "vertical": ("z", "y", "x"),
    "front": ("x", "y", "z"),
}

# Панели, у которых thickness по нормали проверять не нужно (накладные/декор),
# и которые могут законно выходить за габарит (свесы столешниц, вынесенные фасады).
_DECOR_TYPES = frozenset(
    {"door_front", "drawer_front", "facade", "front_panel", "front", "handle", "hardware"}
)

ERROR = "error"
WARN = "warn"


@dataclass
class ConsistencyIssue:
    severity: str
    panel: str
    code: str
    message: str


def _span(pl: dict[str, Any], axis: str) -> float:
    return float(pl[axis + "2"]) - float(pl[axis + "1"])


def _is_decor(panel: dict[str, Any]) -> bool:
    t = str(panel.get("type") or "").lower()
    if t in _DECOR_TYPES:
        return True
    return str(panel.get("basis_orientation") or "").lower() == "front"


def check_consistency(
    data: dict[str, Any],
    *,
    dim_tol: float = 0.5,
    thick_tol: float = 0.05,
    pos_tol: float = 0.05,
    carcass_tol: float = 0.5,
) -> list[ConsistencyIssue]:
    """Возвращает список несостыковок (пустой — если всё согласовано)."""
    issues: list[ConsistencyIssue] = []
    panels = data.get("panels", [])
    if not isinstance(panels, list):
        return issues

    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []

    for i, p in enumerate(panels):
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or f"panel_{i}")
        pl = p.get("placement")
        if not isinstance(pl, dict):
            # placement обязателен для импорта по координатам
            issues.append(
                ConsistencyIssue(WARN, name, "no_placement", "нет placement — панель не будет размещена по координатам")
            )
            continue

        orient = str(p.get("basis_orientation") or "").lower()
        axes = ORIENTATION_AXES.get(orient)
        if axes is None:
            issues.append(
                ConsistencyIssue(ERROR, name, "bad_orientation", f"неизвестная basis_orientation: {orient!r}")
            )
            continue

        w_axis, h_axis, t_axis = axes
        span_w = _span(pl, w_axis)
        span_h = _span(pl, h_axis)
        span_t = _span(pl, t_axis)

        if not _is_decor(p):
            xs += [float(pl["x1"]), float(pl["x2"])]
            ys += [float(pl["y1"]), float(pl["y2"])]
            zs += [float(pl["z1"]), float(pl["z2"])]

        if span_w <= 0 or span_h <= 0 or span_t <= 0:
            issues.append(
                ConsistencyIssue(
                    ERROR, name, "nonpositive_span",
                    f"нулевой/отрицательный пролёт: W={span_w}, H={span_h}, T={span_t}",
                )
            )

        thickness = p.get("thickness")
        if thickness is not None and span_t > 0 and abs(span_t - float(thickness)) > thick_tol:
            issues.append(
                ConsistencyIssue(
                    WARN, name, "thickness_mismatch",
                    f"толщина по нормали из placement={span_t:g} ≠ thickness={float(thickness):g}",
                )
            )

        dims = p.get("dimensions")
        if isinstance(dims, dict):
            dw = dims.get("width")
            dh = dims.get("height")
            if dw is not None and abs(span_w - float(dw)) > dim_tol:
                issues.append(
                    ConsistencyIssue(
                        WARN, name, "dim_width_mismatch",
                        f"placement.width={span_w:g} ≠ dimensions.width={float(dw):g}",
                    )
                )
            if dh is not None and abs(span_h - float(dh)) > dim_tol:
                issues.append(
                    ConsistencyIssue(
                        WARN, name, "dim_height_mismatch",
                        f"placement.height={span_h:g} ≠ dimensions.height={float(dh):g}",
                    )
                )

        pos = p.get("position")
        if isinstance(pos, dict):
            if (
                abs(float(pos.get("x", 0)) - float(pl["x1"])) > pos_tol
                or abs(float(pos.get("y", 0)) - float(pl["y1"])) > pos_tol
                or abs(float(pos.get("z", 0)) - float(pl["z1"])) > pos_tol
            ):
                issues.append(
                    ConsistencyIssue(
                        WARN, name, "position_mismatch",
                        "position не равен минимуму placement (x1,y1,z1)",
                    )
                )

    # Габарит корпуса (по структурным панелям) против overall_dimensions с учётом ножек.
    od = data.get("overall_dimensions")
    if xs and isinstance(od, dict):
        legs = (data.get("hardware") or {}).get("legs") or {}
        hleg = float(legs.get("height") or 0)
        bottom_y, top_y = min(ys), max(ys)
        H = od.get("height")
        if H is not None:
            if abs(top_y - float(H)) > carcass_tol:
                issues.append(
                    ConsistencyIssue(
                        WARN, "(габарит)", "top_below_overall",
                        f"верх корпуса y={top_y:g} ≠ overall.height={float(H):g}",
                    )
                )
            if abs(bottom_y - hleg) > carcass_tol:
                issues.append(
                    ConsistencyIssue(
                        WARN, "(габарит)", "bottom_vs_legs",
                        f"низ корпуса y={bottom_y:g} ≠ legs.height={hleg:g} "
                        f"(под корпусом {bottom_y:g} мм, заявленный цоколь/ножки {hleg:g} мм)",
                    )
                )

    return issues


def format_consistency_report(issues: list[ConsistencyIssue]) -> str:
    if not issues:
        return "Несостыковок не обнаружено."
    n_err = sum(1 for i in issues if i.severity == ERROR)
    lines = [f"Несостыковки: {len(issues)} (ошибок: {n_err}):", ""]
    for it in issues:
        mark = "✗" if it.severity == ERROR else "•"
        lines.append(f"  {mark} [{it.code}] {it.panel}: {it.message}")
    return "\n".join(lines)


def has_errors(issues: list[ConsistencyIssue]) -> bool:
    return any(i.severity == ERROR for i in issues)
