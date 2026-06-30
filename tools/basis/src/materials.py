"""Справочник материалов/фурнитуры (AKD-11).

Стабильные id + кандидаты точных имён БАЗИС (basisName). Назначение — выбирать
реальные материалы из базы, а не «выдумывать» имена по тексту ТЗ. Точные basisName
сверяются/правятся при доступной лицензии БАЗИС (MatBase, AKD-12).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CATALOG_PATH = Path(__file__).resolve().parent.parent / "materials" / "catalog.json"


def load_catalog(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or CATALOG_PATH).read_text(encoding="utf-8"))


def _all_entries(cat: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key in ("boards", "backs", "edges", "hardware"):
        out += cat.get(key, [])
    return out


def by_id(ref: str, cat: dict[str, Any] | None = None) -> dict[str, Any] | None:
    cat = cat or load_catalog()
    for e in _all_entries(cat):
        if e.get("id") == ref:
            return e
    return None


def resolve_board(*, thickness: float | None = None, color: str | None = None,
                  cat: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Подобрать плиту по толщине (и цвету, если задан)."""
    cat = cat or load_catalog()
    best = None
    for e in cat.get("boards", []):
        if thickness is not None and abs(e.get("thickness", -1) - thickness) > 0.05:
            continue
        if color and e.get("color") and color.lower() not in str(e["color"]).lower():
            continue
        best = e
        if color and e.get("color"):
            break
    return best


def check_project_materials(project: dict[str, Any], cat: dict[str, Any] | None = None) -> list[str]:
    """Замечания: материалы/толщины проекта, которым нет соответствия в каталоге."""
    cat = cat or load_catalog()
    notes: list[str] = []
    m = project.get("materials", {})
    th = m.get("board_thickness")
    if th is not None and resolve_board(thickness=th, cat=cat) is None:
        notes.append(f"Плита толщиной {th} мм не найдена в каталоге — добавить в materials/catalog.json.")
    tb = m.get("back_wall_material")
    if tb and not any(tb.lower() in (e.get("basisName", "").lower()) for e in cat.get("backs", [])):
        notes.append(f"Задняя стенка «{tb}» не сопоставлена с каталогом backs.")
    color = str(m.get("color", "")).lower()
    if color and "согласован" not in color and "уточн" not in color:
        if not any(color in str(e.get("color", "")).lower() for e in cat.get("boards", []) if e.get("color")):
            notes.append(f"Цвет «{m.get('color')}» не встречается в каталоге плит — проверить наличие в базе БАЗИС.")
    return notes
