"""
Проверка пересечений панелей по placement (AABB в ГСК).

Правила разрешения (см. RULES.md §6):
- горизонтальные пересечения: допускается разделение на 2 части и стыковка по X (или Z при необходимости);
- вертикальные пересечения: приоритет — не дробить; фиксировать в отчёте для ручной правки/объединения.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class PanelBox:
    name: str
    panel_type: str
    basis_orientation: str
    x1: float
    x2: float
    y1: float
    y2: float
    z1: float
    z2: float
    structural: bool = True

    @property
    def volume(self) -> float:
        return max(0.0, (self.x2 - self.x1) * (self.y2 - self.y1) * (self.z2 - self.z1))

    def intersects(self, other: PanelBox, eps: float = 0.01) -> bool:
        return (
            self.x1 < other.x2 - eps
            and self.x2 > other.x1 + eps
            and self.y1 < other.y2 - eps
            and self.y2 > other.y1 + eps
            and self.z1 < other.z2 - eps
            and self.z2 > other.z1 + eps
        )

    def intersection_volume(self, other: PanelBox) -> float:
        ix1 = max(self.x1, other.x1)
        iy1 = max(self.y1, other.y1)
        iz1 = max(self.z1, other.z1)
        ix2 = min(self.x2, other.x2)
        iy2 = min(self.y2, other.y2)
        iz2 = min(self.z2, other.z2)
        if ix1 >= ix2 or iy1 >= iy2 or iz1 >= iz2:
            return 0.0
        return (ix2 - ix1) * (iy2 - iy1) * (iz2 - iz1)


@dataclass
class OverlapIssue:
    panel_a: str
    panel_b: str
    orientation_a: str
    orientation_b: str
    intersection_volume_mm3: float
    suggested_action: str
    detail: str


DECORATIVE_TYPES = frozenset(
    {
        "door_front",
        "drawer_front",
        "facade",
        "front_panel",
        "front",
        "handle",
        "hardware",
    }
)

HORIZONT = frozenset({"horizont", "horizontal"})
VERTICAL = frozenset({"vertical", "front", "back"})
# Полку режут только настоящие стойки/перегородки. Фасады (front) и задники
# не являются препятствием для полки — иначе полка дробится по фальш-границам.
PARTITION_ORIENTATION = frozenset({"vertical"})


def is_structural_panel(p: dict[str, Any]) -> bool:
    """Корпус/полки/стойки — проверяем; фасады и фурнитура — нет."""
    if not p.get("placement"):
        return False
    t = (p.get("type") or "").lower()
    if t in DECORATIVE_TYPES:
        return False
    orient = (p.get("basis_orientation") or "").lower()
    if orient == "front":
        return False
    return True


def panel_from_dict(p: dict[str, Any]) -> PanelBox | None:
    pl = p.get("placement")
    if not pl:
        return None
    return PanelBox(
        name=p.get("name", "panel"),
        panel_type=(p.get("type") or "").lower(),
        basis_orientation=(p.get("basis_orientation") or "").lower(),
        x1=float(pl["x1"]),
        x2=float(pl["x2"]),
        y1=float(pl["y1"]),
        y2=float(pl["y2"]),
        z1=float(pl["z1"]),
        z2=float(pl["z2"]),
        structural=is_structural_panel(p),
    )


def collect_panels(data: dict[str, Any]) -> list[PanelBox]:
    out: list[PanelBox] = []
    for p in data.get("panels", []):
        box = panel_from_dict(p)
        if box:
            out.append(box)
    return out


def find_overlaps(
    panels: list[PanelBox],
    min_volume: float = 1.0,
    *,
    structural_only: bool = True,
) -> list[OverlapIssue]:
    issues: list[OverlapIssue] = []
    n = len(panels)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = panels[i], panels[j]
            if structural_only and (not a.structural or not b.structural):
                continue
            if not a.intersects(b):
                continue
            vol = a.intersection_volume(b)
            if vol < min_volume:
                continue
            if _is_allowed_corner_wrap(a, b, vol):
                continue

            oa, ob = a.basis_orientation, b.basis_orientation
            action, detail = _suggest_action(oa, ob, a, b, vol)
            issues.append(
                OverlapIssue(
                    panel_a=a.name,
                    panel_b=b.name,
                    orientation_a=oa,
                    orientation_b=ob,
                    intersection_volume_mm3=round(vol, 1),
                    suggested_action=action,
                    detail=detail,
                )
            )
    return issues


def _is_allowed_corner_wrap(a: PanelBox, b: PanelBox, vol: float) -> bool:
    """
    Допустимые стыки корпуса: задник (полоса по Z), угол «X+Z» (L-мм),
    не полноценное прохождение полки через стойку.
    """
    ix1 = max(a.x1, b.x1)
    iy1 = max(a.y1, b.y1)
    iz1 = max(a.z1, b.z1)
    ix2 = min(a.x2, b.x2)
    iy2 = min(a.y2, b.y2)
    iz2 = min(a.z2, b.z2)
    dx, dy, dz = ix2 - ix1, iy2 - iy1, iz2 - iz1
    thin = 20.0
    if a.panel_type == "back" or b.panel_type == "back":
        if dz <= thin:
            return True
    if dx <= thin and dz <= thin:
        return True
    return False


def _suggest_action(oa: str, ob: str, a: PanelBox, b: PanelBox, vol: float) -> tuple[str, str]:
    if oa in HORIZONT and ob in HORIZONT:
        # Две горизонтали в одном ярусе Y — типичный конфликт полки/дна
        if abs(a.y1 - b.y1) < 1.0 and abs(a.y2 - b.y2) < 1.0:
            span_x = min(a.x2, b.x2) - max(a.x1, b.x1)
            span_z = min(a.z2, b.z2) - max(a.z1, b.z1)
            if span_x >= span_z:
                return (
                    "split_horizontal_along_x",
                    f"Разделить одну из панелей по X на 2 части и состыковать по X (общий Y и Z). Объём пересечения ~{vol:.0f} мм³.",
                )
            return (
                "split_horizontal_along_z",
                f"Рассмотреть разделение горизонтали по Z на 2 части с общим Y/X (стыковка по Z). Объём ~{vol:.0f} мм³.",
            )

    if oa in VERTICAL or ob in VERTICAL:
        return (
            "adjust_vertical_or_merge",
            f"Вертикальные панели не дробим автоматически. Сместить одну из панелей или объединить в ТЗ. Объём ~{vol:.0f} мм³.",
        )

    return (
        "review_overlap",
        f"Пересечение {oa} и {ob}. Проверить вручную по чертежу. Объём ~{vol:.0f} мм³.",
    )


def check_placement_geometry(
    data: dict[str, Any],
    min_volume: float = 1.0,
    *,
    structural_only: bool = True,
) -> dict[str, Any]:
    panels = collect_panels(data)
    overlaps = find_overlaps(panels, min_volume=min_volume, structural_only=structural_only)

    by_orientation: dict[str, int] = {}
    for p in panels:
        by_orientation[p.basis_orientation] = by_orientation.get(p.basis_orientation, 0) + 1

    return {
        "panel_count": len(panels),
        "overlap_count": len(overlaps),
        "overlaps": [
            {
                "panel_a": o.panel_a,
                "panel_b": o.panel_b,
                "orientation_a": o.orientation_a,
                "orientation_b": o.orientation_b,
                "intersection_volume_mm3": o.intersection_volume_mm3,
                "suggested_action": o.suggested_action,
                "detail": o.detail,
            }
            for o in overlaps
        ],
        "by_orientation": by_orientation,
        "ok": len(overlaps) == 0,
        "structural_only": structural_only,
    }


def _same_y_band(a: PanelBox, b: PanelBox, eps: float = 0.5) -> bool:
    """Горизонталь и вертикаль «в одном ярусе» по Y (полка проходит через стойку)."""
    return a.y1 < b.y2 - eps and a.y2 > b.y1 + eps


def _cut_intervals(x1: float, x2: float, cuts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Разбить [x1,x2] на отрезки, вырезая зоны cuts (x_lo, x_hi)."""
    segments = [(x1, x2)]
    for cx1, cx2 in sorted(cuts, key=lambda c: c[0]):
        next_seg: list[tuple[float, float]] = []
        for s1, s2 in segments:
            if cx2 <= s1 or cx1 >= s2:
                next_seg.append((s1, s2))
                continue
            if s1 < cx1:
                next_seg.append((s1, cx1))
            if cx2 < s2:
                next_seg.append((cx2, s2))
        segments = [(a, b) for a, b in next_seg if b - a > 0.01]
    return segments


