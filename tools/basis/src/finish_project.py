"""Финализация проекта после расчёта JSON: схема + геометрия + автоисправление."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.consistency_check import check_consistency, format_consistency_report
from src.geometry_check import (
    check_placement_geometry,
    resolve_horizontal_splits,
)
from src.validate import validate_file


FACADE_TYPES = frozenset({"door_front", "drawer_front", "front_panel"})


def _auto_fix_facade_overlaps(
    data: dict[str, Any], *, min_volume: float
) -> tuple[dict[str, Any], list[str]]:
    """
    Если фасады (door_front/drawer_front) пересекаются с корпусом,
    сдвигаем их вперёд: z = -T..0 (T = thickness фасада).

    Это позволяет убрать геом. пересечения в БАЗИС, когда фасад стоит в той же Z-зоне, что и боковины.
    """
    panels = data.get("panels", [])
    if not isinstance(panels, list) or not panels:
        return data, []

    report_all = check_placement_geometry(data, min_volume=min_volume, structural_only=False)
    if report_all.get("ok"):
        return data, []

    by_name: dict[str, dict[str, Any]] = {
        p.get("name", f"panel_{i}"): p for i, p in enumerate(panels) if isinstance(p, dict)
    }

    touched: set[str] = set()
    for o in report_all.get("overlaps", []):
        a_name = o.get("panel_a")
        b_name = o.get("panel_b")
        if not a_name or not b_name:
            continue
        a = by_name.get(a_name)
        b = by_name.get(b_name)
        if not a or not b:
            continue

        a_type = str(a.get("type", "")).lower()
        b_type = str(b.get("type", "")).lower()
        facade = a if a_type in FACADE_TYPES else (b if b_type in FACADE_TYPES else None)
        if not facade:
            continue

        pl = facade.get("placement")
        if not isinstance(pl, dict):
            continue
        t = float(facade.get("thickness") or 16.0)

        # already moved out
        if float(pl.get("z2", 0)) <= 0 and float(pl.get("z1", 0)) <= -t:
            continue

        pl["z1"] = round(-t, 2)
        pl["z2"] = 0
        pos = facade.get("position")
        if isinstance(pos, dict):
            pos["z"] = pl["z1"]
        touched.add(str(facade.get("name", "")))

    if not touched:
        return data, []

    return data, [
        "Фасады пересекались с корпусом -> вынесены вперёд по Z (z=-T..0) для: "
        + ", ".join(sorted(n for n in touched if n))
    ]

@dataclass
class FinishResult:
    json_path: Path
    schema_ok: bool
    geometry_ok: bool
    schema_errors: list[str] = field(default_factory=list)
    overlaps: list[dict[str, Any]] = field(default_factory=list)
    fixes_applied: list[str] = field(default_factory=list)
    consistency_notes: list[str] = field(default_factory=list)
    completeness_errors: list[str] = field(default_factory=list)
    saved: bool = False

    @property
    def ok(self) -> bool:
        return self.schema_ok and self.geometry_ok and not self.completeness_errors

    def summary(self) -> str:
        lines = [f"Файл: {self.json_path}"]
        if not self.schema_ok:
            lines.append(f"Схема: ОШИБКА ({len(self.schema_errors)} шт.)")
            return "\n".join(lines)
        lines.append("Схема: OK")
        if self.fixes_applied:
            lines.append("Автоисправления:")
            for f in self.fixes_applied:
                lines.append(f"  - {f}")
        if self.geometry_ok:
            lines.append("Геометрия: OK (пересечений нет)")
        else:
            lines.append(f"Геометрия: ОШИБКА ({len(self.overlaps)} пересечений)")
            for o in self.overlaps:
                lines.append(
                    f"  - {o['panel_a']} <-> {o['panel_b']}: {o['suggested_action']}"
                )
        if self.completeness_errors:
            lines.append(f"Полнота: ОШИБКА ({len(self.completeness_errors)} шт., AKD-182)")
            for n in self.completeness_errors:
                lines.append(f"  - {n}")
        else:
            lines.append("Полнота: OK (все детали закреплены)")
        if self.consistency_notes:
            lines.append("Несостыковки (не блокируют, проверьте вручную):")
            for n in self.consistency_notes:
                lines.append(f"  - {n}")
        if self.saved:
            lines.append("JSON обновлён на диске.")
        return "\n".join(lines)


def _can_auto_fix_horizontal(overlaps: list[dict[str, Any]]) -> bool:
    """Есть пересечения, которые снимаются разрезом горизонталей."""
    fix_actions = {
        "split_horizontal_along_x",
        "split_horizontal_along_z",
    }
    for o in overlaps:
        if o.get("suggested_action") in fix_actions:
            return True
        oa, ob = o.get("orientation_a", ""), o.get("orientation_b", "")
        if ("horizont" in (oa, ob) or "horizontal" in (oa, ob)) and (
            oa in {"vertical", "front", "back"} or ob in {"vertical", "front", "back"}
        ):
            return True
    return False


def finish_project(
    json_path: Path,
    *,
    min_volume: float = 1.0,
    max_fix_rounds: int = 3,
) -> FinishResult:
    path = Path(json_path)
    result = FinishResult(json_path=path, schema_ok=False, geometry_ok=False)

    schema_errors = validate_file(path)
    if schema_errors:
        result.schema_errors = schema_errors
        return result
    result.schema_ok = True

    with path.open(encoding="utf-8") as f:
        data = json.load(f)

    fixes: list[str] = []
    # 0) Фасады: если пересекаются с корпусом, вынести вперёд по Z и перепроверить
    data, facade_fixes = _auto_fix_facade_overlaps(data, min_volume=min_volume)
    fixes.extend(facade_fixes)

    report = check_placement_geometry(data, min_volume=min_volume)

    from .telemetry import span

    for iteration in range(max_fix_rounds):
        if report["ok"] or not _can_auto_fix_horizontal(report["overlaps"]):
            break
        with span("repair.iteration", {"repair.iteration": iteration + 1}) as trace_span:
            data, round_fixes = resolve_horizontal_splits(data, min_volume=min_volume)
            trace_span.set_attributes({
                "repair.applied": bool(round_fixes),
                "check.outcome": "pass" if round_fixes else "unchanged",
            })
            if not round_fixes:
                break
            fixes.extend(round_fixes)
            report = check_placement_geometry(data, min_volume=min_volume)

    result.fixes_applied = fixes
    result.overlaps = report["overlaps"]
    result.geometry_ok = report["ok"]
    result.consistency_notes = [
        f"[{i.code}] {i.panel}: {i.message}" for i in check_consistency(data)
    ]
    try:
        from src.completeness_check import check_completeness
        result.completeness_errors = check_completeness(data)
    except Exception as e:  # noqa: BLE001 — полнота не должна ронять finish целиком
        result.completeness_errors = [f"проверка полноты упала: {e}"]

    if fixes:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        result.saved = True

    return result
