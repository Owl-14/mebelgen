from __future__ import annotations

import http.client
import json
import re
import threading
from dataclasses import dataclass
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any

import pytest
from argon2 import PasswordHasher, Type

from src.admin import (
    CSRF_COOKIE,
    MAX_JSON_BYTES,
    SESSION_COOKIE,
    SUPPORT_COOKIE,
    _LoginThrottle,
    _initialize_store,
    create_admin_server,
)
from src.identity import IdentityStore


ADMIN_EMAIL = "platform@example.test"
ADMIN_PASSWORD = "correct horse battery staple"
OWNER_PASSWORD = "owner password long enough"


@dataclass
class Response:
    status: int
    headers: list[tuple[str, str]]
    body: bytes

    def header(self, name: str) -> str | None:
        wanted = name.casefold()
        return next((value for key, value in self.headers if key.casefold() == wanted), None)

    def headers_all(self, name: str) -> list[str]:
        wanted = name.casefold()
        return [value for key, value in self.headers if key.casefold() == wanted]

    def json(self) -> dict[str, Any]:
        value = json.loads(self.body.decode("utf-8"))
        assert isinstance(value, dict)
        return value


class Browser:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.origin = f"http://{host}:{port}"
        self.cookies: dict[str, str] = {}

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        raw_body: bytes | None = None,
        content_type: str | None = "application/json",
        origin: str | None | object = ...,
        headers: dict[str, str] | None = None,
    ) -> Response:
        request_headers = dict(headers or {})
        body: bytes | None = None
        if raw_body is not None:
            body = raw_body
        elif payload is not None:
            body = json.dumps(payload).encode("utf-8")
        if body is not None and content_type is not None:
            request_headers["Content-Type"] = content_type
        if method.upper() not in {"GET", "HEAD"}:
            if origin is ...:
                request_headers["Origin"] = self.origin
            elif origin is not None:
                request_headers["Origin"] = str(origin)
        if self.cookies:
            request_headers["Cookie"] = "; ".join(
                f"{name}={value}" for name, value in self.cookies.items()
            )
        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
        connection.request(method, path, body=body, headers=request_headers)
        raw = connection.getresponse()
        result = Response(raw.status, raw.getheaders(), raw.read())
        connection.close()
        for header in result.headers_all("Set-Cookie"):
            parsed = SimpleCookie()
            parsed.load(header)
            for name, morsel in parsed.items():
                if morsel["max-age"] == "0" or not morsel.value:
                    self.cookies.pop(name, None)
                else:
                    self.cookies[name] = morsel.value
        return result

    def csrf_headers(self) -> dict[str, str]:
        return {"X-CSRF-Token": self.cookies[CSRF_COOKIE]}


@dataclass
class LiveAdmin:
    store: IdentityStore
    server: Any
    thread: threading.Thread
    browser: Browser


