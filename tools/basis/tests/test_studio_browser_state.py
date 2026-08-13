"""Real-browser coverage for the Studio state model (MEB-101)."""

from __future__ import annotations

import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Iterator

import pytest
from playwright.sync_api import Browser, Page, expect, sync_playwright


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.studio import _Studio, make_handler  # noqa: E402


PRODUCTION_BUTTONS = ("btnCfrn", "btnB3d", "btnDeliver")


@pytest.fixture(scope="module")
def studio_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    workspace = tmp_path_factory.mktemp("meb101-browser")
    source = ROOT / "paramspecs" / "komi_72_tumba_podkatnaya.json"
    spec_path = workspace / "current.json"
    spec_path.write_text(
        json.dumps(json.loads(source.read_text(encoding="utf-8")), ensure_ascii=False),
        encoding="utf-8",
    )
    out_dir = workspace / "out"
    out_dir.mkdir()
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), make_handler(_Studio(spec_path, out_dir))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture(scope="module")
def chromium() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--enable-unsafe-swiftshader", "--use-angle=swiftshader"],
        )
        try:
            yield browser
        finally:
            browser.close()


@pytest.fixture()
def studio_page(chromium: Browser, studio_url: str) -> Iterator[Page]:
    context = chromium.new_context(viewport={"width": 1440, "height": 960})
    page = context.new_page()
    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.goto(studio_url, wait_until="domcontentloaded")
    page.wait_for_function("() => viewportModelState === 'ready'")
    try:
        yield page
        assert page_errors == []
    finally:
        context.close()


def _expect_production(page: Page, *, enabled: bool) -> None:
    for button_id in PRODUCTION_BUTTONS:
        button = page.locator(f"#{button_id}")
        if enabled:
            expect(button).to_be_enabled()
        else:
            expect(button).to_be_disabled()


def _set_width_and_schedule(page: Page, width: int) -> None:
    page.evaluate(
        """width => {
            SPEC.dimensions.width = width;
            fillForm();
            schedule();
        }""",
        width,
    )


def test_state_transitions_label_last_valid_model_and_gate_production(
    studio_page: Page,
) -> None:
    page = studio_page
    status = page.locator("#viewportModelStateLong")

    expect(status).to_contain_text("Модель актуальна")
    _expect_production(page, enabled=True)

    page.evaluate(
        """() => {
            const originalFetch = window.fetch.bind(window);
            window.__meb101OriginalFetch = originalFetch;
            window.__meb101ReleaseGenerate = null;
            window.fetch = async (input, init) => {
                const url = typeof input === 'string' ? input : input.url;
                if (url === '/api/generate') {
                    await new Promise(resolve => {
                        window.__meb101ReleaseGenerate = resolve;
                    });
                }
                return originalFetch(input, init);
            };
        }"""
    )
    _set_width_and_schedule(page, 300)

    expect(status).to_contain_text("Изменения ещё не пересчитаны")
    _expect_production(page, enabled=False)
    page.wait_for_function("() => viewportModelState === 'recalculating'")
    expect(status).to_contain_text("Пересчитываю изменения")
    _expect_production(page, enabled=False)

    page.evaluate("() => window.__meb101ReleaseGenerate()")
    page.wait_for_function("() => viewportModelState === 'ready'")
    expect(status).to_contain_text("Модель актуальна")
    _expect_production(page, enabled=True)

    page.evaluate("() => { window.fetch = window.__meb101OriginalFetch || window.fetch; }")
    _set_width_and_schedule(page, 100)
    page.wait_for_function("() => viewportModelState === 'blocked'")
    expect(status).to_contain_text("производство заблокировано")
    _expect_production(page, enabled=False)

    _set_width_and_schedule(page, 300)
    page.wait_for_function("() => viewportModelState === 'ready'")
    last_valid_panels = page.evaluate("() => lastPayload.viewer.panels.length")
    assert last_valid_panels > 0

    page.evaluate(
        """() => {
            const originalFetch = window.fetch.bind(window);
            window.fetch = (input, init) => {
                const url = typeof input === 'string' ? input : input.url;
                if (url === '/api/generate') {
                    return Promise.reject(new Error('test recalculation failure'));
                }
                return originalFetch(input, init);
            };
        }"""
    )
    _set_width_and_schedule(page, 301)
    page.wait_for_function("() => viewportModelState === 'stale'")
    expect(status).to_contain_text("показана предыдущая модель")
    expect(page.locator("#componentsStateNotice")).to_contain_text(
        "Показана предыдущая модель"
    )
    assert page.evaluate("() => lastPayload.viewer.panels.length") == last_valid_panels
    _expect_production(page, enabled=False)


def test_notification_queue_retains_history_and_replaces_sticky_progress(
    studio_page: Page,
) -> None:
    page = studio_page
    page.evaluate(
        """() => {
            noticeQueue.length = 0;
            noticeHistory.length = 0;
            hideCurrentNotice({showNext: false});
            renderNoticeHistory();
            toast('Первое сообщение');
            toast('Второе сообщение');
            toast('Третье сообщение');
        }"""
    )

    expect(page.locator("#toastHistoryToggle")).to_have_text("Уведомления · 3")
    visible_sequence = page.evaluate(
        """() => {
            clearTimeout(noticeTimer);
            const messages = [$('toastMessage').textContent];
            hideCurrentNotice();
            clearTimeout(noticeTimer);
            messages.push($('toastMessage').textContent);
            hideCurrentNotice();
            clearTimeout(noticeTimer);
            messages.push($('toastMessage').textContent);
            return messages;
        }"""
    )
    assert visible_sequence == [
        "Первое сообщение",
        "Второе сообщение",
        "Третье сообщение",
    ]
    page.locator("#toastHistoryToggle").click()
    expect(page.locator("#toastHistory li")).to_have_text(
        ["Третье сообщение", "Второе сообщение", "Первое сообщение"]
    )

    page.evaluate(
        """() => {
            noticeQueue.length = 0;
            noticeHistory.length = 0;
            hideCurrentNotice({showNext: false});
            renderNoticeHistory();
            toast('Пересчёт выполняется', false, true);
            toast('Пересчёт завершён');
        }"""
    )
    expect(page.locator("#toastMessage")).to_have_text("Пересчёт завершён")
    page.locator("#toastHistoryToggle").click()
    expect(page.locator("#toastHistory li")).to_have_text(
        ["Пересчёт завершён", "Пересчёт выполняется"]
    )
