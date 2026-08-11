"""Visual and interaction contract for the authenticated Studio login page."""

from __future__ import annotations

import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.studio import _studio_login_page  # noqa: E402


class _LoginDom(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.by_id: dict[str, tuple[str, dict[str, str | None]]] = {}
        self.images: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.by_id[str(values["id"])] = (tag, values)
        if tag == "img":
            self.images.append(values)


def _page() -> tuple[str, _LoginDom]:
    html = _studio_login_page()
    dom = _LoginDom()
    dom.feed(html)
    return html, dom


def test_login_brand_is_centered_and_uses_original_assets() -> None:
    html, dom = _page()
    sources = {image.get("src") for image in dom.images}
    assert "/assets/studio/akeda-studio-wordmark.png" in sources
    assert "/assets/studio/akeda-studio-mark.png" in sources
    assert "justify-content:center" in html
    assert "от ТЗ до производства" not in html
    assert "Рабочее пространство" not in html
    assert ">Вход в Studio<" not in html


def test_login_keeps_labels_and_moves_help_below_form() -> None:
    html, dom = _page()
    assert dom.by_id["email"][1].get("type") == "email"
    assert dom.by_id["password"][1].get("type") == "password"
    assert '<label for="email">Почта</label>' in html
    assert '<label for="password">Пароль</label>' in html
    assert html.index("</form>") < html.index(
        "Используйте логин сотрудника, выданный командой Akeda."
    )


def test_password_visibility_toggle_is_accessible_and_stateful() -> None:
    html, dom = _page()
    tag, toggle = dom.by_id["passwordToggle"]
    assert tag == "button"
    assert toggle.get("type") == "button"
    assert toggle.get("aria-label") == "Показать пароль"
    assert toggle.get("aria-pressed") == "false"
    assert "password.type=visible?'password':'text'" in html
    assert "passwordToggle.setAttribute('aria-pressed',String(!visible))" in html
    assert "visible?'Показать пароль':'Скрыть пароль'" in html

