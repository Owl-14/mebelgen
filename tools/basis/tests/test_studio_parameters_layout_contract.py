"""Контракт компактной верхней части режима «Параметры» (UX-026).

Тесты фиксируют только DOM/CSS-оболочку. Значения по-прежнему читают и пишут
существующие ``fillForm`` / ``harvest`` и динамические ``data-ak`` обработчики.
"""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.studio import PAGE  # noqa: E402


class _Dom(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: dict[str, tuple[str, dict[str, str | None], tuple[str, ...]]] = {}
        self.labels: dict[str, list[dict[str, str | None]]] = {}
        self._stack: list[tuple[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        node_id = values.get("id")
        ancestors = tuple(value for _tag, value in self._stack if value)
        if node_id:
            self.ids[node_id] = (tag, values, ancestors)
        if tag == "label" and values.get("for"):
            self.labels.setdefault(str(values["for"]), []).append(values)
        if tag not in {
            "area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr",
        }:
            self._stack.append((tag, node_id))

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                del self._stack[index:]
                return


def _dom() -> _Dom:
    dom = _Dom()
    dom.feed(PAGE)
    return dom


def _function(name: str, length: int = 4200) -> str:
    match = re.search(rf"function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{", PAGE)
    assert match, f"Не найдена функция {name}"
    return PAGE[match.start():match.start() + length]


def _style() -> str:
    match = re.search(r"<style>(.*?)</style>", PAGE, flags=re.DOTALL)
    assert match
    return match.group(1)


def test_parameter_controls_remain_unique_labeled_and_in_properties_mode() -> None:
    dom = _dom()
    controls = (
        "f_w", "f_d", "f_h", "f_color", "f_facade_color", "f_code", "f_t",
        "decorQ", "archSel", "f_legs", "f_gap",
    )
    for control_id in controls:
        assert len(re.findall(rf'\bid=["\']{re.escape(control_id)}["\']', PAGE)) == 1
        _tag, _attrs, ancestors = dom.ids[control_id]
        assert "rightViewProperties" in ancestors
        assert len(dom.labels.get(control_id, [])) == 1, (
            f"#{control_id} должен иметь ровно одну явную label[for]"
        )


def test_dimensions_are_one_real_three_axis_strip_with_explicit_units() -> None:
    dom = _dom()
    grid_tag, grid, ancestors = dom.ids["fs_dims"]
    assert grid_tag == "fieldset"
    assert "parameter-section" in (grid.get("class") or "").split()
    assert "rightViewProperties" in ancestors

    for field_id in ("f_w", "f_d", "f_h"):
        unit_id = f"{field_id}_unit"
        tag, attrs, field_ancestors = dom.ids[field_id]
        assert tag == "input" and attrs.get("type") == "number"
        assert attrs.get("aria-describedby") == unit_id
        assert "fs_dims" in field_ancestors
        assert dom.ids[unit_id][0] == "span"

    css = re.sub(r"\s+", "", _style())
    assert "#rightViewProperties.dimension-grid{display:grid;" in css
    assert "grid-template-columns:repeat(3,minmax(0,1fr))" in css


def test_material_and_installation_use_compact_decision_grids() -> None:
    dom = _dom()
    for field_id in ("f_color", "f_facade_color", "f_code", "f_t", "decorQ"):
        assert "fs_material" in dom.ids[field_id][2]
    for field_id in ("f_legs", "f_gap"):
        assert "fs_support" in dom.ids[field_id][2]
        assert dom.ids[field_id][1].get("aria-describedby") == f"{field_id}_unit"

    properties = PAGE[PAGE.index('<div id="rightViewProperties"'):PAGE.index(
        '<div id="rightViewComponents"'
    )]
    assert properties.index('id="fs_archetype"') < properties.index('id="fs_arch"')
    assert properties.index('id="fs_arch"') < properties.index('id="fs_support"')
    assert properties.index('id="fs_support"') < properties.index('id="fs_sections"')
    assert 'id="fs_sections" class="parameter-section"' not in properties

    _tag, search, _ancestors = dom.ids["decorQ"]
    assert search.get("autocomplete") == "off"
    assert search.get("aria-controls") == "decorList"
    assert search.get("aria-describedby") == "decorHint"
    assert dom.ids["decorList"][1].get("role") == "list"
    assert dom.ids["decorList"][1].get("aria-live") == "polite"


def test_decor_results_expose_separate_keyboard_actions_for_body_and_facade() -> None:
    loader = _function("loadDecors", length=5000)
    compact = re.sub(r"\s+", "", loader)
    assert "d.setAttribute('role','listitem')" in compact
    assert 'type="button"class="decor-body"' in compact
    assert 'aria-label="Применить${it.label}ккорпусу"' in compact
    assert 'type="button"class="fb"' in compact
    assert 'aria-label="Применить${it.label}кфасадам"' in compact
    assert "if(e.target.classList.contains('fb'))" in compact
    assert "fillForm();apply();" in compact


def test_dynamic_archetype_fields_gain_ids_without_changing_data_contract() -> None:
    renderer = _function("renderArchetype")
    compact = re.sub(r"\s+", "", renderer)
    assert "defs.forEach((f,index)=>" in compact
    assert "constfieldId=`archField_${index}`" in compact
    assert "label.htmlFor=fieldId" in compact
    assert "label.textContent=f.label" in compact
    assert "input.id=fieldId" in compact
    assert "input.dataset.ak=f.key" in compact
    assert "row.className='parameter-row'" in compact
    assert "input=document.createElement('input')" in compact
    assert "input=document.createElement('select')" in compact
    assert "option.textContent=o||'—'" in compact
    assert "row.innerHTML" not in renderer
    assert "input.setAttribute('aria-describedby',hint.id)" in compact

    input_handler = PAGE[PAGE.index("document.addEventListener('input'"):]
    assert "t.dataset&&t.dataset.ak" in input_handler
    assert "FIELDS[SPEC.archetype]" in input_handler
    assert "schedule(); return;" in input_handler


def test_existing_fill_and_harvest_handlers_still_own_all_static_values() -> None:
    fill = _function("fillForm")
    harvest = _function("harvest")
    for field_id in (
        "f_w", "f_d", "f_h", "f_color", "f_facade_color", "f_code", "f_t",
        "f_legs", "f_gap",
    ):
        reference = f"$('" + field_id + "')"
        assert reference in fill, f"fillForm потерял {field_id}"
        assert reference in harvest, f"harvest потерял {field_id}"
