"""Контракт очереди системных уведомлений Studio (MEB-101)."""

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
        self.by_id: dict[str, tuple[str, dict[str, str | None]]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if node_id := values.get("id"):
            self.by_id[node_id] = (tag, values)


def _dom() -> _Dom:
    dom = _Dom()
    dom.feed(PAGE)
    return dom


def _function(name: str, length: int = 3200) -> str:
    match = re.search(rf"function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{", PAGE)
    assert match, f"Не найдена функция {name}"
    return PAGE[match.start() : match.start() + length]


def test_notification_rail_has_one_live_current_message_and_a_hidden_history() -> None:
    dom = _dom()
    assert dom.by_id["toast"][0] == "div"
    assert dom.by_id["toastCurrent"] == (
        "div",
        {"id": "toastCurrent", "role": "status", "aria-live": "polite", "aria-atomic": "true"},
    )
    assert dom.by_id["toastMessage"][0] == "span"
    history_tag, history_attrs = dom.by_id["toastHistory"]
    assert history_tag == "ol"
    assert "hidden" in history_attrs
    button_tag, button_attrs = dom.by_id["toastHistoryToggle"]
    assert button_tag == "button"
    assert button_attrs.get("aria-controls") == "toastHistory"


def test_notifications_queue_and_retain_recent_history_without_losing_progress_result() -> None:
    toast = _function("toast")
    assert "NOTICE_HISTORY_LIMIT=8" in PAGE
    assert "NOTICE_QUEUE_LIMIT=6" in PAGE
    assert "noticeHistory.push(notice)" in toast
    assert "noticeQueue.push(notice);showNextNotice();" in toast
    assert "activeNotice&&activeNotice.sticky" in toast
    assert "hideCurrentNotice({showNext:false})" in toast

    current = _function("showNextNotice")
    assert "activeNotice=noticeQueue.shift()" in current
    assert "setTimeout(()=>hideCurrentNotice(),activeNotice.bad?5000:2800)" in current


def test_notification_history_is_optional_and_respects_reduced_motion() -> None:
    history = _function("renderNoticeHistory")
    assert "const hasHistory=noticeHistory.length>0" in history
    assert "toggle.hidden=!hasHistory" in history
    assert "list.hidden=true" in history
    assert "toastBox.classList.toggle('has-history',hasHistory)" in history
    assert "#toast.has-history:not(.is-visible)" in PAGE
    assert "@media (prefers-reduced-motion:reduce){#toast{transition:none}}" in PAGE