@pytest.fixture
def live_admin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> LiveAdmin:
    monkeypatch.delenv("AKEDA_COOKIE_SECURE", raising=False)
    monkeypatch.delenv("AKEDA_ADMIN_COOKIE_SECURE", raising=False)
    monkeypatch.delenv("AKEDA_ADMIN_ORIGIN", raising=False)
    hasher = PasswordHasher(
        time_cost=1,
        memory_cost=1024,
        parallelism=1,
        hash_len=16,
        salt_len=8,
        type=Type.ID,
    )
    store = IdentityStore(tmp_path / "identity.sqlite3", password_hasher=hasher)
    store.migrate()
    store.bootstrap_platform_admin(ADMIN_EMAIL, "Platform Owner", ADMIN_PASSWORD)
    server = create_admin_server(store, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    browser = Browser("127.0.0.1", int(server.server_address[1]))
    try:
        yield LiveAdmin(store, server, thread, browser)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def login(browser: Browser, email: str = ADMIN_EMAIL, password: str = ADMIN_PASSWORD) -> dict[str, Any]:
    response = browser.request(
        "POST", "/api/auth/login", {"email": email, "password": password}
    )
    assert response.status == 200, response.body
    return response.json()


def create_organization(browser: Browser, suffix: str = "A") -> dict[str, Any]:
    response = browser.request(
        "POST",
        "/api/admin/organizations",
        {
            "name": f"Company {suffix}",
            "owner_name": f"Owner {suffix}",
            "owner_email": f"owner-{suffix.casefold()}@example.test",
        },
        headers=browser.csrf_headers(),
    )
    assert response.status == 201, response.body
    return response.json()


def test_unauthenticated_redirects_and_pages_have_security_headers(live_admin: LiveAdmin) -> None:
    browser = live_admin.browser
    root = browser.request("GET", "/", content_type=None)
    assert root.status == 303
    assert root.header("Location") == "/login"

    protected = browser.request("GET", "/admin", content_type=None)
    assert protected.status == 303
    assert protected.header("Location") == "/login"

    page = browser.request("GET", "/login", content_type=None)
    assert page.status == 200
    assert "no-store" in (page.header("Cache-Control") or "")
    assert page.header("X-Frame-Options") == "DENY"
    assert page.header("X-Content-Type-Options") == "nosniff"
    csp = page.header("Content-Security-Policy") or ""
    assert "default-src 'none'" in csp
    assert "script-src 'nonce-" in csp
    html = page.body.decode("utf-8")
    assert '<script nonce="' in html
    assert '<style nonce="' in html
    assert "unsafe-inline" not in csp

    activation = browser.request("GET", "/activate?token=one-time", content_type=None)
    assert activation.status == 200
    assert "Активировать доступ" in activation.body.decode("utf-8")


def test_login_uses_generic_401_and_secure_session_contract(live_admin: LiveAdmin) -> None:
    browser = live_admin.browser
    missing_origin = browser.request(
        "POST",
        "/api/auth/login",
        {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        origin=None,
    )
    assert missing_origin.status == 403

    unknown = browser.request(
        "POST",
        "/api/auth/login",
        {"email": "unknown@example.test", "password": "wrong password value"},
    )
    wrong = browser.request(
        "POST",
        "/api/auth/login",
        {"email": ADMIN_EMAIL, "password": "wrong password value"},
    )
    assert unknown.status == wrong.status == 401
    assert unknown.json()["error"] == wrong.json()["error"] == "Неверная почта или пароль."

    success = browser.request(
        "POST",
        "/api/auth/login",
        {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
    )
    assert success.status == 200
    payload = success.json()
    assert "session_token" not in payload
    assert payload["csrf_token"] == browser.cookies[CSRF_COOKIE]
    assert SESSION_COOKIE in browser.cookies

    cookie_headers = success.headers_all("Set-Cookie")
    session_cookie = next(value for value in cookie_headers if value.startswith(SESSION_COOKIE + "="))
    csrf_cookie = next(value for value in cookie_headers if value.startswith(CSRF_COOKIE + "="))
    assert "HttpOnly" in session_cookie and "SameSite=Lax" in session_cookie
    assert "Max-Age=" in session_cookie
    assert "HttpOnly" not in csrf_cookie and "SameSite=Lax" in csrf_cookie

    me = browser.request("GET", "/api/auth/me", content_type=None)
    assert me.status == 200
    assert me.json()["authenticated"] is True
    assert me.json()["csrf_token"] == browser.cookies[CSRF_COOKIE]
    assert browser.request("GET", "/admin", content_type=None).status == 200


def test_json_body_cap_origin_and_csrf_are_enforced(live_admin: LiveAdmin) -> None:
    browser = live_admin.browser
    wrong_type = browser.request(
        "POST",
        "/api/auth/login",
        raw_body=b"{}",
        content_type="text/plain",
    )
    assert wrong_type.status == 415

    oversized = browser.request(
        "POST",
        "/api/auth/login",
        raw_body=b"x" * (MAX_JSON_BYTES + 1),
    )
    assert oversized.status == 413

    login(browser)
    no_csrf = browser.request("POST", "/api/auth/logout", {})
    assert no_csrf.status == 403
    wrong_origin = browser.request(
        "POST",
        "/api/auth/logout",
        {},
        headers=browser.csrf_headers(),
        origin="https://evil.example",
    )
    assert wrong_origin.status == 403
    wrong_csrf = browser.request(
        "POST",
        "/api/auth/logout",
        {},
        headers={"X-CSRF-Token": "wrong"},
    )
    assert wrong_csrf.status == 403

    logout = browser.request(
        "POST", "/api/auth/logout", {}, headers=browser.csrf_headers()
    )
    assert logout.status == 200
    assert SESSION_COOKIE not in browser.cookies
    assert CSRF_COOKIE not in browser.cookies
    assert browser.request("GET", "/api/auth/me", content_type=None).status == 401


def test_platform_creation_snapshot_activation_and_tenant_authorization(live_admin: LiveAdmin) -> None:
    platform = live_admin.browser
    login(platform)
    first = create_organization(platform, "A")
    second = create_organization(platform, "B")
    assert first["activation_url"].startswith(platform.origin + "/activate?token=")
    assert first["activation_token"]

    global_snapshot = platform.request("GET", "/api/admin/snapshot", content_type=None)
    assert global_snapshot.status == 200
    organizations = global_snapshot.json().get("organizations", [])
    first_org_id = first["organization"]["id"]
    second_org_id = second["organization"]["id"]
    assert {item["id"] for item in organizations} >= {first_org_id, second_org_id}

    owner = Browser(platform.host, platform.port)
    activated = owner.request(
        "POST",
        "/api/auth/activate",
        {
            "token": first["activation_token"],
            "display_name": "Owner A",
            "password": OWNER_PASSWORD,
        },
    )
    assert activated.status == 200, activated.body
    assert SESSION_COOKIE in owner.cookies and CSRF_COOKIE in owner.cookies
    me = owner.request("GET", "/api/auth/me", content_type=None).json()
    assert me["organization"]["id"] == first_org_id
    assert me["membership"]["role"] == "owner"

    replay = Browser(platform.host, platform.port).request(
        "POST",
        "/api/auth/activate",
        {
            "token": first["activation_token"],
            "display_name": "Owner A",
            "password": OWNER_PASSWORD,
        },
    )
    assert replay.status == 409

    own_snapshot = owner.request(
        "GET", f"/api/admin/snapshot?organization_id={first_org_id}", content_type=None
    )
    assert own_snapshot.status == 200
    foreign_snapshot = owner.request(
        "GET", f"/api/admin/snapshot?organization_id={second_org_id}", content_type=None
    )
    assert foreign_snapshot.status == 403

    forbidden_create = owner.request(
        "POST",
        "/api/admin/organizations",
        {
            "name": "Forbidden Company",
            "owner_name": "Other Owner",
            "owner_email": "other@example.test",
        },
        headers=owner.csrf_headers(),
    )
    assert forbidden_create.status == 403


def test_read_only_support_session_is_bound_to_primary_session_and_can_end(live_admin: LiveAdmin) -> None:
    platform = live_admin.browser
    login(platform)
    created = create_organization(platform, "Support")
    organization_id = created["organization"]["id"]

    started = platform.request(
        "POST",
        "/api/admin/support/start",
        {
            "organization_id": organization_id,
            "reason": "AKD-555 diagnostic review",
            "duration_minutes": 30,
            "scope": "read_only",
        },
        headers=platform.csrf_headers(),
    )
    assert started.status == 201, started.body
    support = started.json()["support_session"]
    assert support["scope"] == "read_only"
    assert SUPPORT_COOKIE in platform.cookies
    support_cookie = next(
        value
        for value in started.headers_all("Set-Cookie")
        if value.startswith(SUPPORT_COOKIE + "=")
    )
    assert "HttpOnly" in support_cookie and "SameSite=Lax" in support_cookie

    me = platform.request("GET", "/api/auth/me", content_type=None)
    assert me.status == 200
    assert me.json()["support_session"]["id"] == support["id"]

    other_login = Browser(platform.host, platform.port)
    login(other_login)
    other_login.cookies[SUPPORT_COOKIE] = support["id"]
    other_me = other_login.request("GET", "/api/auth/me", content_type=None)
    assert other_me.status == 200
    assert other_me.json()["support_session"] is None

    scoped = platform.request(
        "GET", f"/api/admin/snapshot?organization_id={organization_id}", content_type=None
    )
    assert scoped.status == 200

    ended = platform.request(
        "POST",
        "/api/admin/support/end",
        {"support_session_id": support["id"]},
        headers=platform.csrf_headers(),
    )
    assert ended.status == 200, ended.body
    assert SUPPORT_COOKIE not in platform.cookies
    assert platform.request("GET", "/api/auth/me", content_type=None).json()["support_session"] is None

    opened = platform.request(
        "POST",
        "/api/admin/company-view/start",
        {"organization_id": organization_id},
        headers=platform.csrf_headers(),
    )
    assert opened.status == 201, opened.body
    assert opened.json()["company_view"]["reason"] == "Просмотр компании из админ-панели Akeda"
    assert SUPPORT_COOKIE in platform.cookies
    closed = platform.request(
        "POST", "/api/admin/company-view/end", {}, headers=platform.csrf_headers()
    )
    assert closed.status == 200
    assert SUPPORT_COOKIE not in platform.cookies


def test_platform_manages_employee_credentials_without_email_delivery(
    live_admin: LiveAdmin,
) -> None:
    browser = live_admin.browser
    login(browser)
    created = browser.request(
        "POST",
        "/api/admin/organizations/provision",
        {
            "name": "Managed Furniture",
            "owner_name": "Managed Owner",
            "owner_email": "managed-owner@example.test",
        },
        headers=browser.csrf_headers(),
    )
    assert created.status == 201, created.body
    payload = created.json()
    organization_id = payload["organization"]["id"]
    assert payload["credentials"]["login"] == "managed-owner@example.test"
    assert re.fullmatch(r"[A-Za-z0-9]{6}", payload["credentials"]["password"])

    member = browser.request(
        "POST",
        "/api/admin/members/provision",
        {
            "organization_id": organization_id,
            "name": "Managed Designer",
            "email": "managed-designer@example.test",
            "role": "designer",
        },
        headers=browser.csrf_headers(),
    )
    assert member.status == 201, member.body
    member_payload = member.json()
    membership_id = member_payload["membership"]["id"]
    original_password = member_payload["credentials"]["password"]

    updated = browser.request(
        "POST",
        "/api/admin/members/update",
        {
            "organization_id": organization_id,
            "membership_id": membership_id,
            "name": "Updated Designer",
            "email": "updated-designer@example.test",
            "role": "reviewer",
            "status": "active",
        },
        headers=browser.csrf_headers(),
    )
    assert updated.status == 200, updated.body
    assert updated.json()["display_name"] == "Updated Designer"

    rotated = browser.request(
        "POST",
        "/api/admin/members/reset-password",
        {"organization_id": organization_id, "membership_id": membership_id},
        headers=browser.csrf_headers(),
    )
    assert rotated.status == 200, rotated.body
    rotated_credentials = rotated.json()["credentials"]
    assert rotated_credentials["login"] == "updated-designer@example.test"
    assert rotated_credentials["password"] != original_password

    employee = Browser("127.0.0.1", int(live_admin.server.server_address[1]))
    response = employee.request(
        "POST",
        "/api/auth/login",
        {"email": rotated_credentials["login"], "password": rotated_credentials["password"]},
    )
    assert response.status == 200, response.body


def test_login_throttle_tracks_account_independently_of_source_ip() -> None:
    throttle = _LoginThrottle(limit=2, window=60)
    throttle.failure("127.0.0.1", "person@example.test")
    throttle.failure("127.0.0.2", "PERSON@example.test")

    assert throttle.retry_after("127.0.0.3", "person@example.test") > 0
    assert throttle.retry_after("127.0.0.3", "someone-else@example.test") == 0


def test_secure_cookie_mode_is_controlled_by_environment(
    live_admin: LiveAdmin, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AKEDA_ADMIN_COOKIE_SECURE", "1")
    server = create_admin_server(live_admin.store, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    browser = Browser("127.0.0.1", int(server.server_address[1]))
    browser.origin = f"https://{browser.host}:{browser.port}"
    try:
        response = browser.request(
            "POST",
            "/api/auth/login",
            {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        )
        assert response.status == 200, response.body
        assert response.header("Strict-Transport-Security") == "max-age=31536000"
        session_cookie = next(
            value
            for value in response.headers_all("Set-Cookie")
            if value.startswith(SESSION_COOKIE + "=")
        )
        assert "Secure" in session_cookie
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_bootstrap_fails_closed_for_empty_or_public_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hasher = PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1, type=Type.ID)
    empty = IdentityStore(tmp_path / "empty.sqlite3", password_hasher=hasher)
    monkeypatch.delenv("AKEDA_BOOTSTRAP_PASSWORD", raising=False)
    monkeypatch.delenv("AKEDA_BOOTSTRAP_EMAIL", raising=False)
    with pytest.raises(RuntimeError, match="Identity DB is empty"):
        _initialize_store(empty, bootstrap_email=None, bootstrap_name="Admin")

    public = IdentityStore(tmp_path / "public.sqlite3", password_hasher=hasher)
    monkeypatch.setenv("AKEDA_BOOTSTRAP_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("STUDIO_PUBLIC", "1")
    with pytest.raises(RuntimeError, match="forbidden"):
        _initialize_store(public, bootstrap_email=ADMIN_EMAIL, bootstrap_name="Admin")