def _update_panel_placement(panel: dict[str, Any], x1: float, x2: float) -> None:
    pl = panel.setdefault("placement", {})
    pl["x1"] = round(x1, 2)
    pl["x2"] = round(x2, 2)
    pos = panel.setdefault("position", {})
    pos["x"] = pl["x1"]
    dims = panel.get("dimensions")
    if isinstance(dims, dict):
        dims["width"] = round(x2 - x1, 2)


def resolve_horizontal_splits(
    data: dict[str, Any],
    *,
    min_volume: float = 1.0,
) -> tuple[dict[str, Any], list[str]]:
    """
    Горизонталь, пересекающая вертикаль в том же ярусе Y,
    делится на части по границам стойки (стыковка по X, зазор = толщина стойки).
    Вертикали не дробятся.
    """
    result = copy.deepcopy(data)
    log: list[str] = []
    panels_raw = result.get("panels", [])
    boxes = [(i, panel_from_dict(p)) for i, p in enumerate(panels_raw)]
    vert_boxes = [b for _, b in boxes if b and b.basis_orientation in PARTITION_ORIENTATION]

    out: list[dict[str, Any]] = []
    for i, p in enumerate(panels_raw):
        orient = (p.get("basis_orientation") or "").lower()
        if orient not in HORIZONT or not p.get("placement"):
            out.append(p)
            continue

        hbox = panel_from_dict(p)
        if not hbox:
            out.append(p)
            continue

        cuts: list[tuple[float, float]] = []
        blocking: list[str] = []
        for v in vert_boxes:
            if not v or v.name == hbox.name:
                continue
            if hbox.intersection_volume(v) < min_volume:
                continue
            if not _same_y_band(hbox, v):
                continue
            cuts.append((v.x1, v.x2))
            blocking.append(v.name)

        if not cuts:
            out.append(p)
            continue

        segments = _cut_intervals(hbox.x1, hbox.x2, cuts)
        if len(segments) <= 1:
            out.append(p)
            continue

        base_name = p.get("name", "полка")
        for idx, (sx1, sx2) in enumerate(segments, start=1):
            part = copy.deepcopy(p)
            if len(segments) == 2:
                part["name"] = f"{base_name} {'левая' if idx == 1 else 'правая'}"
            else:
                part["name"] = f"{base_name} {idx}"
            _update_panel_placement(part, sx1, sx2)
            out.append(part)
        log.append(
            f"«{base_name}» -> {len(segments)} части по X (обход: {', '.join(blocking)})."
        )

    result["panels"] = out
    return result, log


