"""Authenticated Studio keeps sessions and company catalogs isolated."""

from __future__ import annotations

import http.client
import json
import sys
import threading
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from unittest.mock import patch

from argon2 import PasswordHasher, Type

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.admin import CSRF_COOKIE, SESSION_COOKIE, SUPPORT_COOKIE
from src.identity import IdentityStore
from src.studio import _Studio, make_handler
from http.server import ThreadingHTTPServer


ADMIN_PASSWORD = "platform password long enough"


class Browser:
    def __init__(self, port: int) -> None:
        self.port = port
        self.origin = f"http://127.0.0.1:{port}"
        self.cookies: dict[str, str] = {}

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        csrf: bool = False,
    ) -> tuple[int, dict[str, str], bytes]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers: dict[str, str] = {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if method.upper() not in {"GET", "HEAD"}:
            headers["Origin"] = self.origin
        if csrf:
            headers["X-CSRF-Token"] = self.cookies[CSRF_COOKIE]
        if self.cookies:
            headers["Cookie"] = "; ".join(
                f"{key}={value}" for key, value in self.cookies.items()
            )
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        raw_headers = response.getheaders()
        result = response.status, {key: value for key, value in raw_headers}, response.read()
        connection.close()
        for key, value in raw_headers:
            if key.casefold() != "set-cookie":
                continue
            parsed = SimpleCookie()
            parsed.load(value)
            for name, morsel in parsed.items():
                if morsel["max-age"] == "0" or not morsel.value:
                    self.cookies.pop(name, None)
                else:
                    self.cookies[name] = morsel.value
        return result

    def login(self, email: str, password: str) -> dict[str, Any]:
        status, _headers, body = self.request(
            "POST", "/api/auth/login", {"email": email, "password": password}
        )
        assert status == 200, body
        result = json.loads(body)
        assert SESSION_COOKIE in self.cookies and CSRF_COOKIE in self.cookies
        return result


def _spec(name: str) -> dict[str, Any]:
    return {"schemaVersion": "paramspec-v1", "draft": True, "project_name": name}


def test_authenticated_studio_scopes_catalog_and_renders_profile(tmp_path: Path) -> None:
    hasher = PasswordHasher(
        time_cost=1,
        memory_cost=1024,
        parallelism=1,
        hash_len=16,
        salt_len=8,
        type=Type.ID,
    )
    identity_path = tmp_path / "identity.sqlite3"
    store = IdentityStore(identity_path, password_hasher=hasher)
    store.migrate()
    platform = store.bootstrap_platform_admin(
        "platform@akeda.test", "Platform Owner", ADMIN_PASSWORD
    )
    constanta = store.provision_organization(
        platform["id"], "Константа", "Алексей Лазарев", "alexey@example.test"
    )
    other = store.provision_organization(
        platform["id"], "Другая", "Другой Владелец", "other@example.test"
    )

    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    legacy_path = legacy_dir / "legacy.json"
    legacy_path.write_text(json.dumps(_spec("Общий старый каталог")), encoding="utf-8")
    tenant_root = tmp_path / "tenants"
    for provisioned, product in ((constanta, "Тумба Константы"), (other, "Шкаф другой компании")):
        target = tenant_root / provisioned["organization"]["id"] / "paramspecs"
        target.mkdir(parents=True)
        (target / "product.json").write_text(
            json.dumps(_spec(product), ensure_ascii=False), encoding="utf-8"
        )

    studio = _Studio(
        legacy_path,
        tmp_path / "out",
        identity_db=identity_path,
        tenant_root=tenant_root,
        require_auth=True,
    )
    # Keep tests fast after _Studio opens the same DB with production defaults.
    studio.identity_store = store
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(studio))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = int(server.server_address[1])
    try:
        browser = Browser(port)
        status, headers, _body = browser.request("GET", "/")
        assert status == 303 and headers["Location"] == "/login"
        assert browser.request("GET", "/login")[0] == 200

        login = browser.login(constanta["login"], constanta["starter_password"])
        assert login["redirect"] == "/index.html"
        status, _headers, page = browser.request("GET", "/index.html")
        html = page.decode("utf-8")
        assert status == 200
        assert "Константа" in html and "Алексей Лазарев" in html
        assert "Тумба Константы" in html
        assert "Общий старый каталог" not in html

        assert browser.request("POST", "/api/projects", {})[0] == 403
        status, _headers, projects_body = browser.request(
            "POST", "/api/projects", {}, csrf=True
        )
        assert status == 200
        projects = json.loads(projects_body)["projects"]
        assert [item["name"] for item in projects] == ["Тумба Константы"]

        ai_result = {
            "reply": "Ширина изменена",
            "spec": _spec("Тумба Константы после правки"),
            "changes": ["dimensions.width: 800 → 900"],
            "usage": {"model": "test-model", "prompt": 12, "completion": 4, "total": 16},
        }
        with patch("src.spec_chat.chat_edit", return_value=ai_result) as chat_edit:
            status, _headers, chat_body = browser.request(
                "POST",
                "/api/chat",
                {
                    "spec": _spec("Тумба Константы"),
                    "message": "Сделай шире",
                    "history": [{"role": "user", "text": "чужая клиентская история"}],
                    "provider": "mock",
                },
                csrf=True,
            )
            assert status == 200, chat_body
            assert chat_edit.call_args.args[2] == []

            browser.request(
                "POST",
                "/api/chat",
                {
                    "spec": _spec("Тумба Константы после правки"),
                    "message": "Добавь полку",
                    "history": [],
                    "provider": "mock",
                },
                csrf=True,
            )
            assert chat_edit.call_args.args[2] == [
                {"role": "user", "text": "Сделай шире"},
                {"role": "assistant", "text": "Ширина изменена"},
            ]

        status, _headers, history_body = browser.request(
            "POST", "/api/chat-history", {}, csrf=True
        )
        assert status == 200
        history = json.loads(history_body)
        assert [item["message"] for item in history["operations"]] == [
            "Сделай шире",
            "Добавь полку",
        ]
        assert all(
            item["organization_id"] == constanta["organization"]["id"]
            and item["actor_user_id"] == constanta["user"]["id"]
            and item["project_file"] == "product.json"
            and item["before_revision"]
            and item["after_revision"]
            for item in history["operations"]
        )

        other_browser = Browser(port)
        other_browser.login(other["login"], other["starter_password"])
        status, _headers, other_body = other_browser.request(
            "POST", "/api/projects", {}, csrf=True
        )
        assert status == 200
        assert [item["name"] for item in json.loads(other_body)["projects"]] == [
            "Шкаф другой компании"
        ]
        status, _headers, other_history_body = other_browser.request(
            "POST", "/api/chat-history", {}, csrf=True
        )
        assert status == 200
        assert json.loads(other_history_body)["operations"] == []

        audit = store.list_audit(platform["id"], constanta["organization"]["id"])
        ai_events = [item for item in audit if item["action"] == "studio.ai.completed"]
        assert len(ai_events) == 2
        assert ai_events[0]["actor_user_id"] == constanta["user"]["id"]
        assert ai_events[0]["metadata"]["provider"] == "test-model"
        assert "message" not in ai_events[0]["metadata"]

        platform_browser = Browser(port)
        platform_login = platform_browser.login("platform@akeda.test", ADMIN_PASSWORD)
        assert platform_login["redirect"] == "/admin"
        company_view = store.enter_company_view(
            platform["id"],
            constanta["organization"]["id"],
            primary_session_id=platform_login["session"]["id"],
        )
        platform_browser.cookies[SUPPORT_COOKIE] = company_view["id"]
        status, _headers, support_page = platform_browser.request("GET", "/index.html")
        support_html = support_page.decode("utf-8")
        assert status == 200
        assert "Вы просматриваете компанию" in support_html
        assert "Тумба Константы" in support_html
        assert platform_browser.request(
            "POST", "/api/save", {"spec": _spec("Нельзя записать")}, csrf=True
        )[0] == 403

        assert browser.request("POST", "/api/auth/logout", {}, csrf=True)[0] == 200
        assert SESSION_COOKIE not in browser.cookies
        assert browser.request("GET", "/index.html")[0] == 303
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
