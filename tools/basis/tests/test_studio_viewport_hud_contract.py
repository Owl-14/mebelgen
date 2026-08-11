"""Статический контракт viewport navigation/HUD (MEB-098).

Тесты фиксируют публичные связи с MebelScene и честные состояния режимов, но
не замораживают координаты визуального макета. Живой canvas проверяется отдельно.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.studio import PAGE  # noqa: E402


class _DomIndex(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.by_id: dict[str, tuple[str, dict[str, str | None], tuple[str, ...]]] = {}
        self.id_counts: Counter[str] = Counter()
        self.cameras: list[dict[str, str | None]] = []
        self._stack: list[tuple[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        ancestors = tuple(node_id for _tag, node_id in self._stack if node_id)
        node_id = values.get("id")
        if node_id:
            self.id_counts[node_id] += 1
            self.by_id[node_id] = (tag, values, ancestors)
        if tag == "button" and "vw" in (values.get("class") or "").split():
            self.cameras.append(values)
        if tag not in {"input", "path", "rect", "br", "hr", "img", "meta", "link"}:
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


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _function_slice(name: str, length: int = 2600) -> str:
    match = re.search(rf"function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{", PAGE)
    assert match, f"Не найдена функция {name}"
    return PAGE[match.start() : match.start() + length]


def test_viewport_controls_keep_unique_dom_types_and_defaults() -> None:
    dom = _dom()
    required = {
        "main": "div",
        "view3d": "div",
        "viewportTopbar": "div",
        "tabs": "div",
        "tab3d": "button",
        "tabDraw": "button",
        "tabNest": "button",
        "btnPrint": "button",
        "views": "div",
        "hud": "div",
        "viewportTools": "div",
        "btnToggleOpenAll": "button",
        "explode": "input",
    }
    for node_id, expected_tag in required.items():
        tag, _attrs, _ancestors = dom.by_id[node_id]
        assert tag == expected_tag
        assert dom.id_counts[node_id] == 1

    _tag, toggle_attrs, toggle_ancestors = dom.by_id["btnToggleOpenAll"]
    assert "hud" in toggle_ancestors and toggle_attrs.get("aria-pressed") == "false"
    assert "Открыть всё" in PAGE
    assert "Открыть всё / Закрыть всё" not in PAGE
    assert "viewportTools" in dom.by_id["views"][2]
    assert "viewportTools" in dom.by_id["hud"][2]
    assert "layerCount" not in dom.by_id

    assert [camera["data-view"] for camera in dom.cameras] == [
        "axon",
        "persp",
        "top",
        "front",
        "left",
    ]
    assert sum(camera.get("aria-pressed") == "true" for camera in dom.cameras) == 1
    assert next(camera for camera in dom.cameras if camera["data-view"] == "persp")[
        "aria-pressed"
    ] == "true"

    layer_defaults = {
        "cbHoles": True,
        "cbHw": True,
        "cbTex": True,
        "cbDims": True,
        "cbXray": False,
    }
    for node_id, checked in layer_defaults.items():
        tag, attrs, ancestors = dom.by_id[node_id]
        assert tag == "input" and attrs.get("type") == "checkbox"
        assert "hud" in ancestors
        assert ("checked" in attrs) is checked

    _tag, explode, ancestors = dom.by_id["explode"]
    assert "hud" in ancestors
    assert (explode.get("type"), explode.get("min"), explode.get("max"), explode.get("value")) == (
        "range",
        "0",
        "100",
        "0",
    )


def test_hud_preserves_public_mebel_scene_handlers() -> None:
    compact = _compact(PAGE)
    assert compact.count("MebelScene(view)") == 1
    for mapping in (
        "$('cbHoles').onchange=e=>scene3d.setHoles(e.target.checked)",
        "$('cbHw').onchange=e=>scene3d.setHw(e.target.checked)",
        "$('cbTex').onchange=e=>scene3d.setTextures(e.target.checked)",
        "$('cbDims').onchange=e=>scene3d.setDims(e.target.checked)",
        "$('cbXray').onchange=e=>scene3d.setXray(e.target.checked)",
        "scene3d.setExplode(e.target.value/100)",
        "scene3d.onOpenablesChange=state=>syncOpenAllButton(!!(state&&state.hasOpen))",
        "scene3d.closeAll()",
        "scene3d.openAll()",
        "scene3d.setView(b.dataset.view)",
    ):
        assert _compact(mapping) in compact
    assert "functionsyncOpenAllButton(hasOpen)" in compact
    assert "button.dataset.open=String(!!hasOpen)" in compact


def test_modes_hide_irrelevant_3d_controls_and_gate_current_print() -> None:
    switch = _compact(_function_slice("switchTab"))
    assert "$('views').hidden=!is3d" in switch
    assert "$('hud').hidden=!is3d" in switch
    assert "syncWorkspacePrintState()" in switch
    assert "workspaceViewRequestSeq++" in switch
    assert "aria-selected" in switch

    print_state = _compact(_function_slice("syncWorkspacePrintState"))
    assert "renderedWorkspaceMode===mode" in print_state
    assert "$('draw').querySelector('svg')" in print_state
    assert "$('btnPrint').disabled=!ready" in print_state

    assert "renderedWorkspaceMode=p.svg?'draw':null" in _compact(_function_slice("refreshDraw"))
    assert "renderedWorkspaceMode=p.svg?'nest':null" in _compact(_function_slice("refreshNest"))


def test_camera_preset_survives_click_but_clears_after_real_camera_gesture() -> None:
    compact = _compact(PAGE)
    assert "Math.hypot(event.clientX-cameraGestureStart.x,event.clientY-cameraGestureStart.y)<4" in compact
    assert "view.addEventListener('pointermove'" in compact
    assert "view.addEventListener('wheel',clearCameraPreset,{passive:true})" in compact
    assert "view.addEventListener('pointerdown',()=>" not in compact
    assert "x.setAttribute('aria-pressed','false')" in compact


def test_viewport_shell_uses_container_width_and_preserves_canvas_hit_area() -> None:
    style_match = re.search(r"<style>(.*?)</style>", PAGE, flags=re.DOTALL)
    assert style_match
    css = _compact(style_match.group(1))
    assert "#main{--chat-stack-height:133px" in css
    assert "container-type:inline-size" in css
    assert "#viewportTopbar{" in css and "pointer-events:none" in css
    assert "#tabs,#views,#hud.hud-section{" in css and "pointer-events:auto" in css
    assert "grid-template-columns:max-contentmax-contentminmax(0,1fr)" in css
    assert "grid-template-areas:\"modeslayersviews\"\"..model\"" in css
    assert "#tabs{grid-area:modes}" in css
    assert "#views{grid-area:views}" in css
    assert "#views{justify-content:stretch;gap:1px}" in css
    assert "#views.vw{flex:110;min-width:0" in css
    assert "#hud.hud-layers{grid-area:layers}" in css
    assert "#hud.hud-model{grid-area:model}" in css
    assert "#hud.hud-model{gap:5px}" in css
    assert "flex:11auto" in css
    assert "#draw{position:absolute;inset:90px12px12px" in css
    assert "@container(max-width:1099px)" in css
    assert "@container(max-width:700px)" in css
    assert "@container(max-width:440px)" in css
    assert "#views[hidden],#hud[hidden]{display:none!important}" in css
