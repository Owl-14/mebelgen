"""Защитный DOM/CSS-контракт согласованного поведения панелей Studio.

Это намеренно статические тесты ``PAGE``: они не подменяют browser parity,
но быстро ловят случайное удаление rail, ARIA-связей и desktop breakpoints.
"""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.studio import PAGE  # noqa: E402


class _DomIndex(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.by_id: dict[str, tuple[str, dict[str, str | None]]] = {}
        self.ancestors: dict[str, tuple[str, ...]] = {}
        self._stack: list[tuple[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        node_id = attr_map.get("id")
        if node_id:
            self.by_id[node_id] = (tag, attr_map)
            self.ancestors[node_id] = tuple(
                ancestor_id for _tag, ancestor_id in self._stack if ancestor_id
            )
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self._stack.append((tag, node_id))

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                del self._stack[index:]
                return


def _dom() -> _DomIndex:
    dom = _DomIndex()
    dom.feed(PAGE)
    return dom


def _style() -> str:
    match = re.search(r"<style>(.*?)</style>", PAGE, flags=re.DOTALL | re.IGNORECASE)
    assert match, "Studio PAGE должна содержать встроенный CSS"
    return match.group(1)


def _media_block(css: str, rule: str) -> str:
    """Возвращает тело media rule с учётом вложенных фигурных скобок."""
    match = re.search(rule + r"\s*\{", css, flags=re.IGNORECASE)
    assert match, f"Не найден responsive rule: {rule}"
    start = match.end()
    depth = 1
    for pos in range(start, len(css)):
        if css[pos] == "{":
            depth += 1
        elif css[pos] == "}":
            depth -= 1
            if depth == 0:
                return css[start:pos]
    raise AssertionError(f"Не закрыт responsive rule: {rule}")


def _css_rule(css: str, selector: str) -> str:
    match = re.search(selector + r"\s*\{([^}]*)\}", css, flags=re.DOTALL)
    assert match, f"Не найден CSS selector: {selector}"
    return match.group(1)


def test_right_panel_controls_keep_dom_and_aria_contract():
    dom = _dom()

    app_tag, app = dom.by_id["app"]
    assert app_tag == "div"
    assert "right-collapsed" in (app.get("class") or "").split()
    assert "rightside" in dom.by_id
    assert "rightRail" in dom.by_id

    controls = (
        "rightRailProperties",
        "rightRailComponents",
        "rightRailProduction",
        "rightPanelClose",
    )
    labels: set[str] = set()
    for control_id in controls:
        tag, attrs = dom.by_id[control_id]
        assert tag == "button", f"#{control_id} должен оставаться button"
        assert attrs.get("aria-controls") == "rightside"
        assert attrs.get("aria-expanded") in {"true", "false"}
        label = attrs.get("aria-label")
        assert label, f"#{control_id} нужен понятный aria-label"
        labels.add(label)

    assert len(labels) == len(controls), "Управляющие кнопки должны иметь разные подписи"

    modes = (
        ("rightTabProperties", "rightViewProperties", "true"),
        ("rightTabComponents", "rightViewComponents", "false"),
        ("rightTabProduction", "rightViewProduction", "false"),
    )
    tablist_tag, tablist = dom.by_id["rightPanelTabs"]
    assert tablist_tag == "nav" and tablist.get("role") == "tablist"
    for tab_id, panel_id, selected in modes:
        tab_tag, tab = dom.by_id[tab_id]
        panel_tag, panel = dom.by_id[panel_id]
        assert tab_tag == "button" and tab.get("role") == "tab"
        assert tab.get("aria-controls") == panel_id
        assert tab.get("aria-selected") == selected
        assert panel_tag == "div" and panel.get("role") == "tabpanel"
        assert panel.get("aria-labelledby") == tab_id


def test_right_panel_modes_partition_every_existing_section() -> None:
    dom = _dom()
    expected = {
        "rightViewProperties": (
            "fs_dims", "fs_material", "fs_support", "fs_archetype",
            "fs_arch", "fs_sections", "fs_raw",
        ),
        "rightViewComponents": ("fs_hw", "fs_est", "bom"),
        "rightViewProduction": ("fs_export",),
    }
    for panel_id, child_ids in expected.items():
        for child_id in child_ids:
            assert panel_id in dom.ancestors[child_id], (
                f"#{child_id} должен оставаться в режиме #{panel_id}"
            )

    for panel_id in ("rightViewComponents", "rightViewProduction"):
        _tag, attrs = dom.by_id[panel_id]
        assert "hidden" in attrs and "inert" in attrs


def test_set_right_panel_is_single_state_api_for_class_and_aria():
    declaration = re.search(
        r"function\s+setRightPanel\s*\(\s*open\s*,\s*mode\s*=\s*rightPanelMode"
        r"\s*,\s*fromUser\s*=\s*false\s*\)\s*\{",
        PAGE,
    )
    assert declaration, "Панель должна управляться через setRightPanel(open, mode)"

    # Ограничиваем проверку телом функции, чтобы случайное упоминание ниже в PAGE
    # не маскировало потерю синхронизации состояния.
    function_slice = PAGE[declaration.start():declaration.start() + 1500]
    assert "right-collapsed" in function_slice
    assert "classList.toggle" in function_slice
    assert "setRightPanelMode(mode)" in function_slice
    assert "aria-expanded" in function_slice
    assert "setAttribute" in function_slice
    assert "scene3d.resize()" in function_slice
    assert "scrollIntoView" not in function_slice

    mode_declaration = re.search(r"function\s+setRightPanelMode\s*\(\s*mode\s*\)\s*\{", PAGE)
    assert mode_declaration
    mode_slice = PAGE[mode_declaration.start():mode_declaration.start() + 1600]
    assert "item.panel.hidden=!active" in mode_slice
    assert "item.panel.inert=!active" in mode_slice
    assert "$('sideScroll').inert" not in mode_slice
    assert "aria-selected" in mode_slice
    assert "item.tab.tabIndex=active?0:-1" in mode_slice
    assert "item.rail.classList.toggle('is-active',active)" in mode_slice
    assert "rightPanelTitle" in mode_slice

    assert "item.rail.onclick=()=>setRightPanel(true,mode,true)" in PAGE
    assert "item.tab.onclick=()=>setRightPanel(true,mode,true)" in PAGE
    for key in ("ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"):
        assert key in PAGE


def test_right_panel_css_has_collapsed_rail_and_open_panel_states():
    css = _style()

    collapsed_panel = _css_rule(css, r"#app\.right-collapsed\s+#rightside")
    assert (
        re.search(r"display\s*:\s*none", collapsed_panel)
        or (
            re.search(r"visibility\s*:\s*hidden", collapsed_panel)
            and re.search(r"pointer-events\s*:\s*none", collapsed_panel)
        )
    )

    rail = _css_rule(css, r"#rightRail")
    assert re.search(r"display\s*:\s*(?:flex|grid|block)", rail)
    assert (
        re.search(r"display\s*:\s*none", rail)
        or re.search(r"visibility\s*:\s*hidden", rail)
        or re.search(r"opacity\s*:\s*0", rail)
    )

    collapsed_rail = _css_rule(css, r"#app\.right-collapsed\s+#rightRail")
    assert (
        re.search(r"visibility\s*:\s*visible", collapsed_rail)
        or re.search(r"opacity\s*:\s*1", collapsed_rail)
        or re.search(r"display\s*:\s*(?:flex|grid|block)", collapsed_rail)
    )
    assert not re.search(r"pointer-events\s*:\s*none", collapsed_rail)


def test_right_panel_css_covers_compact_and_wide_desktop():
    css = _style()
    compact = _media_block(css, r"@media\s*\(\s*max-width\s*:\s*1440px\s*\)")
    wide = _media_block(css, r"@media\s*\(\s*min-width\s*:\s*1600px\s*\)")

    for block in (compact, wide):
        assert "#app" in block
        assert (
            "grid-template-columns" in block
            or ("--side-width" in block and "--inspector-width" in block)
        )


def test_components_tables_do_not_force_horizontal_panel_overflow() -> None:
    css = _style()
    bom_table = _css_rule(css, r"#bom\s+table")
    bom_cells = _css_rule(css, r"#bom\s+td")
    assert re.search(r"table-layout\s*:\s*fixed", bom_table)
    assert re.search(r"overflow-wrap\s*:\s*anywhere", bom_cells)