def run_geometry_pipeline(
    data: dict[str, Any],
    *,
    min_volume: float = 1.0,
    auto_fix_horizontal: bool = False,
) -> dict[str, Any]:
    """Проверка → опционально авторазделение горизонталей → повторная проверка."""
    report = check_placement_geometry(data, min_volume=min_volume)
    fixes: list[str] = []
    working = data
    if auto_fix_horizontal and not report["ok"]:
        working, fixes = resolve_horizontal_splits(working, min_volume=min_volume)
        report = check_placement_geometry(working, min_volume=min_volume)
    report["fixes_applied"] = fixes
    if auto_fix_horizontal and fixes:
        report["data_after_fix"] = working
    return report


def format_overlap_report(issues: list[OverlapIssue]) -> str:
    if not issues:
        return "Пересечений панелей не обнаружено (порог min_volume учитывается)."
    lines = ["Обнаружены пересечения панелей:", ""]
    for o in issues:
        lines.append(
            f"- {o.panel_a} ({o.orientation_a}) <-> {o.panel_b} ({o.orientation_b}): "
            f"~{o.intersection_volume_mm3} мм³ — {o.suggested_action}. {o.detail}"
        )
    return "\n".join(lines)


# orientation -> (ось ширины, ось высоты) для dimensions, согласовано с импортёром
_DIM_AXES = {
    "horizont": ("x", "z"),
    "horizontal": ("x", "z"),
    "vertical": ("z", "y"),
    "front": ("x", "y"),
}


def recompute_dimensions_from_placement(
    data: dict[str, Any], *, tol: float = 0.5
) -> tuple[dict[str, Any], list[str]]:
    """
    Приводит dimensions.width/height каждой панели к фактическому пролёту placement
    (источник истины конвейера). Возвращает копию данных и журнал изменений.
    """
    result = copy.deepcopy(data)
    log: list[str] = []
    for p in result.get("panels", []):
        if not isinstance(p, dict):
            continue
        pl = p.get("placement")
        dims = p.get("dimensions")
        orient = str(p.get("basis_orientation") or "").lower()
        axes = _DIM_AXES.get(orient)
        if not isinstance(pl, dict) or not isinstance(dims, dict) or axes is None:
            continue
        w_axis, h_axis = axes
        new_w = round(float(pl[w_axis + "2"]) - float(pl[w_axis + "1"]), 2)
        new_h = round(float(pl[h_axis + "2"]) - float(pl[h_axis + "1"]), 2)
        old_w = dims.get("width")
        old_h = dims.get("height")
        changed = []
        if old_w is None or abs(float(old_w) - new_w) > tol:
            dims["width"] = new_w
            changed.append(f"width {old_w}→{new_w}")
        if old_h is None or abs(float(old_h) - new_h) > tol:
            dims["height"] = new_h
            changed.append(f"height {old_h}→{new_h}")
        if changed:
            log.append(f"«{p.get('name', 'панель')}»: " + ", ".join(changed))
    return result, log


def check_placement_file(path: str | Path, min_volume: float = 1.0) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as f:
        data = json.load(f)
    return check_placement_geometry(data, min_volume=min_volume)
