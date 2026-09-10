"""Статический контракт левой панели и AI-композера Studio.

Browser parity остаётся обязательным, а эти проверки быстро ловят потерю
существующих контролов, контекста выбранной детали и восстановления команды
после сетевой ошибки.
"""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.studio import PAGE  # noqa: E402


# Идентификаторы функционального baseline до визуального среза. В набор входят
# и элементы, которые создаются шаблоном карточки выбранной детали.
LEGACY_FUNCTIONAL_IDS = {
    "addSec",
    "aiProvider",
    "app",
    "applyRaw",
    "archFields",
    "archSel",
    "badges",
    "bom",
    "btnB3d",
    "btnCfrn",
    "btnDeliver",
    "btnFixAll",
    "btnToggleOpenAll",
    "btnPrint",
    "btnSave",
    "btnUndo",
    "builds",
    "catCats",
    "catClose",
    "catGrid",
    "catHead",
    "catQ",
    "catalog",
    "cbDims",
    "cbHoles",
    "cbHw",
    "cbTex",
    "cbXray",
    "chatAttach",
    "chatFile",
    "chatImgs",
    "chatMsg",
    "chatSend",
    "chatlog",
    "decorHint",
    "decorList",
    "decorQ",
    "draw",
    "emptyState",
    "errors",
    "esFile",
    "esUpload",
    "estTable",
    "estTotal",
    "explode",
    "f_code",
    "f_color",
    "f_d",
    "f_facade_color",
    "f_gap",
    "f_h",
    "f_hoff",
    "f_hsize",
    "f_legs",
    "f_t",
    "f_w",
    "fs_arch",
    "fs_chat",
    "fs_est",
    "fs_hw",
    "fs_part",
    "fs_sections",
    "hud",
    "hwBadge",
    "hwSlots",
    "main",
    "ovApply",
    "ovDelete",
    "ovDetail",
    "ovReset",
    "partCard",
    "projCat",
    "projDup",
    "projNew",
    "projRen",
    "projSel",
    "rawspec",
    "rightside",
    "sections",
    "side",
    "stats",
    "swCarcass",
    "swFacade",
    "tab3d",
    "tabDraw",
    "tabNest",
    "tabs",
    "toast",
    "tokenCount",
    "verRestore",
    "verSel",
    "view3d",
    "views",
}


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
        self.by_id: dict[str, tuple[str, dict[str, str | None]]] = {}
        self.ancestors_by_id: dict[str, tuple[str, ...]] = {}
        self._stack: list[tuple[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        node_id = attr_map.get("id")
        if node_id:
            self.by_id[node_id] = (tag, attr_map)
            self.ancestors_by_id[node_id] = tuple(
                ancestor_id
                for _ancestor_tag, ancestor_id in self._stack
                if ancestor_id is not None
            )
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


def _section(start_marker: str, end_marker: str) -> str:
    start = PAGE.index(start_marker)
    end = PAGE.index(end_marker, start + len(start_marker))
    return PAGE[start:end]


def _function(name: str, limit: int = 2200) -> str:
    match = re.search(rf"(?:async\s+)?function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{", PAGE)
    assert match, f"Не найдена JS-функция {name}"
    return PAGE[match.start():match.start() + limit]


def _css_rule(selector: str) -> str:
    match = re.search(
        rf"(?:^|\n)\s*{re.escape(selector)}\s*\{{(?P<body>[^}}]*)\}}",
        PAGE,
        flags=re.DOTALL,
    )
    assert match, f"Не найдено CSS-правило {selector}"
    return match.group("body")


def test_left_slice_keeps_existing_functional_ids():
    ids = set(re.findall(r"""\bid=["']([^"']+)""", PAGE))
    missing = LEGACY_FUNCTIONAL_IDS - ids
    assert not missing, f"Визуальный срез удалил функциональные id: {sorted(missing)}"


def test_left_and_ai_structure_has_stable_named_regions():
    dom = _dom()
    expected_tags = {
        "sideScroll": "div",
        "sidePinned": "div",
        "modelState": "section",
        "operationLog": "section",
        "operationLogTitle": "span",
        "chatStatusTray": "div",
        "chatStateMark": "span",
        "chatResultActions": "div",
        "chatShowLog": "button",
        "chatUndoQuick": "button",
        "chatSurface": "div",
        "chatProgress": "div",
        "chatContext": "div",
        "chatContextLabel": "b",
        "chatComposer": "div",
        "chatState": "div",
        "chatSendLabel": "span",
    }
    for node_id, expected_tag in expected_tags.items():
        tag, _attrs = dom.by_id[node_id]
        assert tag == expected_tag

    _, chat = dom.by_id["fs_chat"]
    assert chat.get("aria-busy") == "false"
    _, state = dom.by_id["chatState"]
    assert state.get("role") == "status"
    assert state.get("aria-live") == "polite"
    _, log = dom.by_id["chatlog"]
    assert log.get("role") == "log"
    assert log.get("aria-live") == "polite"
    assert log.get("aria-relevant") == "additions text"
    assert log.get("tabindex") == "0"
    _, progress = dom.by_id["chatProgress"]
    assert progress.get("aria-hidden") == "true"
    _, result_actions = dom.by_id["chatResultActions"]
    assert "hidden" in result_actions
    _, quick_undo = dom.by_id["chatUndoQuick"]
    assert "hidden" in quick_undo


def test_floating_composer_and_persistent_log_live_in_separate_regions():
    dom = _dom()

    composer_ancestors = dom.ancestors_by_id["fs_chat"]
    assert "main" in composer_ancestors
    assert "side" not in composer_ancestors
    assert "sideScroll" not in composer_ancestors
    assert "view3d" not in composer_ancestors

    log_ancestors = dom.ancestors_by_id["chatlog"]
    assert "operationLog" in log_ancestors
    assert "sideScroll" not in log_ancestors
    assert "sidePinned" not in log_ancestors
    assert "side" in log_ancestors
    assert "fs_chat" not in log_ancestors

    for fixed_id in ("studioBrand", "fs_project", "modelState"):
        assert "sidePinned" in dom.ancestors_by_id[fixed_id]
        assert "operationLog" not in dom.ancestors_by_id[fixed_id]
    assert "sideScroll" in dom.ancestors_by_id["fs_part"]
    assert "sidePinned" not in dom.ancestors_by_id["fs_part"]


def test_operation_history_is_an_engineering_ledger_not_nested_cards():
    log = _css_rule("#chatlog")
    container = _css_rule("#operationLog")
    side_scroll = _css_rule("#sideScroll")
    pinned = _css_rule("#sidePinned")
    part = _css_rule("#side #fs_part")
    errors = _css_rule("#modelState #errors:not(:empty)")
    record = _css_rule(".operation-record")
    assert "max-height" not in log
    assert re.search(r"\boverflow-y\s*:\s*auto", log)
    assert "min-height:0" in log
    assert "overscroll-behavior:contain" in log
    assert "flex:1 1 220px" in container
    assert "overflow:hidden" in container
    assert "overflow:hidden" in side_scroll
    assert "flex:0 0 auto" in pinned
    assert "overflow" not in pinned
    assert "overflow-y:auto" in part
    assert "overscroll-behavior:contain" in part
    assert "overflow-y:auto" in errors
    assert "outline:2px solid var(--accent)" in _css_rule("#chatlog:focus-visible")
    assert "border-top" in record
    for decorative_card_property in ("border-radius", "box-shadow", "background"):
        assert decorative_card_property not in record

    create = _function("createOperation", limit=6200)
    assert "operationNode('article','operation-record is-pending')" in create
    assert "operationContextSnapshot" in create
    assert "attachmentMeta" in create
    assert "OPERATIONS.set(id,operation)" in create
    assert "OPERATION_LIMIT" in create
    assert "textContent" in _function("operationNode")
    assert "log.scrollTop=log.scrollHeight" in create
    assert "el.scrollIntoView" not in create


def test_operation_history_has_real_diff_checks_and_revision_safe_actions():
    finish = _function("finishOperation", limit=3400)
    checks = _function("currentCheckSnapshot", limit=3100)
    show = _function("showOperationTarget", limit=1300)
    assert "renderOperationChanges(operation,changes)" in finish
    assert "checkSnapshot.text" in finish
    assert "lastPayload.issues" in checks
    assert "lastPayload.refs" in checks
    assert "lastPayload.viewer.panels" in show
    assert "scene3d.select(index)" in show
    assert "switchTab('3d')" in show


def test_command_bar_is_a_safe_floating_overlay_without_glass_blur():
    dock = _css_rule("#fs_chat")
    assert re.search(r"\bposition\s*:\s*absolute\b", dock)
    assert re.search(r"\bleft\s*:\s*50%", dock)
    assert re.search(
        r"\bbottom\s*:\s*(?:\d+px|calc\([^;]*--viewport-status-height[^;]*\))",
        dock,
    )
    assert re.search(r"\btransform\s*:\s*translateX\(\s*-50%\s*\)", dock)
    assert re.search(r"\bpointer-events\s*:\s*none\b", dock)
    assert "width:min(" in re.sub(r"\s+", "", dock)

    surface = _css_rule("#chatSurface")
    assert re.search(r"\bpointer-events\s*:\s*auto\b", surface)
    assert re.search(r"\bbackground\s*:\s*rgba\(", surface)

    normalized_page = PAGE.lower().replace(" ", "")
    assert "backdrop-filter:" not in normalized_page
    assert "-webkit-backdrop-filter:" not in normalized_page


def test_status_and_result_actions_follow_real_command_state():
    state = _function("setChatState")
    assert "$('fs_chat')" in state
    for class_name in ("has-state", "state-error", "state-success"):
        assert class_name in state
    assert "$('chatResultActions')" in state
    assert re.search(r"actions\.hidden\s*=\s*mode\s*!==\s*['\"]success['\"]", state)
    assert "chatQuickUndoDepth" in state
    assert "refreshUndoState()" in state
    assert "canUndo" in state

    busy = _function("setChatBusy")
    assert re.search(
        r"classList\.toggle\(\s*['\"]is-busy['\"]\s*,\s*chatBusy\s*\)",
        busy,
    )
    assert "setChatState" in busy

    assert re.search(
        r"\$\(\s*['\"]chatShowLog['\"]\s*\)\.onclick\s*=",
        PAGE,
    )
    show_log = _section("$('chatShowLog').onclick=", "$('chatUndoQuick').onclick=")
    assert "history.scrollTop=history.scrollHeight" in show_log
    assert "history.focus({preventScroll:true})" in show_log
    assert re.search(
        r"\$\(\s*['\"]chatUndoQuick['\"]\s*\)\.onclick\s*=\s*\(\)\s*=>"
        r"\$\(\s*['\"]btnUndo['\"]\s*\)\.click\(\)",
        PAGE,
    )


def test_quick_undo_is_bound_to_the_current_revision_and_undo_is_awaited():
    quick_match = _function("quickUndoRevisionMatches")
    operation_match = _function("operationRevisionMatches")
    assert "chatQuickUndoDepth" in PAGE
    assert "chatQuickUndoDepth===UNDO.length" in quick_match
    assert "UNDO[UNDO.length-1]===chatQuickUndoBeforeSpecJson" in quick_match
    assert "JSON.stringify(SPEC)===chatQuickUndoAfterSpecJson" in quick_match
    assert "operation.undoDepthAfter===UNDO.length" in operation_match
    assert "UNDO[UNDO.length-1]===operation.undoBeforeSpecJson" in operation_match
    assert "JSON.stringify(SPEC)===operation.afterSpecJson" in operation_match
    undo_handler = _function("undoLastChange", limit=2800)
    assert "await apply()" in undo_handler
    assert re.search(r"\$\(['\"]btnUndo['\"]\)\.onclick\s*=\s*undoLastChange", PAGE)


def test_ai_apply_is_transactional_and_finishes_operation_only_after_regeneration():
    run_chat = _function("runChat", limit=7600)
    assert "rollbackSpec=JSON.stringify(SPEC)" in run_chat
    assert "rollbackUndoDepth=UNDO.length" in run_chat
    assert "UNDO.splice(rollbackUndoDepth)" in run_chat
    apply_pos = run_chat.index("await apply();")
    viewer_check_pos = run_chat.index("!generated.viewer")
    finish_pos = run_chat.index("finishOperation(operation")
    assert apply_pos < viewer_check_pos < finish_pos
    assert "createOperation(m,{images:imgs})" in run_chat
    assert "addMsg(" not in run_chat
    assert "JSON.stringify(SPEC)!==requestSpecJson" in run_chat


def test_fix_all_is_one_aggregated_non_atomic_operation():
    fix_all = _function("fixAll", limit=9800)
    assert "createOperation('Автоматическое исправление проверок'" in fix_all
    assert "allChanges" in fix_all
    assert "appliedPasses" in fix_all
    assert "if(p.error&&!p.spec)throw new Error" in fix_all
    assert "finishOperation(operation" in fix_all
    assert "canUndo:false" in fix_all
    assert "addMsg(" not in fix_all
    assert "JSON.stringify(SPEC)!==passSpecJson" in fix_all


def test_selection_updates_chat_context_without_reopening_inspector():
    selection = _section(
        "/* ---------- выбор детали кликом (AKD-120) ---------- */",
        "/* ---------- каталог проектов (AKD-132) ---------- */",
    )
    assert "setRightPanel(" not in selection
    assert "SELECTED_PART" in selection
    assert "$('chatContext')" in selection
    assert "$('chatContextLabel')" in selection
    assert re.search(r"classList\.toggle\(\s*['\"]selected['\"]", selection)
    assert "sel.panel.name" in selection
    assert "Всё изделие" in selection


def test_busy_helper_exposes_state_and_keeps_a_cancel_action_available():
    busy = _function("setChatBusy")
    assert re.search(
        r"\$\(\s*['\"]fs_chat['\"]\s*\)\.setAttribute\(\s*['\"]aria-busy['\"]",
        busy,
    )
    for control_id in ("chatMsg", "chatAttach", "aiProvider"):
        assert re.search(rf"\$\(\s*['\"]{control_id}['\"]\s*\)", busy)
    assert re.search(r"\.disabled\s*=\s*chatBusy", busy)
    assert "syncModelEditLock()" in busy
    assert "refreshUndoState()" in busy

    primary = _function("syncChatPrimaryAction")
    assert "activeChatController" in primary
    assert "activeChatController.signal.aborted" in primary
    assert "Остановить" in primary
    assert "['user','navigation'].includes(chatAbortReason)" in primary
    assert "Выполняю…" in primary
    assert "is-cancel" in primary
    assert "button.disabled=chatBusy?!canCancel:partEditBusy" in primary


def test_busy_ai_request_keeps_3d_interactive_and_blocks_only_shift_drag_mutation():
    lock = _function("syncModelEditLock")
    assert "view.style.pointerEvents" not in lock
    assert "view.inert" not in lock
    assert "syncViewportStatus()" in lock

    guard = re.search(
        r"view\.addEventListener\(\s*['\"]pointerdown['\"]\s*,(?P<body>.*?)\}\s*,\s*true\s*\);",
        PAGE,
        flags=re.DOTALL,
    )
    assert guard, "Нет capture-защиты от Shift+drag во время пересчёта"
    body = guard.group("body")
    assert "modelMutationLocked()" in body
    assert "event.shiftKey" in body
    assert "event.preventDefault()" in body
    assert "event.stopPropagation()" in body

    transform = re.search(
        r"scene3d\.onTransform\s*=\s*\(name,delta\)\s*=>\s*\{(?P<body>.*?)\n\};",
        PAGE,
        flags=re.DOTALL,
    )
    assert transform
    assert "rebuild(lastPayload.viewer)" in transform.group("body")


def test_ai_request_has_user_cancel_and_bounded_wait():
    request = _function("requestChat")
    cancel = _function("cancelActiveChatRequest")
    send = _function("sendChat")
    assert "CHAT_REQUEST_TIMEOUT_MS=130000" in PAGE
    assert "signal:controller.signal" in request
    assert "controller.abort()" in request
    assert "chatAbortReason='timeout'" in request
    assert "reason='user'" in cancel
    assert "chatAbortReason=reason" in cancel
    assert "fetch('/api/chat/cancel'" in cancel
    assert "activeChatController.abort()" in cancel
    assert "if(chatBusy){cancelActiveChatRequest();return;}" in send
    assert "Остановлено пользователем. Модель не изменена." in PAGE


def test_ai_navigation_is_confirmed_cancelled_and_guarded_against_late_results():
    transition = _function("prepareWorkspaceChange")
    assert "confirm(" in transition
    assert "cancelActiveChatRequest('navigation')" in transition
    assert "while(chatBusy" in transition

    run_chat = _function("runChat", limit=9000)
    assert "chatWorkspaceGeneration!==requestGeneration" in run_chat
    assert "JSON.stringify(SPEC)!==requestSpecJson" in run_chat
    assert "abortReason==='navigation'" in run_chat
    assert "beforeunload" in PAGE

    catalog = _function("openCatalog")
    assert "modelMutationLocked()" not in catalog
    assert "partEditBusy" in catalog
    assert "prepareWorkspaceChange('открыть выбранное изделие')" in PAGE
    assert "prepareWorkspaceChange('создать новое изделие')" in PAGE
    assert "prepareWorkspaceChange('выйти из аккаунта')" in PAGE


def test_chat_http_error_restores_command_and_attachments():
    run_chat = _function("runChat", limit=9000)
    assert re.search(r"if\s*\(\s*!r\.ok\s*\)\s*throw", run_chat)

    catch_start = run_chat.index("}catch")
    finally_start = run_chat.index("}finally", catch_start)
    recovery = run_chat[catch_start:finally_start]
    assert "PENDING_IMGS.unshift(...imgs)" in recovery
    assert "$('chatMsg').value=m" in recovery
    assert "renderImgs()" in recovery
    assert "Команда не выполнена" in recovery
    assert "setChatBusy(false)" in run_chat[finally_start:]


def test_diagnostics_never_call_errors_questions_or_generic_problems():
    assert "вопрос" not in PAGE.lower()
    assert ">Исправить проблемы<" not in PAGE
    assert re.search(r">\s*Запустить автоисправление\s*<", PAGE)
    assert "Применено · есть замечания" in PAGE


def test_ai_commands_do_not_discard_dirty_exact_part_values():
    guard = _function("requireCleanPartDraft")
    run_chat = _function("runChat")
    send_chat = _function("sendChat")
    fix_all = _function("fixAll")
    assert "readPartDraft()" in guard
    assert "draft.dirty" in guard
    assert "примените или отмените точные значения" in guard
    assert "if(!requireCleanPartDraft())return false;" in run_chat
    assert send_chat.index("if(!requireCleanPartDraft())return;") < send_chat.index("$('chatMsg').value='';")
    assert "if(!requireCleanPartDraft())return;" in fix_all


def test_composer_supports_ctrl_and_command_enter():
    shortcut = re.search(
        r"\$\(\s*['\"]chatMsg['\"]\s*\)\.addEventListener\(\s*['\"]keydown['\"]"
        r"\s*,(?P<body>.*?)\}\s*\);",
        PAGE,
        flags=re.DOTALL,
    )
    assert shortcut, "Не найден keyboard handler AI-композера"
    body = shortcut.group("body")
    assert re.search(r"e\.key\s*===\s*['\"]Enter['\"]", body)
    assert "e.ctrlKey" in body and "e.metaKey" in body
    assert "e.preventDefault()" in body
    assert "sendChat()" in body


def test_studio_still_uses_shared_mebel_scene_engine():
    dom = _dom()
    assert "main" in dom.ancestors_by_id["view3d"]
    assert "view3d" not in dom.ancestors_by_id["fs_chat"]
    assert "__SCENE_JS__" in PAGE
    assert re.search(r"\bscene3d\s*=\s*MebelScene\(\s*view\s*\)", PAGE)
