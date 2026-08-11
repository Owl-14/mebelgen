"""Контракт первого contextual-среза секций правой панели (MEB-096)."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.studio import PAGE  # noqa: E402


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _function(name: str, length: int = 9000) -> str:
    match = re.search(rf"function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{", PAGE)
    assert match, f"Не найдена функция {name}"
    return PAGE[match.start() : match.start() + length]


def test_sections_use_contextual_details_without_changing_the_existing_form_anchor() -> None:
    compact = _compact(PAGE)
    assert 'id="fs_sections"' in PAGE
    assert 'id="sections"' in PAGE
    assert 'id="addSec"' in PAGE
    assert "SECTION_KIND_LABELS={drawers:'Ящики',shelves:'Полки',door:'Дверь',open:'Открытая'}" in compact
    assert "details.className='section-inspector'" in compact
    assert "section-inspector-body" in compact
    assert "section-inspector-actions" in compact
    assert "data-section-context=" in compact


def test_each_section_type_exposes_only_its_relevant_fields() -> None:
    renderer = _compact(_function("sectionContextFields"))
    assert "if(kind==='drawers')" in renderer
    assert "'drawers','Количествоящиков'" in renderer
    assert "'drawer_heights','Высотыящиков'" in renderer
    assert "if(kind==='shelves')" in renderer
    assert "'shelves','Количествополок'" in renderer
    assert "'shelf_levels','Уровниполок'" in renderer
    assert "if(kind==='door')" in renderer
    assert "'door','Количестводверей'" in renderer
    assert "Открытаясекциянетребуетдополнительныхпараметров." in renderer


def test_section_open_state_and_existing_data_contract_survive_rerender() -> None:
    renderer = _compact(_function("renderSections"))
    assert "sectionInspectorStateKnown?openSectionInspectors.has(index):index===0" in renderer
    assert "details.addEventListener('toggle'" in renderer
    assert "openSectionInspectors.add(index)" in renderer
    assert "openSectionInspectors.delete(index)" in renderer
    assert "select[data-k=\"kind\"]" in _function("renderSections")
    assert "requestAnimationFrame(renderSections)" in renderer
    assert "data-i=\"${index}\"" in _function("sectionInputRow")
    assert "data-k=\"${key}\"" in _function("sectionInputRow")


def test_sections_keep_engineering_surface_not_cards() -> None:
    style = re.search(r"<style>(.*?)</style>", PAGE, flags=re.DOTALL)
    assert style
    css = _compact(style.group(1))
    assert "#rightViewProperties.section-inspector{margin:0;border:0;border-top:1pxsolid#e6e9ed}" in css
    assert "#rightViewProperties.section-inspector>summary" in css
    assert "#rightViewProperties.section-inspector-body{display:grid" in css
    assert ".sec{border:1pxdashed" not in css


def test_sections_explain_unsupported_empty_and_locked_states() -> None:
    assert 'id="sectionsState"' in PAGE
    state_sync = _function("syncSectionInspectorState")
    for text in (
        "Для этого типа изделия секции не используются.",
        "В изделии пока нет секций. Добавьте первую секцию.",
        "Пересчёт идёт — значения пока нельзя менять.",
    ):
        assert text in state_sync
    assert "syncSectionInspectorState();" in _function("renderSections")
    assert "syncSectionInspectorState();" in _function("syncModelEditLock")
