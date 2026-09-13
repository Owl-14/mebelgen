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

import pytest

pytest.importorskip("argon2")
from argon2 import PasswordHasher, Type

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.admin import CSRF_COOKIE, SESSION_COOKIE, SUPPORT_COOKIE
from src.identity import IdentityStore
from src.studio import _Studio, make_handler
from src.webviewer import SCENE_JS
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
    for provisioned, product, version_width in (
        (constanta, "Тумба Константы", 811),
        (other, "Шкаф другой компании", 922),
    ):
        target = tenant_root / provisioned["organization"]["id"] / "paramspecs"
        target.mkdir(parents=True)
        (target / "product.json").write_text(
            json.dumps(_spec(product), ensure_ascii=False), encoding="utf-8"
        )
        version_spec = _spec(product)
        version_spec["dimensions"] = {
            "width": version_width,
            "depth": 400,
            "height": 750,
        }
        (target / "product.versions.json").write_text(
            json.dumps(
                [{"ts": "2026-08-08T12:00:00", "spec": version_spec}],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    designer = store.provision_member(
        platform["id"],
        constanta["organization"]["id"],
        "designer@constanta.test",
        "Проектировщик Константы",
        "designer",
    )
    reviewer = store.provision_member(
        platform["id"],
        constanta["organization"]["id"],
        "reviewer@constanta.test",
        "Наблюдатель Константы",
        "reviewer",
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
        assert 'id="profileCompanyAdmin"' in html
        assert "membership.role==='owner'" in html
        assert "Управление компанией" in html
        assert "Тумба Константы" in html
        assert "Общий старый каталог" not in html
        assert SCENE_JS in html

        status, _headers, me_body = browser.request("GET", "/api/auth/me")
        assert status == 200
        me = json.loads(me_body)
        assert me["authenticated"] is True
        assert me["user"]["id"] == constanta["user"]["id"]
        assert me["organization"]["id"] == constanta["organization"]["id"]

        assert browser.request("POST", "/api/projects", {})[0] == 403
        status, _headers, projects_body = browser.request(
            "POST", "/api/projects", {}, csrf=True
        )
        assert status == 200
        catalog_payload = json.loads(projects_body)
        projects = catalog_payload["projects"]
        assert [item["name"] for item in projects] == ["Тумба Константы"]
        assert projects[0]["updated_at"] > 0
        assert projects[0]["creator_user_id"] == constanta["user"]["id"]
        assert projects[0]["responsible_user_id"] == constanta["user"]["id"]
        assert projects[0]["author"] == "Алексей Лазарев"
        assert projects[0]["responsible"] == "Алексей Лазарев"
        assert catalog_payload["counts"] == {
            "all": 1,
            "mine": 1,
            "unassigned": 0,
            "archived": 0,
        }
        assert {member["user_id"] for member in catalog_payload["members"]} == {
            constanta["user"]["id"],
            designer["user"]["id"],
            reviewer["user"]["id"],
        }
        migrated = json.loads(
            (tenant_root / constanta["organization"]["id"] / "paramspecs" / "product.json")
            .read_text(encoding="utf-8")
        )
        assert migrated["catalog"]["creator_user_id"] == constanta["user"]["id"]
        assert migrated["catalog"]["responsible_user_id"] == constanta["user"]["id"]

        status, _headers, versions_body = browser.request(
            "POST", "/api/versions", {}, csrf=True
        )
        assert status == 200
        assert json.loads(versions_body)["versions"][0]["dims"] == "811×400×750"

        foreign_path = (
            f"../{other['organization']['id']}/paramspecs/product.json"
        )
        status, _headers, foreign_body = browser.request(
            "POST", "/api/open", {"file": foreign_path}, csrf=True
        )
        assert status == 404
        assert json.loads(foreign_body)["code"] == "not_found"
        assert "Шкаф другой компании" not in foreign_body.decode("utf-8")

        assert browser.request(
            "POST", "/api/rename", {"file": "product.json", "name": "Без CSRF"}
        )[0] == 403
        status, _headers, renamed_body = browser.request(
            "POST",
            "/api/rename",
            {"file": "product.json", "name": "Тумба Константы — рабочая"},
            csrf=True,
        )
        assert status == 200
        assert json.loads(renamed_body)["project"]["name"] == "Тумба Константы — рабочая"

        status, _headers, duplicate_body = browser.request(
            "POST",
            "/api/duplicate",
            {"file": "product.json", "stay_catalog": True},
            csrf=True,
        )
        assert status == 200
        duplicate = json.loads(duplicate_body)
        assert duplicate["file"] == "product_copy.json"
        status, _headers, projects_body = browser.request(
            "POST", "/api/projects", {}, csrf=True
        )
        catalog_after_actions = json.loads(projects_body)
        assert catalog_after_actions["current"] == "product.json"
        assert [item["name"] for item in catalog_after_actions["projects"]] == [
            "Тумба Константы — рабочая",
            "Тумба Константы — рабочая (копия)",
        ]

        status, _headers, conflict_body = browser.request(
            "POST",
            "/api/rename",
            {"file": "product_copy.json", "name": "Тумба Константы — рабочая"},
            csrf=True,
        )
        assert status == 409
        assert json.loads(conflict_body)["code"] == "name_conflict"

        status, _headers, assigned_body = browser.request(
            "POST",
            "/api/catalog/assign",
            {
                "file": "product_copy.json",
                "responsible_user_id": designer["user"]["id"],
            },
            csrf=True,
        )
        assert status == 200, assigned_body
        assert json.loads(assigned_body)["responsible"] == "Проектировщик Константы"

        status, _headers, foreign_archive_body = browser.request(
            "POST",
            "/api/catalog/archive",
            {"file": foreign_path, "reason": "Чужой tenant"},
            csrf=True,
        )
        assert status == 404
        assert json.loads(foreign_archive_body)["code"] == "not_found"

        status, _headers, archive_body = browser.request(
            "POST",
            "/api/catalog/archive",
            {"file": "product_copy.json", "reason": "Проверка обратимого архива"},
            csrf=True,
        )
        assert status == 200, archive_body
        archive_id = json.loads(archive_body)["archive_id"]
        status, _headers, archived_body = browser.request(
            "POST", "/api/projects", {"scope": "archived"}, csrf=True
        )
        assert status == 200
        archived_catalog = json.loads(archived_body)
        assert archived_catalog["counts"]["archived"] == 1
        assert archived_catalog["projects"][0]["archive_id"] == archive_id
        assert archived_catalog["projects"][0]["archive_reason"] == (
            "Проверка обратимого архива"
        )
        assert archived_catalog["projects"][0]["can_manage"] is True
        status, _headers, restore_body = browser.request(
            "POST",
            "/api/catalog/restore",
            {"archive_id": archive_id},
            csrf=True,
        )
        assert status == 200, restore_body
        assert json.loads(restore_body)["file"] == "product_copy.json"
        status, _headers, reassigned_body = browser.request(
            "POST",
            "/api/catalog/assign",
            {
                "file": "product_copy.json",
                "responsible_user_id": constanta["user"]["id"],
            },
            csrf=True,
        )
        assert status == 200, reassigned_body

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
                    "project_file": "product.json",
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
                    "project_file": "product.json",
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

        import_failure = {
            "reply": "AI не вернул полное изделие",
            "error": "AI не вернул полное изделие",
            "code": "create_paramspec_missing",
            "spec": None,
            "changes": [],
            "usage": {"model": "test-model", "total": 10},
        }
        with patch("src.spec_chat.chat_edit", return_value=import_failure):
            status, _headers, import_body = browser.request(
                "POST",
                "/api/import-tz",
                {"name": "tz.png", "data": "QUJD", "provider": "mock"},
                csrf=True,
            )
        assert status == 422, import_body
        import_error = json.loads(import_body)
        assert import_error["ok"] is False
        assert import_error["code"] == "create_paramspec_missing"
        assert import_error["error_code"] == import_error["code"]
        assert len(import_error["trace_id"]) == 32

        provider_failure = {
            "reply": "timeout at https://private-provider.invalid/account/secret",
            "error": "timeout at https://private-provider.invalid/account/secret",
            "code": "ai_provider_failed",
            "spec": None,
            "changes": [],
        }
        with patch("src.spec_chat.chat_edit", return_value=provider_failure):
            status, _headers, provider_body = browser.request(
                "POST",
                "/api/import-tz",
                {"name": "tz.png", "data": "QUJD", "provider": "mock"},
                csrf=True,
            )
        assert status == 502, provider_body
        provider_error = json.loads(provider_body)
        assert provider_error["code"] == "ai_provider_failed"
        assert "private-provider" not in provider_error["error"]
        assert len(provider_error["trace_id"]) == 32

        # AI-журнал рядом с identity DB: каждый вызов и фото ТЗ — в базе компании
        from contextlib import closing
        from src.ai_journal import AIJournal

        journal = AIJournal(tmp_path / "ai_journal.sqlite3")
        with closing(journal.connect()) as connection:
            calls = connection.execute(
                "SELECT * FROM ai_calls ORDER BY created_at"
            ).fetchall()
            intake = connection.execute("SELECT * FROM tz_intake").fetchall()
        assert [(call["workflow"], call["error_code"]) for call in calls] == [
            ("chat", ""),
            ("chat", ""),
            ("import_tz", "create_paramspec_missing"),
            ("import_tz", "ai_provider_failed"),
        ]
        assert {call["organization_id"] for call in calls} == {constanta["organization"]["id"]}
        assert calls[0]["message"] == "Сделай шире"
        assert json.loads(calls[0]["after_spec"])["project_name"] == "Тумба Константы после правки"
        assert [(item["source"], item["bytes"]) for item in intake] == [
            ("import_tz", 3), ("import_tz", 3),
        ]
        assert {item["call_id"] for item in intake} == {
            call["id"] for call in calls if call["workflow"] == "import_tz"
        }
        assert all((tmp_path / "ai_journal_files" / item["file"]).is_file() for item in intake)

        status, _headers, history_body = browser.request(
            "POST", "/api/chat-history", {"project_file": "product.json"}, csrf=True
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
        status, _headers, other_versions_body = other_browser.request(
            "POST", "/api/versions", {}, csrf=True
        )
        assert status == 200
        assert json.loads(other_versions_body)["versions"][0]["dims"] == "922×400×750"
        status, _headers, other_history_body = other_browser.request(
            "POST", "/api/chat-history", {"project_file": "product.json"}, csrf=True
        )
        assert status == 200
        assert json.loads(other_history_body)["operations"] == []

        audit = store.list_audit(platform["id"], constanta["organization"]["id"])
        ai_events = [item for item in audit if item["action"] == "studio.ai.completed"]
        assert len(ai_events) == 2
        assert ai_events[0]["actor_user_id"] == constanta["user"]["id"]
        assert ai_events[0]["metadata"]["provider"] == "test-model"
        assert "message" not in ai_events[0]["metadata"]
        catalog_actions = {item["action"] for item in audit}
        assert "studio.project.responsible_changed" in catalog_actions
        assert "studio.project.archived" in catalog_actions
        assert "studio.project.restored" in catalog_actions

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
        status, _headers, support_projects_body = platform_browser.request(
            "POST", "/api/projects", {}, csrf=True
        )
        assert status == 200, support_projects_body
        assert json.loads(support_projects_body)["members"][0]["display_name"] == (
            "Алексей Лазарев"
        )
        assert platform_browser.request(
            "POST", "/api/save", {"spec": _spec("Нельзя записать")}, csrf=True
        )[0] == 403
        assert platform_browser.request(
            "POST",
            "/api/catalog/archive",
            {"file": "product.json", "reason": "Нельзя из support-view"},
            csrf=True,
        )[0] == 403

        reviewer_browser = Browser(port)
        reviewer_browser.login(reviewer["login"], reviewer["starter_password"])
        assert reviewer_browser.request(
            "POST",
            "/api/catalog/archive",
            {"file": "product.json", "reason": "Наблюдатель только читает"},
            csrf=True,
        )[0] == 403

        assert browser.request("POST", "/api/auth/logout", {}, csrf=True)[0] == 200
        assert SESSION_COOKIE not in browser.cookies
        assert browser.request("GET", "/index.html")[0] == 303

        designer_browser = Browser(port)
        designer_browser.login(designer["login"], designer["starter_password"])
        assert designer_browser.request("GET", "/api/auth/me")[0] == 200
        assert designer_browser.request(
            "POST",
            "/api/rename",
            {"file": "product.json", "name": "Чужое изделие"},
            csrf=True,
        )[0] == 403
        status, _headers, new_body = designer_browser.request(
            "POST", "/api/new", {"name": "Изделие проектировщика"}, csrf=True
        )
        assert status == 200, new_body
        designer_file = json.loads(new_body)["file"]
        status, _headers, mine_body = designer_browser.request(
            "POST", "/api/projects", {"scope": "mine"}, csrf=True
        )
        assert status == 200
        mine = json.loads(mine_body)
        assert [item["file"] for item in mine["projects"]] == [designer_file]
        assert mine["projects"][0]["responsible_user_id"] == designer["user"]["id"]

        owner_filter_browser = Browser(port)
        owner_filter_browser.login(constanta["login"], constanta["starter_password"])
        status, _headers, assigned_body = owner_filter_browser.request(
            "POST",
            "/api/projects",
            {"responsible_user_id": designer["user"]["id"]},
            csrf=True,
        )
        assert status == 200
        assert [item["file"] for item in json.loads(assigned_body)["projects"]] == [
            designer_file
        ]
        store.update_membership(
            platform["id"],
            constanta["organization"]["id"],
            designer["membership"]["id"],
            status="disabled",
        )
        status, _headers, disabled_assignment_body = owner_filter_browser.request(
            "POST",
            "/api/catalog/assign",
            {"file": "product.json", "responsible_user_id": designer["user"]["id"]},
            csrf=True,
        )
        assert status == 409
        assert json.loads(disabled_assignment_body)["code"] == "member_unavailable"
        status, _headers, blocked_member_body = designer_browser.request(
            "GET", "/api/auth/me"
        )
        assert status == 401
        assert json.loads(blocked_member_body)["code"] == "unauthenticated"
        assert SESSION_COOKIE not in designer_browser.cookies

        store.update_membership(
            platform["id"],
            constanta["organization"]["id"],
            designer["membership"]["id"],
            status="active",
        )
        designer_browser.login(designer["login"], designer["starter_password"])
        store.update_organization_status(
            platform["id"], constanta["organization"]["id"], "disabled"
        )
        status, _headers, blocked_company_body = designer_browser.request(
            "GET", "/api/auth/me"
        )
        assert status == 401
        assert json.loads(blocked_company_body)["code"] == "unauthenticated"
        assert SESSION_COOKIE not in designer_browser.cookies
        assert designer_browser.request(
            "POST",
            "/api/auth/login",
            {"email": designer["login"], "password": designer["starter_password"]},
        )[0] == 401
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
