"""Контракт нижней строки состояния viewport (MEB-098, второй срез)."""

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
        self.by_id: dict[str, tuple[str, dict[str, str | None], tuple[str, ...]]] = {}
        self._stack: list[tuple[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        node_id = values.get("id")
        ancestors = tuple(value for _tag, value in self._stack if value)
        if node_id:
            self.by_id[node_id] = (tag, values, ancestors)
        if tag not in {"input", "path", "rect", "br", "hr", "img", "meta", "link"}:
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


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _function(name: str, length: int = 5000) -> str:
    match = re.search(rf"(?:async\s+)?function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{", PAGE)
    assert match, f"Не найдена функция {name}"
    return PAGE[match.start() : match.start() + length]


def _css(selector: str) -> str:
    style = re.search(r"<style>(.*?)</style>", PAGE, flags=re.DOTALL)
    assert style
    match = re.search(selector + r"\s*\{([^}]*)\}", style.group(1), flags=re.DOTALL)
    assert match, f"Не найден CSS selector {selector}"
    return match.group(1)


def test_status_bar_is_static_noninteractive_dom_with_one_live_region() -> None:
    dom = _dom()
    expected = {
        "viewportStatus": "div",
        "viewportModelStatus": "div",
        "viewportModelMark": "span",
        "viewportModelStateLong": "span",
        "viewportModelStateShort": "span",
        "viewportSaveStatus": "div",
        "viewportSaveMark": "span",
        "viewportSaveStateLong": "span",
        "viewportSaveStateShort": "span",
        "viewportSelection": "div",
        "viewportSelectionName": "b",
        "viewportHint": "div",
        "viewportUnits": "div",
    }
    for node_id, tag in expected.items():
        actual, _attrs, ancestors = dom.by_id[node_id]
        assert actual == tag
        if node_id != "viewportStatus":
            assert "viewportStatus" in ancestors

    _tag, model_status, _ancestors = dom.by_id["viewportModelStatus"]
    assert model_status.get("role") == "status"
    assert model_status.get("aria-live") == "polite"
    assert model_status.get("aria-atomic") == "true"
    assert sum(attrs.get("role") == "status" for _tag, attrs, _ancestors in dom.by_id.values()) >= 1
    assert dom.by_id["viewportUnits"][1] == {"id": "viewportUnits", "class": "viewport-status-segment"}


def test_status_bar_is_flat_bottom_rail_and_composer_toast_clear_it() -> None:
    status = _compact(_css(r"#viewportStatus"))
    assert "position:absolute" in status and "bottom:0" in status
    assert "height:var(--viewport-status-height)" in status
    assert "border-top:1pxsolid#d9dee5" in status
    assert "background:#fff" in status and "pointer-events:none" in status
    assert "box-shadow" not in status and "backdrop-filter" not in status

    main = _compact(_css(r"#main"))
    assert "--viewport-status-height:24px" in main
    dock = _compact(_css(r"#fs_chat"))
    assert "bottom:calc(10px+var(--viewport-status-height))" in dock
    toast = _compact(_css(r"#toast"))
    assert "var(--chat-stack-height)" in toast
    draw = _compact(_css(r"#draw"))
    assert "var(--chat-stack-height)" in draw
    assert "var(--viewport-status-height)" in draw


def test_model_state_api_names_every_user_decision_state() -> None:
    status = _compact(_function("syncViewportStatus"))
    for phase in ("changed", "recalculating", "ready", "decision", "blocked", "stale", "draft"):
        assert f"viewportModelState==='{phase}'" in status
    for text in (
        "Изменения ещё не пересчитаны",
        "Пересчитываю изменения",
        "производство доступно",
        "требуется решение",
        "производство заблокировано",
        "Текущая редакция не построена",
    ):
        assert _compact(text) in status
    assert "savedSpecJson" in status
    assert "Естьнесохранённыеизменения" in status
    assert ".textContent=longText" in status
    assert ".textContent=shortText" in status
    assert "status.dataset.tone=tone" in status


def test_generation_token_prevents_stale_response_from_overwriting_status() -> None:
    apply = _compact(_function("apply", length=3600))
    stale = apply.index("requestId!==generateRequestSeq")
    painted = apply.index("paint(p,opts)")
    terminal = apply.index("setViewportModelState(!p.viewer?")
    assert stale < painted < terminal
    assert "setViewportModelState('recalculating')" in apply
    assert "!p.ok?'blocked':diagnostics.unresolved?'decision':'ready'" in apply
    assert "setViewportModelState('stale')" in apply
    assert "generatedRevision=p.viewer?String(p.revision||''):''" in apply

    schedule = _compact(_function("schedule", length=900))
    assert "setViewportModelState('changed')" in schedule
    assert "renderedWorkspaceMode=null" in schedule
    assert "renderedWorkspaceSpecJson=null" in schedule
    assert "apply().catch(()=>{})" in schedule
    assert "fillForm(); resize(); apply().catch(()=>{});" in PAGE


def test_production_controls_require_the_successfully_rendered_revision() -> None:
    availability = _compact(_function("syncProductionAvailability", length=1700))
    assert "generatedSpecJson===JSON.stringify(SPEC)" in availability
    assert "viewportModelState==='ready'" in availability
    assert "!!generatedRevision" in availability
    for button in ("btnCfrn", "btnB3d", "btnDeliver"):
        assert button in availability

    post = _compact(_function("post", length=600))
    assert "model_revision:generatedRevision" in post
    assert "productionErrorText" in PAGE


def test_selection_mode_and_print_revision_feed_the_status() -> None:
    status = _compact(_function("syncViewportStatus"))
    assert "currentSelectedPart()" in status
    assert "currentWorkspaceMode()" in status
    assert "viewportSelectionName" in status
    assert "Кликподетали—выбрать" in status
    assert "Shift+перетаскивание—переместить" in status

    print_state = _compact(_function("syncWorkspacePrintState", length=1300))
    assert "renderedWorkspaceSpecJson===JSON.stringify(SPEC)" in print_state
    assert "syncViewportStatus()" in print_state
    assert "renderedWorkspaceSpecJson=p.svg?requestSpecJson:null" in _compact(
        _function("refreshDraw", length=1800)
    )
    assert "renderedWorkspaceSpecJson=p.svg?requestSpecJson:null" in _compact(
        _function("refreshNest", length=1600)
    )

    selection_handler = PAGE[PAGE.index("scene3d.onSelect=sel=>{") :][:1800]
    assert "syncViewportStatus()" in selection_handler


def test_dynamic_composer_height_and_rename_invalidate_dependent_views() -> None:
    assert "syncCommandStackHeight()" in _function("resizeChatInput", length=600)
    assert "syncCommandStackHeight()" in _function("renderImgs", length=1800)

    rename = PAGE[PAGE.index("$('projRen').onclick=async()=>{") :][:1100]
    assert "previousName=SPEC.project_name" in rename
    assert "schedule()" in rename
    assert "SPEC.project_name=previousName" in rename
