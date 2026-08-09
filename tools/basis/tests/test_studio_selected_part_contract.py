"""Статический контракт карточки выбранной детали Studio.

Проверки намеренно смотрят только на короткие именованные JS-функции и
статический DOM. Интерактивный parity MebelScene остаётся отдельным browser-QA.
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
    _VOID_TAGS = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }

    def __init__(self) -> None:
        super().__init__()
        self.by_id: dict[str, tuple[str, dict[str, str | None], tuple[str, ...]]] = {}
        self.part_coordinate_keys: list[str] = []
        self._stack: list[tuple[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        ancestors = tuple(node_id for _tag, node_id in self._stack if node_id)
        node_id = attr_map.get("id")
        if node_id:
            self.by_id[node_id] = (tag, attr_map, ancestors)
        if "partCard" in ancestors and attr_map.get("data-ov"):
            self.part_coordinate_keys.append(str(attr_map["data-ov"]))
        if tag not in self._VOID_TAGS:
            self._stack.append((tag, node_id))

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in self._VOID_TAGS:
            self._stack.pop()

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                del self._stack[index:]
                return


def _dom() -> _DomIndex:
    dom = _DomIndex()
    dom.feed(PAGE)
    return dom


def _balanced_block(match: re.Match[str]) -> str:
    """Вернуть короткий JS-блок с корректным балансом скобок и строк."""
    opening = PAGE.find("{", match.start(), match.end())
    assert opening >= 0, f"У блока нет открывающей скобки: {match.group(0)!r}"
    depth = 0
    quote: str | None = None
    escaped = False
    line_comment = False
    block_comment = False
    index = opening
    while index < len(PAGE):
        char = PAGE[index]
        nxt = PAGE[index + 1] if index + 1 < len(PAGE) else ""
        if line_comment:
            if char == "\n":
                line_comment = False
        elif block_comment:
            if char == "*" and nxt == "/":
                block_comment = False
                index += 1
        elif quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char == "/" and nxt == "/":
            line_comment = True
            index += 1
        elif char == "/" and nxt == "*":
            block_comment = True
            index += 1
        elif char in ("'", '"', "`"):
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return PAGE[match.start() : index + 1]
        index += 1
    raise AssertionError(f"Не удалось закрыть JS-блок: {match.group(0)!r}")


def _function(name: str) -> str:
    match = re.search(
        rf"(?:async\s+)?function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{",
        PAGE,
    )
    assert match, f"Не найдена JS-функция {name}"
    return _balanced_block(match)


def _arrow_block(pattern: str) -> str:
    match = re.search(pattern, PAGE)
    assert match, f"Не найден JS-обработчик {pattern}"
    return _balanced_block(match)


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def test_selected_part_is_a_static_dom_inspector_with_real_facts() -> None:
    dom = _dom()
    expected = {
        "fs_part": "fieldset",
        "partCard": "div",
        "partName": "b",
        "partKind": "span",
        "partOrigin": "span",
        "partDimensions": "dd",
        "partMaterial": "dd",
        "partEdges": "dd",
        "partHoles": "dd",
        "partEdgesRaw": "p",
        "partHolesRaw": "p",
        "partExact": "details",
        "partEditStatus": "div",
        "ovApply": "button",
        "ovReset": "button",
        "ovDelete": "button",
        "ovDetail": "button",
    }
    for node_id, tag in expected.items():
        actual_tag, _attrs, ancestors = dom.by_id[node_id]
        assert actual_tag == tag
        if node_id != "fs_part":
            assert "fs_part" in ancestors
        if node_id not in ("fs_part", "partCard"):
            assert "partCard" in ancestors

    assert set(dom.part_coordinate_keys) == {"x1", "x2", "y1", "y2", "z1", "z2"}

    render = _function("renderSelectedPart")
    for source in (
        "panel.material",
        "panel.thickness",
        "parsePanelEdges(panel.edges)",
        "partHoleData(panel.name)",
    ):
        assert source in render
    for target in ("partName", "partDimensions", "partMaterial", "partEdges", "partHoles"):
        assert f"$('{target}').textContent" in render
    assert ".innerHTML" not in render


def test_hidden_legacy_part_chat_routes_to_the_global_composer() -> None:
    dom = _dom()
    _tag, row_attrs, row_ancestors = dom.by_id["partChatRow"]
    assert "partCard" in row_ancestors
    assert "hidden" in row_attrs and row_attrs.get("aria-hidden") == "true"

    _tag, input_attrs, _ancestors = dom.by_id["partChat"]
    assert input_attrs.get("type") == "hidden"
    assert input_attrs.get("tabindex") == "-1"
    _tag, send_attrs, _ancestors = dom.by_id["partChatSend"]
    assert "hidden" in send_attrs and send_attrs.get("tabindex") == "-1"

    focus = _function("focusPartCommand")
    assert "$('chatMsg').focus()" in focus
    assert "$('chatMsg').scrollIntoView" in focus
    assert "$(`partCommandFocus`)" not in PAGE  # не допускаем второй шаблонный binding
    assert "$('partCommandFocus').onclick=focusPartCommand" in PAGE
    assert "$('partChatSend').onclick=focusPartCommand" in PAGE


def test_selection_updates_the_static_card_without_opening_right_inspector() -> None:
    selection = _arrow_block(r"scene3d\.onSelect\s*=\s*sel\s*=>\s*\{")
    assert "SELECTED_PART=sel?sel.panel:null" in _compact(selection)
    assert "renderSelectedPart(sel.panel)" in selection
    assert "$('chatContext')" in selection
    assert "$('chatContextLabel')" in selection
    assert "setRightPanel(" not in selection
    assert ".innerHTML" not in selection
    assert "card.innerHTML" not in selection


def test_exact_editor_requires_finite_ordered_dirty_coordinates() -> None:
    draft = _compact(_function("readPartDraft"))
    assert "PART_COORD_KEYS.every(key=>Number.isFinite(values[key]))" in draft
    for ordering in ("values.x1<values.x2", "values.y1<values.y2", "values.z1<values.z2"):
        assert ordering in draft
    assert "PART_COORD_KEYS.some(key=>values[key]!==Number(panel[key]))" in draft

    sync = _compact(_function("syncPartEditState"))
    assert "locked=partEditBusy||chatBusy" in sync
    assert "$('ovApply').disabled=locked||!draft.dirty||!draft.valid" in sync
    assert "input.disabled=locked" in sync

    apply_handler = _arrow_block(r"\$\('ovApply'\)\.onclick\s*=\s*\(\)\s*=>\s*\{")
    guard = "if(!draft.panel||!draft.valid||!draft.dirty)return"
    assert guard in _compact(apply_handler)


def test_part_mutation_is_transactional_and_rolls_back_spec_and_undo() -> None:
    mutation = _compact(_function("commitPartMutation"))
    assert "beforeSpec=JSON.stringify(SPEC)" in mutation
    assert "undoBefore=UNDO.slice()" in mutation
    assert mutation.index("pushUndo()") < mutation.index("mutate()") < mutation.index("awaitapply()")
    assert "!generated||generated.stale||!generated.viewer" in mutation
    assert "SPEC=JSON.parse(beforeSpec)" in mutation
    assert "UNDO.splice(0,UNDO.length,...undoBefore)" in mutation
    assert mutation.count("syncModelEditLock()") >= 2
    assert "partEditBusy=false" in mutation


def test_add_transform_reset_and_delete_have_distinct_semantics() -> None:
    override_state = _compact(_function("selectedPartOverrides"))
    assert "item.action==='add'" in override_state
    assert "(item.action||'transform')==='transform'" in override_state

    set_override = _compact(_function("setOverride"))
    assert "(item.action||'transform')==='transform'" in set_override
    assert "if(!override){override={panel:name};SPEC.overrides.push(override);}" in set_override

    reset = _compact(_function("clearOverride"))
    assert "item.panel===name&&(item.action||'transform')==='transform'" in reset
    assert "item.panel!==name" not in reset

    delete = _compact(_function("deleteSelectedPart"))
    assert "wasAdded=selectedPartOverrides(name).added" in delete
    assert "filter(item=>item.panel!==name)" in delete
    assert "if(!wasAdded)SPEC.overrides.push({panel:name,action:'delete'})" in delete


def test_regeneration_restores_selection_only_on_the_current_payload() -> None:
    apply = _compact(_function("apply"))
    assert "requestId!==generateRequestSeq||JSON.stringify(SPEC)!==requestSpecJson" in apply
    assert "restorePartName=" in apply
    assert "p.viewer.panels.findIndex(panel=>panel.name===restorePartName)" in apply
    assert "if(restoreIndex>=0)scene3d.select(restoreIndex)" in apply
    assert apply.index("paint(p)") < apply.index("scene3d.select(restoreIndex)")


def test_detail_drawing_uses_a_workspace_request_token() -> None:
    drawing = _compact(_function("openSelectedPartDrawing"))
    assert "switchTab('draw',false)" in drawing
    assert "requestId=++workspaceViewRequestSeq" in drawing
    assert "detailRequestId=++detailDrawingRequestSeq" in drawing
    assert "requestId!==workspaceViewRequestSeq||detailRequestId!==detailDrawingRequestSeq" in drawing
    assert "!drawOn||!current||current.name!==name" in drawing
    assert "body:JSON.stringify({spec:SPEC,panel:name})" in drawing
    assert "!r.ok||!data.svg" in drawing
    assert "requestId===workspaceViewRequestSeq" in drawing

    tabs = _compact(_function("switchTab"))
    assert "workspaceViewRequestSeq++" in tabs


def test_escape_cancels_dirty_exact_values_before_clearing_selection() -> None:
    escape = _arrow_block(
        r"document\.addEventListener\('keydown'\s*,\s*e\s*=>\s*\{\s*"
        r"if\(e\.key!==['\"]Escape['\"]\)return"
    )
    compact = _compact(escape)
    assert "exact&&exact.open&&draft&&draft.dirty" in compact
    assert "fillPartCoordinateInputs(draft.panel)" in compact
    assert "exact.open=false" in compact
    reset_position = compact.index("exact.open=false")
    branch_return = compact.index("return", reset_position)
    assert reset_position < branch_return < compact.index("scene3d.select(null)")


def test_chat_and_part_mutations_share_one_interaction_lock() -> None:
    predicate = _compact(_function("modelMutationLocked"))
    assert "returnchatBusy||partEditBusy" in predicate

    lock = _compact(_function("syncModelEditLock"))
    assert "locked=modelMutationLocked()" in lock
    assert "$('sideScroll').inert=locked" not in lock
    assert "setModelMutationControlsLocked(locked)" in lock
    assert "item.panel.inert=key!==rightPanelMode" in lock
    assert "view.style.pointerEvents" not in lock
    assert "syncViewportStatus()" in lock

    mutation_controls = _compact(_function("modelMutationControls"))
    for control in ("#projRen", "#projDup", "#projShare", "#rightViewProperties"):
        assert control in mutation_controls
    for safe_control in ("#projCat", "#profileChip", "#profileLogout", "#projSel"):
        assert safe_control not in mutation_controls

    chat = _compact(_function("setChatBusy"))
    assert "syncModelEditLock()" in chat
    assert "if(currentSelectedPart())syncPartEditState()" in chat

    transform = _arrow_block(r"scene3d\.onTransform\s*=\s*\(name\s*,\s*delta\)\s*=>\s*\{")
    assert "modelMutationLocked()" in transform
    assert "setOverride(name,null,delta)" in transform
