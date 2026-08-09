"""Loopback-only identity and administration HTTP server.

This module is deliberately separate from :mod:`src.studio`.  The current
Studio still owns process-global/file-backed project state, so authenticating
the administration console must not imply that Studio is tenant-isolated.

The HTTP layer treats ``IdentityStore`` as the source of truth for identity,
permissions, session expiry and audit events.  Browser state is only an opaque
session cookie plus the raw CSRF secret whose hash is held by the store.
"""

from __future__ import annotations

import dataclasses
import hmac
import inspect
import json
import os
import secrets
import threading
import time
import webbrowser
from collections import defaultdict, deque
from collections.abc import Mapping
from datetime import date, datetime
from email.utils import formatdate
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

from .admin_page import activation_page, admin_page, login_page
from .identity import IdentityError, IdentityStore


SESSION_COOKIE = "akeda_session"
CSRF_COOKIE = "akeda_csrf"
SUPPORT_COOKIE = "akeda_support"
MAX_JSON_BYTES = 64 * 1024
LOGIN_WINDOW_SECONDS = 5 * 60
LOGIN_FAILURE_LIMIT = 8


class _HTTPProblem(Exception):
    def __init__(self, status: int, code: str, message: str, *, headers: list[tuple[str, str]] | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.headers = headers or []


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _jsonable(value: Any) -> Any:
    """Convert store DTOs to JSON without requiring a concrete DTO library."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if dataclasses.is_dataclass(value):
        return _jsonable(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "_asdict"):
        return _jsonable(value._asdict())
    if hasattr(value, "__dict__"):
        return {k: _jsonable(v) for k, v in vars(value).items() if not k.startswith("_")}
    return str(value)


def _as_mapping(value: Any) -> dict[str, Any]:
    converted = _jsonable(value)
    return dict(converted) if isinstance(converted, dict) else {"result": converted}


def _value(value: Any, *keys: str) -> Any:
    data = _as_mapping(value)
    for key in keys:
        if data.get(key) is not None:
            return data[key]
    return None


def _normalize_snapshot(value: Any) -> dict[str, Any]:
    """Adapt the store snapshot to the stable browser-facing shape.

    ``IdentityStore`` deliberately returns an organization together with the
    current user's membership.  The administration UI consumes a flat company
    row, so retain the membership as metadata while exposing the organization
    fields at the top level.  The member aliases similarly bridge the richer
    joined rows returned by SQLite with the public HTTP contract.
    """

    payload = _as_mapping(value)
    normalized_organizations: list[dict[str, Any]] = []
    organizations = payload.get("organizations")
    if isinstance(organizations, list):
        for item in organizations:
            item_data = _as_mapping(item)
            nested = item_data.get("organization")
            if nested is None:
                normalized_organizations.append(item_data)
                continue
            organization = _as_mapping(nested)
            membership = item_data.get("membership")
            if membership is not None:
                organization.setdefault("membership", _jsonable(membership))
            for key, nested_value in item_data.items():
                if key not in {"organization", "membership"}:
                    organization.setdefault(key, nested_value)
            normalized_organizations.append(organization)
        payload["organizations"] = normalized_organizations

    members = payload.get("memberships")
    if members is None:
        members = payload.get("members")
    if isinstance(members, list):
        normalized_members: list[dict[str, Any]] = []
        for member in members:
            item = _as_mapping(member)
            aliases = {
                "id": "membership_id",
                "status": "membership_status",
                "created_at": "membership_created_at",
                "updated_at": "membership_updated_at",
            }
            for public_name, store_name in aliases.items():
                if item.get(public_name) is None and item.get(store_name) is not None:
                    item[public_name] = item[store_name]
            normalized_members.append(item)
        payload["members"] = normalized_members
        payload["memberships"] = normalized_members
    return payload


def _invoke(store: Any, method_name: str, /, **kwargs: Any) -> Any:
    """Call a store method while tolerating optional parameters during rollout.

    Keyword filtering is based on the declared signature.  It never retries a
    mutation after ``TypeError`` because doing so could duplicate an invitation
    or organization when the exception came from inside the store.
    """

    method = getattr(store, method_name)
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return method(**kwargs)
    accepts_kwargs = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values())
    if accepts_kwargs:
        return method(**kwargs)
    accepted = {
        name: value
        for name, value in kwargs.items()
        if name in signature.parameters
        and signature.parameters[name].kind
        not in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.VAR_POSITIONAL)
    }
    return method(**accepted)


def _identity_status(code: str) -> int:
    normalized = str(code or "").lower()
    if normalized in {
        "unauthenticated", "invalid_session", "session_expired", "invalid_credentials",
        "authentication_failed", "disabled_user", "disabled_membership",
    }:
        return 401
    if normalized in {"forbidden", "permission_denied", "support_read_only"}:
        return 403
    if normalized in {"not_found", "organization_not_found", "membership_not_found"}:
        return 404
    if normalized in {
        "conflict", "already_exists", "email_exists", "invite_used", "invite_expired",
        "invalid_or_expired_invitation", "sole_owner",
    }:
        return 409
    if normalized in {"rate_limited", "too_many_requests"}:
        return 429
    return 400


class _LoginThrottle:
    """Small in-memory throttle for the loopback vertical slice."""

    def __init__(self, *, limit: int = LOGIN_FAILURE_LIMIT, window: int = LOGIN_WINDOW_SECONDS):
        self.limit = max(1, int(limit))
        self.window = max(1, int(window))
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _trim(self, queue: deque[float], now: float) -> None:
        while queue and now - queue[0] >= self.window:
            queue.popleft()

    def retry_after(self, ip: str, email: str) -> int:
        now = time.monotonic()
        keys = (f"ip:{ip}", f"account:{email.casefold()}")
        with self._lock:
            retry = 0
            for key in keys:
                queue = self._failures[key]
                self._trim(queue, now)
                if len(queue) >= self.limit:
                    retry = max(retry, int(self.window - (now - queue[0])) + 1)
            return retry

    def failure(self, ip: str, email: str) -> None:
        now = time.monotonic()
        with self._lock:
            for key in (f"ip:{ip}", f"account:{email.casefold()}"):
                queue = self._failures[key]
                self._trim(queue, now)
                queue.append(now)

    def success(self, ip: str, email: str) -> None:
        with self._lock:
            self._failures.pop(f"account:{email.casefold()}", None)


class _AdminServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], store: IdentityStore):
        self.identity_store = store
        self.cookie_secure = _truthy_env("AKEDA_COOKIE_SECURE") or _truthy_env(
            "AKEDA_ADMIN_COOKIE_SECURE"
        )
        self.configured_origin = os.environ.get("AKEDA_ADMIN_ORIGIN", "").rstrip("/")
        self.studio_url = os.environ.get("AKEDA_STUDIO_URL", "/index.html").strip() or "/index.html"
        self.login_throttle = _LoginThrottle(
            limit=int(os.environ.get("AKEDA_LOGIN_FAILURE_LIMIT", LOGIN_FAILURE_LIMIT)),
            window=int(os.environ.get("AKEDA_LOGIN_WINDOW_SECONDS", LOGIN_WINDOW_SECONDS)),
        )
        super().__init__(address, _AdminHandler)


class _AdminHandler(BaseHTTPRequestHandler):
    server: _AdminServer

    def log_message(self, *_args: Any) -> None:
        return

    # ------------------------------------------------------------ responses

    def _security_headers(self, *, nonce: str | None = None) -> list[tuple[str, str]]:
        if nonce:
            csp = (
                "default-src 'none'; "
                f"script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
                "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
                "base-uri 'none'; form-action 'self'; frame-ancestors 'none'; object-src 'none'"
            )
        else:
            csp = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
        headers = [
            ("Cache-Control", "no-store, max-age=0"),
            ("Pragma", "no-cache"),
            ("Content-Security-Policy", csp),
            ("X-Content-Type-Options", "nosniff"),
            ("X-Frame-Options", "DENY"),
            ("Referrer-Policy", "no-referrer"),
            ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"),
            ("Cross-Origin-Opener-Policy", "same-origin"),
            ("Cross-Origin-Resource-Policy", "same-origin"),
        ]
        if self.server.cookie_secure:
            headers.append(("Strict-Transport-Security", "max-age=31536000"))
        return headers

    def _send(
        self,
        status: int,
        body: bytes = b"",
        content_type: str = "application/json; charset=utf-8",
        *,
        headers: list[tuple[str, str]] | None = None,
        nonce: str | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in self._security_headers(nonce=nonce):
            self.send_header(name, value)
        for name, value in headers or []:
            self.send_header(name, value)
        self.end_headers()
        if body:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _json(
        self,
        status: int,
        payload: Any,
        *,
        headers: list[tuple[str, str]] | None = None,
    ) -> None:
        body = json.dumps(_jsonable(payload), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send(status, body, headers=headers)

    def _html(self, page: str) -> None:
        nonce = secrets.token_urlsafe(18)
        rendered = (
            page.replace(
                "__STUDIO_URL__",
                json.dumps(self.server.studio_url, ensure_ascii=False).replace("</", "<\\/"),
            )
            .replace("<style>", f'<style nonce="{nonce}">')
            .replace("<script>", f'<script nonce="{nonce}">')
        )
        self._send(200, rendered.encode("utf-8"), "text/html; charset=utf-8", nonce=nonce)

    def _redirect(self, location: str) -> None:
        self._send(303, headers=[("Location", location)])

    def _problem(self, problem: _HTTPProblem) -> None:
        self._json(
            problem.status,
            {"ok": False, "error": problem.message, "code": problem.code},
            headers=problem.headers,
        )

    # --------------------------------------------------------------- cookies

    def _cookies(self) -> dict[str, str]:
        raw = self.headers.get("Cookie", "")
        parsed = SimpleCookie()
        try:
            parsed.load(raw)
        except Exception:
            return {}
        return {name: morsel.value for name, morsel in parsed.items()}

    def _cookie(
        self,
        name: str,
        value: str,
        *,
        http_only: bool,
        clear: bool = False,
        max_age: int | None = None,
    ) -> str:
        cookie = SimpleCookie()
        cookie[name] = value
        morsel = cookie[name]
        morsel["path"] = "/"
        morsel["samesite"] = "Lax"
        if http_only:
            morsel["httponly"] = True
        if self.server.cookie_secure:
            morsel["secure"] = True
        if clear:
            morsel["max-age"] = 0
            morsel["expires"] = formatdate(0, usegmt=True)
        elif max_age is not None:
            morsel["max-age"] = max(1, int(max_age))
        return morsel.OutputString()

    def _clear_auth_cookies(self) -> list[tuple[str, str]]:
        return [
            ("Set-Cookie", self._cookie(SESSION_COOKIE, "", http_only=True, clear=True)),
            ("Set-Cookie", self._cookie(CSRF_COOKIE, "", http_only=False, clear=True)),
            ("Set-Cookie", self._cookie(SUPPORT_COOKIE, "", http_only=True, clear=True)),
        ]

    def _login_cookies(self, result: Any) -> tuple[list[tuple[str, str]], str, str]:
        session_token = str(_value(result, "session_token", "token") or "")
        csrf_token = str(_value(result, "csrf_token", "csrf") or "")
        if not session_token or not csrf_token:
            raise RuntimeError("IdentityStore did not return session and CSRF tokens")
        expires_at = _value(result, "expires_at")
        try:
            max_age = max(1, int(expires_at) - int(time.time())) if expires_at else None
        except (TypeError, ValueError):
            max_age = None
        headers = [
            ("Set-Cookie", self._cookie(SESSION_COOKIE, session_token, http_only=True, max_age=max_age)),
            ("Set-Cookie", self._cookie(CSRF_COOKIE, csrf_token, http_only=False, max_age=max_age)),
            ("Set-Cookie", self._cookie(SUPPORT_COOKIE, "", http_only=True, clear=True)),
        ]
        return headers, session_token, csrf_token

    # ------------------------------------------------------------- request IO

    def _request_origin(self) -> str:
        if self.server.configured_origin:
            return self.server.configured_origin
        host = self.headers.get("Host", "")
        port = int(self.server.server_address[1])
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if port == (443 if self.server.cookie_secure else 80):
            allowed.update({"127.0.0.1", "localhost"})
        if host not in allowed:
            raise _HTTPProblem(400, "invalid_host", "Недопустимый адрес запроса.")
        scheme = "https" if self.server.cookie_secure else "http"
        return f"{scheme}://{host}"

    def _require_same_origin(self) -> None:
        origin = self.headers.get("Origin", "")
        expected = self._request_origin()
        if not origin or origin == "null" or not hmac.compare_digest(origin, expected):
            raise _HTTPProblem(403, "origin_mismatch", "Запрос отклонён: неверный источник.")

    def _read_json(self) -> dict[str, Any]:
        content_type = self.headers.get("Content-Type", "")
        media_type = content_type.split(";", 1)[0].strip().lower()
        if media_type != "application/json":
            raise _HTTPProblem(415, "json_required", "Требуется Content-Type application/json.")
        if self.headers.get("Transfer-Encoding"):
            raise _HTTPProblem(400, "unsupported_transfer_encoding", "Потоковое тело запроса не поддерживается.")
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise _HTTPProblem(411, "length_required", "Не указан размер тела запроса.")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise _HTTPProblem(400, "invalid_length", "Некорректный размер тела запроса.") from exc
        if length < 0:
            raise _HTTPProblem(400, "invalid_length", "Некорректный размер тела запроса.")
        if length > MAX_JSON_BYTES:
            raise _HTTPProblem(413, "body_too_large", "Тело запроса слишком большое.")
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _HTTPProblem(400, "invalid_json", "Некорректный JSON.") from exc
        if not isinstance(value, dict):
            raise _HTTPProblem(400, "object_required", "JSON должен быть объектом.")
        return value

    # --------------------------------------------------------- authentication

    def _load_session(self) -> dict[str, Any] | None:
        cookies = self._cookies()
        token = cookies.get(SESSION_COOKIE, "")
        support_id = cookies.get(SUPPORT_COOKIE) or None
        if not token:
            return None
        stale_support = False
        try:
            result = _invoke(
                self.server.identity_store,
                "session",
                token=token,
                support_session_id=support_id,
            )
        except IdentityError:
            if not support_id:
                return None
            try:
                result = _invoke(
                    self.server.identity_store,
                    "session",
                    token=token,
                    support_session_id=None,
                )
                stale_support = True
            except IdentityError:
                return None
        if not result and support_id:
            try:
                result = _invoke(
                    self.server.identity_store,
                    "session",
                    token=token,
                    support_session_id=None,
                )
                stale_support = bool(result)
            except IdentityError:
                result = None
        if not result:
            return None
        return {
            "token": token,
            "support_session_id": support_id,
            "stale_support": stale_support,
            "context": result,
        }

    @staticmethod
    def _actor_user_id(auth: dict[str, Any]) -> str:
        context = _as_mapping(auth["context"])
        user = context.get("user")
        user_data = _as_mapping(user) if user is not None else {}
        actor = context.get("actor_user_id") or context.get("user_id") or user_data.get("id") or user_data.get("user_id")
        if not actor:
            raise _HTTPProblem(401, "unauthenticated", "Сессия недействительна.")
        return str(actor)

    @staticmethod
    def _primary_session_id(auth: dict[str, Any]) -> str | None:
        session = _value(auth["context"], "session")
        session_id = _value(session, "id", "session_id") if session is not None else None
        return str(session_id) if session_id else None

    def _require_auth(self, *, csrf: bool = False) -> dict[str, Any]:
        auth = self._load_session()
        if not auth:
            raise _HTTPProblem(
                401,
                "unauthenticated",
                "Требуется вход.",
                headers=self._clear_auth_cookies(),
            )
        if csrf:
            header_token = self.headers.get("X-CSRF-Token", "")
            cookie_token = self._cookies().get(CSRF_COOKIE, "")
            valid = bool(
                header_token
                and cookie_token
                and hmac.compare_digest(header_token, cookie_token)
            )
            if valid:
                try:
                    valid = bool(
                        _invoke(
                            self.server.identity_store,
                            "verify_csrf",
                            token=auth["token"],
                            raw_csrf=header_token,
                        )
                    )
                except IdentityError:
                    valid = False
            if not valid:
                raise _HTTPProblem(403, "csrf_failed", "Проверка CSRF не пройдена.")
        return auth

    # --------------------------------------------------------------- routing

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        try:
            parsed = urlsplit(self.path)
            path = parsed.path
            if path == "/api/auth/me":
                self._get_me()
                return
            if path == "/api/admin/snapshot":
                self._get_snapshot(parse_qs(parsed.query))
                return
            if path in {"/", "/login", "/admin"}:
                auth = self._load_session()
                if path == "/":
                    self._redirect("/admin" if auth else "/login")
                elif path == "/login":
                    if auth:
                        self._redirect("/admin")
                    else:
                        self._html(login_page())
                elif not auth:
                    self._redirect("/login")
                else:
                    self._html(admin_page())
                return
            if path == "/activate":
                self._html(activation_page())
                return
            if path == "/healthz":
                self._json(200, {"ok": True})
                return
            if path in {
                "/assets/studio/akeda-studio-wordmark.png",
                "/assets/studio/akeda-studio-mark.png",
            }:
                asset = Path(__file__).resolve().parent.parent / "assets" / "studio" / path.rsplit("/", 1)[-1]
                if asset.is_file():
                    self._send(200, asset.read_bytes(), "image/png")
                else:
                    self._send(404, b"")
                return
            self._json(404, {"ok": False, "error": "Не найдено.", "code": "not_found"})
        except _HTTPProblem as problem:
            self._problem(problem)
        except IdentityError as error:
            self._identity_problem(error)
        except Exception:
            self._json(500, {"ok": False, "error": "Внутренняя ошибка сервера.", "code": "internal_error"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        try:
            self._require_same_origin()
            body = self._read_json()
            path = urlsplit(self.path).path
            if path == "/api/auth/login":
                self._post_login(body)
            elif path == "/api/auth/activate":
                self._post_activate(body)
            elif path == "/api/auth/logout":
                self._post_logout(body)
            elif path == "/api/admin/organizations":
                self._post_organization(body)
            elif path == "/api/admin/organizations/provision":
                self._post_organization_provision(body)
            elif path == "/api/admin/organizations/status":
                self._post_organization_status(body)
            elif path == "/api/admin/invitations":
                self._post_invitation(body)
            elif path == "/api/admin/members/provision":
                self._post_member_provision(body)
            elif path == "/api/admin/members/update":
                self._post_managed_member(body)
            elif path == "/api/admin/members/reset-password":
                self._post_managed_member_password(body)
            elif path == "/api/admin/memberships/update":
                self._post_membership(body)
            elif path == "/api/admin/support/start":
                self._post_support_start(body)
            elif path == "/api/admin/support/end":
                self._post_support_end(body)
            elif path == "/api/admin/company-view/start":
                self._post_company_view_start(body)
            elif path == "/api/admin/company-view/end":
                self._post_support_end(body)
            else:
                self._json(404, {"ok": False, "error": "Не найдено.", "code": "not_found"})
        except _HTTPProblem as problem:
            self._problem(problem)
        except IdentityError as error:
            self._identity_problem(error)
        except Exception:
            self._json(500, {"ok": False, "error": "Внутренняя ошибка сервера.", "code": "internal_error"})

    def _identity_problem(self, error: IdentityError) -> None:
        code = str(getattr(error, "code", "identity_error"))
        message = str(error) or "Операция отклонена."
        self._json(_identity_status(code), {"ok": False, "error": message, "code": code})

    def _get_me(self) -> None:
        auth = self._require_auth()
        payload = _as_mapping(auth["context"])
        payload["authenticated"] = True
        raw_csrf = self._cookies().get(CSRF_COOKIE, "")
        if raw_csrf:
            try:
                if _invoke(
                    self.server.identity_store,
                    "verify_csrf",
                    token=auth["token"],
                    raw_csrf=raw_csrf,
                ):
                    payload["csrf_token"] = raw_csrf
            except IdentityError:
                pass
        headers: list[tuple[str, str]] = []
        if auth["stale_support"]:
            headers.append(("Set-Cookie", self._cookie(SUPPORT_COOKIE, "", http_only=True, clear=True)))
        self._json(200, payload, headers=headers)

    def _get_snapshot(self, query: dict[str, list[str]]) -> None:
        auth = self._require_auth()
        organization_id = (query.get("organization_id") or [None])[0]
        result = _invoke(
            self.server.identity_store,
            "snapshot",
            actor_user_id=self._actor_user_id(auth),
            organization_id=organization_id,
            support_session_id=auth["support_session_id"],
            primary_session_id=self._primary_session_id(auth),
        )
        self._json(200, _normalize_snapshot(result))

    def _post_login(self, body: dict[str, Any]) -> None:
        email = str(body.get("email") or "").strip().casefold()[:320]
        password = str(body.get("password") or "")[:4096]
        ip = self.client_address[0]
        retry = self.server.login_throttle.retry_after(ip, email)
        if retry:
            raise _HTTPProblem(
                429,
                "rate_limited",
                "Вход временно недоступен. Повторите позже.",
                headers=[("Retry-After", str(retry))],
            )
        try:
            result = _invoke(
                self.server.identity_store,
                "login",
                email=email,
                password=password,
                organization_id=body.get("organization_id"),
                ip=ip,
                user_agent=self.headers.get("User-Agent"),
            )
        except IdentityError as error:
            if str(getattr(error, "code", "")) == "organization_selection_required":
                details = _jsonable(getattr(error, "details", {}))
                self._json(
                    409,
                    {
                        "ok": False,
                        "error": "Выберите компанию для входа.",
                        "code": "organization_selection_required",
                        "details": details,
                    },
                )
                return
            self.server.login_throttle.failure(ip, email)
            raise _HTTPProblem(401, "invalid_credentials", "Неверная почта или пароль.")
        self.server.login_throttle.success(ip, email)
        headers, _session_token, csrf_token = self._login_cookies(result)
        payload = _as_mapping(result)
        payload.pop("session_token", None)
        payload["csrf_token"] = csrf_token
        payload.update(
            {
                "ok": True,
                "redirect": (
                    "/index.html"
                    if _as_mapping(result.get("organization")).get("id")
                    else "/admin"
                ),
            }
        )
        self._json(200, payload, headers=headers)

    def _post_activate(self, body: dict[str, Any]) -> None:
        token = str(body.get("token") or body.get("activation_token") or "")
        password = str(body.get("password") or "")[:4096]
        result = _invoke(
            self.server.identity_store,
            "activate_invitation",
            token=token,
            password=password,
            display_name=body.get("name") or body.get("display_name"),
            ip=self.client_address[0],
            user_agent=self.headers.get("User-Agent"),
        )
        headers, _session_token, csrf_token = self._login_cookies(result)
        payload = _as_mapping(result)
        payload.pop("session_token", None)
        payload["csrf_token"] = csrf_token
        payload.update({"ok": True, "redirect": "/admin"})
        self._json(200, payload, headers=headers)

    def _post_logout(self, _body: dict[str, Any]) -> None:
        auth = self._require_auth(csrf=True)
        _invoke(self.server.identity_store, "logout", token=auth["token"])
        self._json(200, {"ok": True}, headers=self._clear_auth_cookies())

    def _activation_result(self, result: Any) -> dict[str, Any]:
        payload = _as_mapping(result)
        invitation = payload.get("invitation")
        token = payload.get("activation_token")
        if not token and invitation is not None:
            token = _as_mapping(invitation).get("activation_token")
        if token:
            payload["activation_token"] = token
            payload["activation_url"] = (
                f"{self._request_origin()}/activate?token={quote(str(token), safe='')}"
            )
        return payload

    def _post_organization(self, body: dict[str, Any]) -> None:
        auth = self._require_auth(csrf=True)
        result = _invoke(
            self.server.identity_store,
            "create_organization",
            actor_user_id=self._actor_user_id(auth),
            name=body.get("name"),
            owner_name=body.get("owner_name"),
            owner_email=body.get("owner_email"),
        )
        self._json(201, {"ok": True, **self._activation_result(result)})

    @staticmethod
    def _credential_result(result: Any) -> dict[str, Any]:
        payload = _as_mapping(result)
        login = payload.pop("login", None)
        starter_password = payload.pop("starter_password", None)
        if login and starter_password:
            payload["credentials"] = {
                "login": str(login),
                "password": str(starter_password),
            }
        return payload

    def _post_organization_provision(self, body: dict[str, Any]) -> None:
        auth = self._require_auth(csrf=True)
        result = _invoke(
            self.server.identity_store,
            "provision_organization",
            actor_user_id=self._actor_user_id(auth),
            name=body.get("name"),
            owner_name=body.get("owner_name"),
            owner_email=body.get("owner_email"),
        )
        self._json(201, {"ok": True, **self._credential_result(result)})

    def _post_organization_status(self, body: dict[str, Any]) -> None:
        auth = self._require_auth(csrf=True)
        result = _invoke(
            self.server.identity_store,
            "update_organization_status",
            actor_user_id=self._actor_user_id(auth),
            organization_id=body.get("organization_id"),
            status=body.get("status"),
        )
        self._json(200, {"ok": True, **_as_mapping(result)})

    def _post_invitation(self, body: dict[str, Any]) -> None:
        auth = self._require_auth(csrf=True)
        result = _invoke(
            self.server.identity_store,
            "invite_member",
            actor_user_id=self._actor_user_id(auth),
            organization_id=body.get("organization_id"),
            name=body.get("name"),
            email=body.get("email"),
            role=body.get("role"),
        )
        self._json(201, {"ok": True, **self._activation_result(result)})

    def _post_member_provision(self, body: dict[str, Any]) -> None:
        auth = self._require_auth(csrf=True)
        result = _invoke(
            self.server.identity_store,
            "provision_member",
            actor_user_id=self._actor_user_id(auth),
            organization_id=body.get("organization_id"),
            name=body.get("name"),
            email=body.get("email"),
            role=body.get("role"),
        )
        self._json(201, {"ok": True, **self._credential_result(result)})

    def _post_managed_member(self, body: dict[str, Any]) -> None:
        auth = self._require_auth(csrf=True)
        result = _invoke(
            self.server.identity_store,
            "update_managed_member",
            actor_user_id=self._actor_user_id(auth),
            organization_id=body.get("organization_id"),
            membership_id=body.get("membership_id"),
            name=body.get("name"),
            email=body.get("email"),
            role=body.get("role"),
            status=body.get("status"),
        )
        self._json(200, {"ok": True, **_as_mapping(result)})

    def _post_managed_member_password(self, body: dict[str, Any]) -> None:
        auth = self._require_auth(csrf=True)
        result = _invoke(
            self.server.identity_store,
            "reset_managed_member_password",
            actor_user_id=self._actor_user_id(auth),
            organization_id=body.get("organization_id"),
            membership_id=body.get("membership_id"),
        )
        self._json(200, {"ok": True, **self._credential_result(result)})

    def _post_membership(self, body: dict[str, Any]) -> None:
        auth = self._require_auth(csrf=True)
        result = _invoke(
            self.server.identity_store,
            "update_membership",
            actor_user_id=self._actor_user_id(auth),
            organization_id=body.get("organization_id"),
            membership_id=body.get("membership_id"),
            role=body.get("role"),
            status=body.get("status"),
        )
        self._json(200, {"ok": True, **_as_mapping(result)})

    def _post_support_start(self, body: dict[str, Any]) -> None:
        auth = self._require_auth(csrf=True)
        result = _invoke(
            self.server.identity_store,
            "start_support",
            actor_user_id=self._actor_user_id(auth),
            organization_id=body.get("organization_id"),
            reason=body.get("reason"),
            duration_minutes=body.get("duration_minutes", 30),
            primary_session_id=self._primary_session_id(auth),
        )
        self._send_company_context_started(result, response_key="support_session")

    def _post_company_view_start(self, body: dict[str, Any]) -> None:
        auth = self._require_auth(csrf=True)
        result = _invoke(
            self.server.identity_store,
            "enter_company_view",
            actor_user_id=self._actor_user_id(auth),
            organization_id=body.get("organization_id"),
            primary_session_id=self._primary_session_id(auth),
        )
        self._send_company_context_started(result, response_key="company_view")

    def _send_company_context_started(self, result: Any, *, response_key: str) -> None:
        payload = _as_mapping(result)
        support = payload.get("support_session", result)
        support_id = _value(support, "id", "support_session_id")
        headers: list[tuple[str, str]] = []
        if support_id:
            expires_at = _value(support, "expires_at")
            try:
                max_age = max(1, int(expires_at) - int(time.time())) if expires_at else None
            except (TypeError, ValueError):
                max_age = None
            headers.append(
                (
                    "Set-Cookie",
                    self._cookie(
                        SUPPORT_COOKIE,
                        str(support_id),
                        http_only=True,
                        max_age=max_age,
                    ),
                )
            )
        response = {"ok": True, **payload}
        response["support_session"] = _jsonable(support)
        response[response_key] = _jsonable(support)
        self._json(201, response, headers=headers)

    def _post_support_end(self, body: dict[str, Any]) -> None:
        auth = self._require_auth(csrf=True)
        support_id = body.get("support_session_id") or auth["support_session_id"]
        result = _invoke(
            self.server.identity_store,
            "end_support",
            actor_user_id=self._actor_user_id(auth),
            support_session_id=support_id,
        )
        headers = [("Set-Cookie", self._cookie(SUPPORT_COOKIE, "", http_only=True, clear=True))]
        self._json(200, {"ok": True, **_as_mapping(result)}, headers=headers)


def create_admin_server(store: IdentityStore, *, port: int = 0) -> ThreadingHTTPServer:
    """Create an unstarted loopback server, primarily for tests and embedding."""

    return _AdminServer(("127.0.0.1", int(port)), store)


def _initialize_store(
    store: IdentityStore,
    *,
    bootstrap_email: str | None,
    bootstrap_name: str,
) -> None:
    _invoke(store, "migrate")
    count = int(_invoke(store, "count_users"))
    email = (bootstrap_email or os.environ.get("AKEDA_BOOTSTRAP_EMAIL") or "").strip()
    password = os.environ.get("AKEDA_BOOTSTRAP_PASSWORD", "")
    requested = bool(email or password)
    if count != 0:
        return
    if not requested:
        raise RuntimeError(
            "Identity DB is empty: provide --bootstrap-email and AKEDA_BOOTSTRAP_PASSWORD"
        )
    if os.environ.get("STUDIO_PUBLIC") == "1":
        raise RuntimeError("Bootstrap platform admin is forbidden when STUDIO_PUBLIC=1")
    if not email or not password:
        raise RuntimeError(
            "Empty identity DB bootstrap requires email and AKEDA_BOOTSTRAP_PASSWORD"
        )
    _invoke(
        store,
        "bootstrap_platform_admin",
        email=email,
        display_name=bootstrap_name,
        password=password,
    )


def run_admin(
    db_path: str | Path,
    *,
    port: int = 8766,
    open_browser: bool = True,
    bootstrap_email: str | None = None,
    bootstrap_name: str = "Администратор Akeda",
) -> None:
    """Run the local administration console on ``127.0.0.1`` only."""

    store = IdentityStore(db_path)
    _initialize_store(
        store,
        bootstrap_email=bootstrap_email,
        bootstrap_name=bootstrap_name,
    )
    server = create_admin_server(store, port=port)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Akeda Identity Admin: {url}  (DB: {Path(db_path)})")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        close = getattr(store, "close", None)
        if callable(close):
            close()


__all__ = [
    "CSRF_COOKIE",
    "MAX_JSON_BYTES",
    "SESSION_COOKIE",
    "SUPPORT_COOKIE",
    "create_admin_server",
    "run_admin",
]
