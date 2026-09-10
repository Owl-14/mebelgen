"""Identity, organization membership and support-access foundation for Studio.

The module deliberately owns no HTTP concerns: cookies, Origin validation and
response rendering belong to the server layer.  It does own the security
boundary behind those concerns: password/session/invitation secrets, current
account state, permissions, ownership invariants and append-only audit events.

SQLite is the Phase 1 persistence layer.  Every operation uses its own
connection so the store is safe to call from ``ThreadingHTTPServer`` handlers.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
import unicodedata
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

try:  # argon2 is only needed when Studio runs with accounts (--require-auth)
    from argon2 import PasswordHasher, Type
    from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
except ImportError:  # pragma: no cover - exercised on machines without the extra
    PasswordHasher = None  # type: ignore[assignment,misc]
    Type = None  # type: ignore[assignment,misc]

    class _ArgonUnavailable(Exception):
        """Placeholder so ``except`` clauses stay valid without argon2."""

    InvalidHashError = VerificationError = VerifyMismatchError = _ArgonUnavailable  # type: ignore[misc]

ARGON2_INSTALL_HINT = (
    "Для аккаунтов Studio нужен пакет argon2-cffi: "
    "pip install -r requirements-server.txt"
)


SCHEMA_VERSION = 2
DEFAULT_SESSION_TTL_SECONDS = 12 * 60 * 60
DEFAULT_INVITATION_TTL_SECONDS = 7 * 24 * 60 * 60
MAX_SUPPORT_DURATION_MINUTES = 4 * 60

PLATFORM_ROLES = frozenset({"platform_owner", "platform_support"})
ORGANIZATION_ROLES = frozenset({"owner", "admin", "designer", "technologist", "reviewer"})
USER_STATUSES = frozenset({"active", "disabled"})
ORGANIZATION_STATUSES = frozenset({"active", "disabled"})
ORGANIZATION_MODES = frozenset({"standard", "demo"})
MEMBERSHIP_STATUSES = frozenset({"active", "disabled"})

PLATFORM_ROLE_PERMISSIONS: Mapping[str, frozenset[str]] = {
    "platform_owner": frozenset(
        {
            "platform.organization.create",
            "platform.organization.manage",
            "platform.user.manage",
            "platform.support.start",
            "platform.audit.read",
        }
    ),
    "platform_support": frozenset({"platform.support.start"}),
}

_ALL_ORGANIZATION_PERMISSIONS = frozenset(
    {
        "organization.read",
        "organization.manage",
        "member.read",
        "member.invite",
        "member.manage",
        "project.read",
        "project.create",
        "project.write",
        "ai.run",
        "audit.read",
        "production.export",
        "production.build",
        "support.grant",
    }
)

ORGANIZATION_ROLE_PERMISSIONS: Mapping[str, frozenset[str]] = {
    "owner": _ALL_ORGANIZATION_PERMISSIONS,
    "admin": frozenset(
        {
            "organization.read",
            "member.read",
            "project.read",
            "project.create",
            "project.write",
            "ai.run",
            "audit.read",
            "production.export",
            "production.build",
            "support.grant",
        }
    ),
    "designer": frozenset(
        {
            "organization.read",
            "project.read",
            "project.create",
            "project.write",
            "ai.run",
            "production.export",
        }
    ),
    "technologist": frozenset(
        {
            "organization.read",
            "project.read",
            "project.create",
            "project.write",
            "production.export",
            "production.build",
        }
    ),
    "reviewer": frozenset({"organization.read", "project.read"}),
}

SUPPORT_READ_ONLY_PERMISSIONS = frozenset(
    {"organization.read", "member.read", "project.read", "audit.read"}
)
DEMO_SESSION_PERMISSIONS = frozenset(
    {"organization.read", "project.read", "project.create", "project.write", "ai.run"}
)

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_SAFE_AUDIT_KEY_PARTS = (
    "password",
    "passwd",
    "token",
    "secret",
    "api_key",
    "apikey",
    "image",
    "paramspec",
)


class IdentityError(Exception):
    """Stable domain error which an HTTP layer can map without parsing text."""

    code = "identity_error"

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.details = dict(details or {})


class ValidationError(IdentityError):
    code = "validation_error"


class AuthenticationFailed(IdentityError):
    code = "authentication_failed"


class PermissionDenied(IdentityError):
    code = "permission_denied"


class NotFound(IdentityError):
    code = "not_found"


class Conflict(IdentityError):
    code = "conflict"


class InvalidInvitation(IdentityError):
    code = "invalid_or_expired_invitation"


class SoleOwner(Conflict):
    code = "sole_owner"


class OrganizationSelectionRequired(IdentityError):
    code = "organization_selection_required"


def _uuid() -> str:
    return str(uuid.uuid4())


def _now(value: int | float | None = None) -> int:
    return int(time.time() if value is None else value)


def _hash_secret(raw: str) -> bytes:
    if not isinstance(raw, str) or not raw:
        return b""
    return hashlib.sha256(raw.encode("utf-8")).digest()


def _issue_secret() -> tuple[str, bytes]:
    raw = secrets.token_urlsafe(32)
    return raw, _hash_secret(raw)


def _normalize_email(email: str) -> tuple[str, str]:
    if not isinstance(email, str):
        raise ValidationError("Email должен быть строкой")
    display = unicodedata.normalize("NFKC", email).strip()
    normalized = display.casefold()
    if len(display) > 254 or not _EMAIL_RE.fullmatch(display):
        raise ValidationError("Некорректный email")
    return display, normalized


def _display_name(value: str | None, *, fallback: str = "") -> str:
    name = unicodedata.normalize("NFKC", value or fallback).strip()
    if not name or len(name) > 120:
        raise ValidationError("Имя должно содержать от 1 до 120 символов")
    return name


def _organization_name(value: str) -> str:
    name = unicodedata.normalize("NFKC", value or "").strip()
    if len(name) < 2 or len(name) > 160:
        raise ValidationError("Название компании должно содержать от 2 до 160 символов")
    return name


def _organization_mode(value: str) -> str:
    if value not in ORGANIZATION_MODES:
        raise ValidationError("Неизвестный режим компании")
    return value


def _password(value: str) -> str:
    if not isinstance(value, str) or len(value) < 15:
        raise ValidationError("Пароль должен содержать не менее 15 символов")
    if len(value.encode("utf-8")) > 1024:
        raise ValidationError("Пароль слишком длинный")
    return value


def _generated_access_password() -> str:
    """Return a compact operator-issued password shown only once.

    Six characters is an explicit first-version product constraint.  Avoid
    ambiguous glyphs and guarantee all three requested character classes.
    The raw value never enters SQLite or audit metadata.
    """

    lower = "abcdefghjkmnpqrstuvwxyz"
    upper = "ABCDEFGHJKMNPQRSTUVWXYZ"
    digits = "23456789"
    alphabet = lower + upper + digits
    chars = [secrets.choice(lower), secrets.choice(upper), secrets.choice(digits)]
    chars.extend(secrets.choice(alphabet) for _ in range(3))
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def _bounded_text(value: str | None, field: str, *, maximum: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{field} должен быть строкой")
    result = unicodedata.normalize("NFKC", value).strip()
    if len(result) > maximum:
        raise ValidationError(f"{field} слишком длинный")
    return result or None


def _role(value: str) -> str:
    if value not in ORGANIZATION_ROLES:
        raise ValidationError("Неизвестная роль компании")
    return value


def _status(value: str, allowed: Iterable[str], label: str) -> str:
    if value not in allowed:
        raise ValidationError(f"Неизвестный статус {label}")
    return value


def _ttl(value: int, *, maximum: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > maximum:
        raise ValidationError(f"Некорректный срок действия {label}")
    return value


def _row_dict(row: sqlite3.Row | None, *, omit: Iterable[str] = ()) -> dict[str, Any] | None:
    if row is None:
        return None
    hidden = set(omit)
    return {key: row[key] for key in row.keys() if key not in hidden}


def permissions_for_platform_role(role: str | None) -> frozenset[str]:
    return PLATFORM_ROLE_PERMISSIONS.get(role or "", frozenset())


def permissions_for_organization_role(role: str | None) -> frozenset[str]:
    return ORGANIZATION_ROLE_PERMISSIONS.get(role or "", frozenset())


def _split_sql_script(script: str) -> Iterator[str]:
    """Yield complete SQLite statements, preserving trigger bodies."""

    pending = ""
    for line in script.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            statement = pending.strip()
            if statement:
                yield statement
            pending = ""
    if pending.strip():
        raise RuntimeError("Incomplete identity migration SQL")


_MIGRATION_1 = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at INTEGER NOT NULL
);

CREATE TABLE users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    email_normalized TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    platform_role TEXT CHECK (platform_role IS NULL OR platform_role IN ('platform_owner','platform_support')),
    status TEXT NOT NULL CHECK (status IN ('active','disabled')),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE organizations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active','disabled')),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE memberships (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    role TEXT NOT NULL CHECK (role IN ('owner','admin','designer','technologist','reviewer')),
    status TEXT NOT NULL CHECK (status IN ('active','disabled')),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE (organization_id, user_id)
);
CREATE INDEX memberships_user_idx ON memberships(user_id, status);

CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    organization_id TEXT REFERENCES organizations(id) ON DELETE RESTRICT,
    token_hash BLOB NOT NULL UNIQUE,
    csrf_hash BLOB NOT NULL,
    ip TEXT,
    user_agent TEXT,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL,
    revoked_at INTEGER
);
CREATE INDEX sessions_user_idx ON sessions(user_id, revoked_at, expires_at);
CREATE INDEX sessions_org_idx ON sessions(organization_id, revoked_at, expires_at);

CREATE TABLE invitations (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    email TEXT NOT NULL,
    email_normalized TEXT NOT NULL,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('owner','admin','designer','technologist','reviewer')),
    token_hash BLOB NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('pending','accepted','revoked')),
    invited_by_user_id TEXT REFERENCES users(id) ON DELETE RESTRICT,
    accepted_user_id TEXT REFERENCES users(id) ON DELETE RESTRICT,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    accepted_at INTEGER,
    revoked_at INTEGER
);
CREATE UNIQUE INDEX invitations_one_pending_idx
    ON invitations(organization_id, email_normalized) WHERE status = 'pending';
CREATE INDEX invitations_token_idx ON invitations(token_hash);

CREATE TABLE support_sessions (
    id TEXT PRIMARY KEY,
    platform_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    primary_session_id TEXT REFERENCES sessions(id) ON DELETE RESTRICT,
    reason TEXT NOT NULL,
    scope TEXT NOT NULL CHECK (scope = 'read_only'),
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    ended_at INTEGER,
    ended_by_user_id TEXT REFERENCES users(id) ON DELETE RESTRICT
);
CREATE INDEX support_active_idx
    ON support_sessions(platform_user_id, organization_id, ended_at, expires_at);

CREATE TABLE audit_events (
    id TEXT PRIMARY KEY,
    actor_user_id TEXT REFERENCES users(id) ON DELETE RESTRICT,
    organization_id TEXT REFERENCES organizations(id) ON DELETE RESTRICT,
    support_session_id TEXT REFERENCES support_sessions(id) ON DELETE RESTRICT,
    action TEXT NOT NULL,
    target_type TEXT,
    target_id TEXT,
    metadata_json TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX audit_org_idx ON audit_events(organization_id, created_at, id);
CREATE INDEX audit_actor_idx ON audit_events(actor_user_id, created_at, id);

CREATE TRIGGER audit_events_no_update
BEFORE UPDATE ON audit_events BEGIN
    SELECT RAISE(ABORT, 'audit_events are append-only');
END;

CREATE TRIGGER audit_events_no_delete
BEFORE DELETE ON audit_events BEGIN
    SELECT RAISE(ABORT, 'audit_events are append-only');
END;
"""

_MIGRATION_2 = """
ALTER TABLE organizations ADD COLUMN mode TEXT NOT NULL DEFAULT 'standard'
    CHECK (mode IN ('standard','demo'));
"""


class IdentityStore:
    """Thread-safe-by-connection identity repository and policy service."""

    def __init__(
        self,
        path: str | Path,
        *,
        busy_timeout_ms: int = 5_000,
        password_hasher: PasswordHasher | None = None,
    ) -> None:
        if str(path) == ":memory:":
            raise ValidationError("IdentityStore требует файловую SQLite-базу")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.busy_timeout_ms = max(1, int(busy_timeout_ms))
        if password_hasher is None and PasswordHasher is None:
            raise RuntimeError(ARGON2_INSTALL_HINT)
        self._hasher = password_hasher or PasswordHasher(
            time_cost=3,
            memory_cost=65_536,
            parallelism=4,
            hash_len=32,
            salt_len=16,
            type=Type.ID,
        )
        # A missing account still performs Argon2 work, reducing email oracle timing.
        self._dummy_password_hash = self._hasher.hash(secrets.token_urlsafe(24))

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self.path),
            timeout=self.busy_timeout_ms / 1_000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def migrate(self) -> int:
        """Apply idempotent schema migrations and return the current version."""

        with self._transaction() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL)"
            )
            applied = {
                int(row["version"])
                for row in connection.execute("SELECT version FROM schema_migrations")
            }
            if 1 not in applied:
                # executescript would implicitly commit; execute complete statements here.
                for statement in _split_sql_script(_MIGRATION_1):
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (1, _now()),
                )
            if 2 not in applied:
                for statement in _split_sql_script(_MIGRATION_2):
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (2, _now()),
                )
        return SCHEMA_VERSION

    def count_users(self) -> int:
        with self._read() as connection:
            row = connection.execute("SELECT COUNT(*) AS n FROM users").fetchone()
            return int(row["n"])

    # ------------------------------------------------------------------
    # Internal policy and serialization helpers

    def _verify_password(self, encoded: str, password: str) -> bool:
        try:
            return bool(self._hasher.verify(encoded, password))
        except (VerifyMismatchError, VerificationError, InvalidHashError, TypeError):
            return False

    @staticmethod
    def _user(row: sqlite3.Row) -> dict[str, Any]:
        return _row_dict(row, omit={"password_hash", "email_normalized"}) or {}

    @staticmethod
    def _organization(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return _row_dict(row)

    @staticmethod
    def _membership(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return _row_dict(row)

    @staticmethod
    def _invitation(row: sqlite3.Row) -> dict[str, Any]:
        return _row_dict(row, omit={"token_hash", "email_normalized"}) or {}

    @staticmethod
    def _support(row: sqlite3.Row | None, *, now: int | None = None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = _row_dict(row) or {}
        if now is not None:
            result["seconds_left"] = max(0, int(row["expires_at"]) - now)
        return result

    @staticmethod
    def _audit_metadata(metadata: Mapping[str, Any] | None) -> str:
        value: Mapping[str, Any] = metadata or {}

        def inspect(item: Any) -> None:
            if isinstance(item, Mapping):
                for key, nested in item.items():
                    normalized = str(key).casefold()
                    if any(part in normalized for part in _SAFE_AUDIT_KEY_PARTS):
                        raise ValidationError(f"Секретное поле нельзя писать в аудит: {key}")
                    inspect(nested)
            elif isinstance(item, (list, tuple)):
                for nested in item:
                    inspect(nested)

        inspect(value)
        try:
            encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValidationError("Audit metadata должна быть JSON-совместимой") from exc
        if len(encoded.encode("utf-8")) > 8 * 1024:
            raise ValidationError("Audit metadata превышает 8 КБ")
        return encoded

    def _append_audit(
        self,
        connection: sqlite3.Connection,
        *,
        action: str,
        actor_user_id: str | None,
        organization_id: str | None = None,
        support_session_id: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        now: int,
    ) -> dict[str, Any]:
        clean_action = _bounded_text(action, "Действие аудита", maximum=120)
        if not clean_action:
            raise ValidationError("Действие аудита обязательно")
        event = {
            "id": _uuid(),
            "actor_user_id": actor_user_id,
            "organization_id": organization_id,
            "support_session_id": support_session_id,
            "action": clean_action,
            "target_type": _bounded_text(target_type, "Тип объекта", maximum=80),
            "target_id": _bounded_text(target_id, "ID объекта", maximum=160),
            "metadata_json": self._audit_metadata(metadata),
            "created_at": now,
        }
        connection.execute(
            """INSERT INTO audit_events
               (id, actor_user_id, organization_id, support_session_id, action,
                target_type, target_id, metadata_json, created_at)
               VALUES (:id, :actor_user_id, :organization_id, :support_session_id,
                       :action, :target_type, :target_id, :metadata_json, :created_at)""",
            event,
        )
        result = dict(event)
        result["metadata"] = json.loads(result.pop("metadata_json"))
        return result

    def append_audit(
        self,
        action: str,
        *,
        actor_user_id: str | None,
        organization_id: str | None = None,
        support_session_id: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        now: int | float | None = None,
    ) -> dict[str, Any]:
        """Append a sanitized event; audit rows cannot be changed or removed."""

        timestamp = _now(now)
        with self._transaction() as connection:
            return self._append_audit(
                connection,
                action=action,
                actor_user_id=actor_user_id,
                organization_id=organization_id,
                support_session_id=support_session_id,
                target_type=target_type,
                target_id=target_id,
                metadata=metadata,
                now=timestamp,
            )

    @staticmethod
    def _active_platform_user(
        connection: sqlite3.Connection, actor_user_id: str
    ) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM users WHERE id = ?", (actor_user_id,)).fetchone()
        if row is None:
            raise NotFound("Пользователь не найден")
        if row["status"] != "active" or row["platform_role"] not in PLATFORM_ROLES:
            raise PermissionDenied("Нет platform-доступа")
        return row

    def _require_platform(
        self, connection: sqlite3.Connection, actor_user_id: str, permission: str
    ) -> sqlite3.Row:
        user = self._active_platform_user(connection, actor_user_id)
        if permission not in permissions_for_platform_role(user["platform_role"]):
            raise PermissionDenied("Недостаточно platform-прав")
        return user

    def _active_membership(
        self,
        connection: sqlite3.Connection,
        actor_user_id: str,
        organization_id: str,
    ) -> tuple[sqlite3.Row, sqlite3.Row, sqlite3.Row]:
        user = connection.execute("SELECT * FROM users WHERE id = ?", (actor_user_id,)).fetchone()
        organization = connection.execute(
            "SELECT * FROM organizations WHERE id = ?", (organization_id,)
        ).fetchone()
        membership = connection.execute(
            "SELECT * FROM memberships WHERE organization_id = ? AND user_id = ?",
            (organization_id, actor_user_id),
        ).fetchone()
        if user is None or organization is None or membership is None:
            raise PermissionDenied("Нет доступа к компании")
        if (
            user["status"] != "active"
            or organization["status"] != "active"
            or membership["status"] != "active"
        ):
            raise PermissionDenied("Доступ к компании отключён")
        return user, organization, membership

    def _active_support(
        self,
        connection: sqlite3.Connection,
        actor_user_id: str,
        organization_id: str,
        support_session_id: str,
        *,
        now: int,
        primary_session_id: str | None = None,
    ) -> sqlite3.Row | None:
        support = connection.execute(
            """SELECT ss.*
               FROM support_sessions ss
               JOIN users u ON u.id = ss.platform_user_id
               JOIN organizations o ON o.id = ss.organization_id
               WHERE ss.id = ? AND ss.platform_user_id = ? AND ss.organization_id = ?
                 AND ss.scope = 'read_only' AND ss.ended_at IS NULL
                 AND ss.expires_at > ? AND u.status = 'active'
                 AND u.platform_role IN ('platform_owner','platform_support')
                 AND o.status = 'active'""",
            (support_session_id, actor_user_id, organization_id, now),
        ).fetchone()
        if support is None:
            return None
        if (
            support["primary_session_id"] is not None
            and support["primary_session_id"] != primary_session_id
        ):
            return None
        return support

    def _organization_permissions_in_connection(
        self,
        connection: sqlite3.Connection,
        actor_user_id: str,
        organization_id: str,
        *,
        support_session_id: str | None,
        primary_session_id: str | None = None,
        now: int,
    ) -> frozenset[str]:
        try:
            _user, organization, membership = self._active_membership(
                connection, actor_user_id, organization_id
            )
        except PermissionDenied:
            if support_session_id and self._active_support(
                connection,
                actor_user_id,
                organization_id,
                support_session_id,
                now=now,
                primary_session_id=primary_session_id,
            ):
                return SUPPORT_READ_ONLY_PERMISSIONS
            return frozenset()
        if organization["mode"] == "demo":
            return DEMO_SESSION_PERMISSIONS
        return permissions_for_organization_role(membership["role"])

    def _require_organization(
        self,
        connection: sqlite3.Connection,
        actor_user_id: str,
        organization_id: str,
        permission: str,
        *,
        support_session_id: str | None = None,
        primary_session_id: str | None = None,
        now: int,
    ) -> frozenset[str]:
        permissions = self._organization_permissions_in_connection(
            connection,
            actor_user_id,
            organization_id,
            support_session_id=support_session_id,
            primary_session_id=primary_session_id,
            now=now,
        )
        if permission not in permissions:
            raise PermissionDenied("Недостаточно прав в компании")
        return permissions

    def _issue_session(
        self,
        connection: sqlite3.Connection,
        *,
        user_id: str,
        organization_id: str | None,
        ip: str | None,
        user_agent: str | None,
        ttl_seconds: int,
        now: int,
    ) -> tuple[dict[str, Any], str, str]:
        raw_token, token_hash = _issue_secret()
        raw_csrf, csrf_hash = _issue_secret()
        session = {
            "id": _uuid(),
            "user_id": user_id,
            "organization_id": organization_id,
            "token_hash": token_hash,
            "csrf_hash": csrf_hash,
            "ip": _bounded_text(ip, "IP", maximum=64),
            "user_agent": _bounded_text(user_agent, "User-Agent", maximum=512),
            "created_at": now,
            "expires_at": now + ttl_seconds,
            "last_seen_at": now,
            "revoked_at": None,
        }
        connection.execute(
            """INSERT INTO sessions
               (id,user_id,organization_id,token_hash,csrf_hash,ip,user_agent,
                created_at,expires_at,last_seen_at,revoked_at)
               VALUES (:id,:user_id,:organization_id,:token_hash,:csrf_hash,:ip,:user_agent,
                       :created_at,:expires_at,:last_seen_at,:revoked_at)""",
            session,
        )
        public = {key: value for key, value in session.items() if key not in {"token_hash", "csrf_hash"}}
        return public, raw_token, raw_csrf

    def _login_result(
        self,
        connection: sqlite3.Connection,
        *,
        user_id: str,
        organization_id: str | None,
        session: dict[str, Any],
        session_token: str,
        csrf_token: str,
    ) -> dict[str, Any]:
        user = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        organization = None
        membership = None
        if organization_id is not None:
            organization = connection.execute(
                "SELECT * FROM organizations WHERE id = ?", (organization_id,)
            ).fetchone()
            membership = connection.execute(
                "SELECT * FROM memberships WHERE organization_id = ? AND user_id = ?",
                (organization_id, user_id),
            ).fetchone()
        return {
            "user": self._user(user),
            "organization": self._organization(organization),
            "membership": self._membership(membership),
            "session": session,
            "session_token": session_token,
            "csrf_token": csrf_token,
            "expires_at": session["expires_at"],
        }

    # ------------------------------------------------------------------
    # Bootstrap and authentication

    def bootstrap_platform_admin(
        self,
        email: str,
        display_name: str,
        password: str,
        *,
        now: int | float | None = None,
    ) -> dict[str, Any]:
        """Create the first platform owner; refuses once any user exists."""

        shown_email, normalized_email = _normalize_email(email)
        name = _display_name(display_name)
        encoded_password = self._hasher.hash(_password(password))
        timestamp = _now(now)
        user_id = _uuid()
        with self._transaction() as connection:
            count = int(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0])
            if count:
                raise Conflict("Bootstrap разрешён только для пустой базы")
            connection.execute(
                """INSERT INTO users
                   (id,email,email_normalized,display_name,password_hash,platform_role,status,
                    created_at,updated_at)
                   VALUES (?,?,?,?,?,'platform_owner','active',?,?)""",
                (user_id, shown_email, normalized_email, name, encoded_password, timestamp, timestamp),
            )
            self._append_audit(
                connection,
                action="platform.bootstrap",
                actor_user_id=user_id,
                target_type="user",
                target_id=user_id,
                metadata={"platform_role": "platform_owner"},
                now=timestamp,
            )
            row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            return self._user(row)

    def login(
        self,
        email: str,
        password: str,
        ip: str | None = None,
        user_agent: str | None = None,
        *,
        organization_id: str | None = None,
        ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
        now: int | float | None = None,
    ) -> dict[str, Any]:
        """Authenticate and issue one-time raw session and CSRF secrets."""

        _shown_email, normalized_email = _normalize_email(email)
        if not isinstance(password, str):
            password = ""
        ttl_seconds = _ttl(ttl_seconds, maximum=30 * 24 * 60 * 60, label="сессии")
        timestamp = _now(now)

        with self._read() as connection:
            candidate = connection.execute(
                "SELECT * FROM users WHERE email_normalized = ?", (normalized_email,)
            ).fetchone()
        encoded = candidate["password_hash"] if candidate is not None else self._dummy_password_hash
        verified = self._verify_password(encoded, password)
        if candidate is None or not verified or candidate["status"] != "active":
            raise AuthenticationFailed("Неверный email или пароль")

        with self._transaction() as connection:
            user = connection.execute("SELECT * FROM users WHERE id = ?", (candidate["id"],)).fetchone()
            if user is None or user["status"] != "active" or user["password_hash"] != encoded:
                raise AuthenticationFailed("Неверный email или пароль")

            active_memberships = connection.execute(
                """SELECT m.*, o.name AS organization_name
                   FROM memberships m JOIN organizations o ON o.id = m.organization_id
                   WHERE m.user_id = ? AND m.status = 'active' AND o.status = 'active'
                   ORDER BY o.name, o.id""",
                (user["id"],),
            ).fetchall()
            selected_organization_id = organization_id
            if selected_organization_id is None:
                # Platform identity stays in the platform realm by default,
                # even if that person also has an explicit tenant membership.
                if user["platform_role"] in PLATFORM_ROLES:
                    selected_organization_id = None
                elif len(active_memberships) == 1:
                    selected_organization_id = active_memberships[0]["organization_id"]
                elif len(active_memberships) > 1:
                    raise OrganizationSelectionRequired(
                        "Нужно выбрать компанию",
                        details={
                            "organizations": [
                                {
                                    "id": row["organization_id"],
                                    "name": row["organization_name"],
                                    "role": row["role"],
                                }
                                for row in active_memberships
                            ]
                        },
                    )
                elif user["platform_role"] not in PLATFORM_ROLES:
                    raise PermissionDenied("Нет активной компании")
            else:
                if not any(
                    row["organization_id"] == selected_organization_id for row in active_memberships
                ):
                    raise PermissionDenied("Нет доступа к выбранной компании")

            if self._hasher.check_needs_rehash(user["password_hash"]):
                connection.execute(
                    "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                    (self._hasher.hash(password), timestamp, user["id"]),
                )

            session, raw_token, raw_csrf = self._issue_session(
                connection,
                user_id=user["id"],
                organization_id=selected_organization_id,
                ip=ip,
                user_agent=user_agent,
                ttl_seconds=ttl_seconds,
                now=timestamp,
            )
            self._append_audit(
                connection,
                action="auth.login",
                actor_user_id=user["id"],
                organization_id=selected_organization_id,
                target_type="session",
                target_id=session["id"],
                metadata={},
                now=timestamp,
            )
            return self._login_result(
                connection,
                user_id=user["id"],
                organization_id=selected_organization_id,
                session=session,
                session_token=raw_token,
                csrf_token=raw_csrf,
            )

    def _session_in_connection(
        self,
        connection: sqlite3.Connection,
        token_hash: bytes,
        *,
        now: int,
        support_session_id: str | None = None,
    ) -> dict[str, Any] | None:
        row = connection.execute(
            """SELECT * FROM sessions
               WHERE token_hash = ? AND revoked_at IS NULL AND expires_at > ?""",
            (token_hash, now),
        ).fetchone()
        if row is None:
            return None
        user = connection.execute("SELECT * FROM users WHERE id = ?", (row["user_id"],)).fetchone()
        if user is None or user["status"] != "active":
            return None

        organization = None
        membership = None
        permissions = frozenset()
        if row["organization_id"] is not None:
            organization = connection.execute(
                "SELECT * FROM organizations WHERE id = ?", (row["organization_id"],)
            ).fetchone()
            membership = connection.execute(
                "SELECT * FROM memberships WHERE organization_id = ? AND user_id = ?",
                (row["organization_id"], row["user_id"]),
            ).fetchone()
            if (
                organization is None
                or membership is None
                or organization["status"] != "active"
                or membership["status"] != "active"
            ):
                return None
            permissions = (
                DEMO_SESSION_PERMISSIONS
                if organization["mode"] == "demo"
                else permissions_for_organization_role(membership["role"])
            )

        support = None
        if support_session_id is not None:
            support_ref = connection.execute(
                "SELECT organization_id FROM support_sessions WHERE id = ?",
                (support_session_id,),
            ).fetchone()
            support_organization_id = row["organization_id"]
            if support_organization_id is None and support_ref is not None:
                support_organization_id = support_ref["organization_id"]
            if support_organization_id:
                support = self._active_support(
                    connection,
                    user["id"],
                    support_organization_id,
                    support_session_id,
                    now=now,
                    primary_session_id=row["id"],
                )
            if support is None:
                # Explicit support context is part of the request authority.
                # Do not silently downgrade it; the HTTP layer can retry the
                # primary session and clear its stale support cookie.
                return None
        elif user["platform_role"] in PLATFORM_ROLES:
            support = connection.execute(
                """SELECT ss.* FROM support_sessions ss
                   JOIN organizations o ON o.id = ss.organization_id
                   WHERE ss.platform_user_id = ? AND ss.primary_session_id = ?
                     AND ss.ended_at IS NULL AND ss.expires_at > ?
                     AND ss.scope = 'read_only' AND o.status = 'active'
                   ORDER BY ss.created_at DESC LIMIT 1""",
                (user["id"], row["id"], now),
            ).fetchone()

        return {
            "session": _row_dict(row, omit={"token_hash", "csrf_hash"}),
            "user": self._user(user),
            "organization": self._organization(organization),
            "membership": self._membership(membership),
            "permissions": sorted(permissions),
            "platform_permissions": sorted(permissions_for_platform_role(user["platform_role"])),
            "support_session": self._support(support, now=now),
        }

    def session(
        self,
        token: str,
        *,
        now: int | float | None = None,
        support_session_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Resolve a live session against current user/org/membership state."""

        token_hash = _hash_secret(token)
        if not token_hash:
            return None
        timestamp = _now(now)
        with self._read() as connection:
            return self._session_in_connection(
                connection, token_hash, now=timestamp, support_session_id=support_session_id
            )

    def verify_csrf(
        self,
        token: str,
        raw_csrf: str,
        *,
        now: int | float | None = None,
    ) -> bool:
        """Constant-time CSRF proof check, valid only while the session is live."""

        token_hash = _hash_secret(token)
        csrf_hash = _hash_secret(raw_csrf)
        if not token_hash or not csrf_hash:
            return False
        timestamp = _now(now)
        with self._read() as connection:
            row = connection.execute(
                "SELECT csrf_hash FROM sessions WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
            if row is None or not hmac.compare_digest(bytes(row["csrf_hash"]), csrf_hash):
                return False
            return self._session_in_connection(connection, token_hash, now=timestamp) is not None

    def logout(self, token: str, *, now: int | float | None = None) -> bool:
        """Revoke a session.  Repeated logout is harmless and returns ``False``."""

        token_hash = _hash_secret(token)
        if not token_hash:
            return False
        timestamp = _now(now)
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE token_hash = ? AND revoked_at IS NULL",
                (token_hash,),
            ).fetchone()
            if row is None:
                return False
            connection.execute(
                "UPDATE sessions SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (timestamp, row["id"]),
            )
            connection.execute(
                """UPDATE support_sessions SET ended_at = ?, ended_by_user_id = ?
                   WHERE primary_session_id = ? AND ended_at IS NULL""",
                (timestamp, row["user_id"], row["id"]),
            )
            self._append_audit(
                connection,
                action="auth.logout",
                actor_user_id=row["user_id"],
                organization_id=row["organization_id"],
                target_type="session",
                target_id=row["id"],
                metadata={},
                now=timestamp,
            )
            return True

    # ------------------------------------------------------------------
    # Permission helpers

    def platform_permissions(self, actor_user_id_or_role: str) -> frozenset[str]:
        if actor_user_id_or_role in PLATFORM_ROLES:
            return permissions_for_platform_role(actor_user_id_or_role)
        with self._read() as connection:
            row = connection.execute(
                "SELECT platform_role, status FROM users WHERE id = ?",
                (actor_user_id_or_role,),
            ).fetchone()
            if row is None or row["status"] != "active":
                return frozenset()
            return permissions_for_platform_role(row["platform_role"])

    def has_platform_permission(self, actor_user_id: str, permission: str) -> bool:
        return permission in self.platform_permissions(actor_user_id)

    def require_platform_permission(self, actor_user_id: str, permission: str) -> None:
        with self._read() as connection:
            self._require_platform(connection, actor_user_id, permission)

    def organization_permissions(
        self,
        actor_user_id: str,
        organization_id: str,
        *,
        support_session_id: str | None = None,
        primary_session_id: str | None = None,
        now: int | float | None = None,
    ) -> frozenset[str]:
        timestamp = _now(now)
        with self._read() as connection:
            return self._organization_permissions_in_connection(
                connection,
                actor_user_id,
                organization_id,
                support_session_id=support_session_id,
                primary_session_id=primary_session_id,
                now=timestamp,
            )

    def has_organization_permission(
        self,
        actor_user_id: str,
        organization_id: str,
        permission: str,
        *,
        support_session_id: str | None = None,
        primary_session_id: str | None = None,
        now: int | float | None = None,
    ) -> bool:
        return permission in self.organization_permissions(
            actor_user_id,
            organization_id,
            support_session_id=support_session_id,
            primary_session_id=primary_session_id,
            now=now,
        )

    def require_organization_permission(
        self,
        actor_user_id: str,
        organization_id: str,
        permission: str,
        *,
        support_session_id: str | None = None,
        primary_session_id: str | None = None,
        now: int | float | None = None,
    ) -> None:
        timestamp = _now(now)
        with self._read() as connection:
            self._require_organization(
                connection,
                actor_user_id,
                organization_id,
                permission,
                support_session_id=support_session_id,
                primary_session_id=primary_session_id,
                now=timestamp,
            )

    # ------------------------------------------------------------------
    # Organizations and invitations

    def _insert_invitation(
        self,
        connection: sqlite3.Connection,
        *,
        organization_id: str,
        email: str,
        email_normalized: str,
        display_name: str,
        role: str,
        invited_by_user_id: str,
        token_hash: bytes,
        expires_at: int,
        now: int,
    ) -> sqlite3.Row:
        # A resend invalidates every older raw link for this email and tenant.
        connection.execute(
            """UPDATE invitations SET status = 'revoked', revoked_at = ?
               WHERE organization_id = ? AND email_normalized = ? AND status = 'pending'""",
            (now, organization_id, email_normalized),
        )
        invitation_id = _uuid()
        connection.execute(
            """INSERT INTO invitations
               (id,organization_id,email,email_normalized,display_name,role,token_hash,status,
                invited_by_user_id,accepted_user_id,created_at,expires_at,accepted_at,revoked_at)
               VALUES (?,?,?,?,?,?,?,'pending',?,NULL,?,?,NULL,NULL)""",
            (
                invitation_id,
                organization_id,
                email,
                email_normalized,
                display_name,
                role,
                token_hash,
                invited_by_user_id,
                now,
                expires_at,
            ),
        )
        return connection.execute(
            "SELECT * FROM invitations WHERE id = ?", (invitation_id,)
        ).fetchone()

    def create_organization(
        self,
        actor_user_id: str,
        name: str,
        owner_name: str,
        owner_email: str,
        *,
        mode: str = "standard",
        invitation_ttl_seconds: int = DEFAULT_INVITATION_TTL_SECONDS,
        now: int | float | None = None,
    ) -> dict[str, Any]:
        """Atomically create a company and its pending first-owner invitation."""

        clean_name = _organization_name(name)
        clean_mode = _organization_mode(mode)
        clean_owner_name = _display_name(owner_name)
        shown_email, normalized_email = _normalize_email(owner_email)
        ttl = _ttl(
            invitation_ttl_seconds,
            maximum=30 * 24 * 60 * 60,
            label="приглашения",
        )
        timestamp = _now(now)
        raw_token, token_hash = _issue_secret()
        organization_id = _uuid()
        with self._transaction() as connection:
            self._require_platform(
                connection, actor_user_id, "platform.organization.create"
            )
            connection.execute(
                """INSERT INTO organizations(id,name,status,created_at,updated_at,mode)
                   VALUES (?,?,'active',?,?,?)""",
                (organization_id, clean_name, timestamp, timestamp, clean_mode),
            )
            invitation = self._insert_invitation(
                connection,
                organization_id=organization_id,
                email=shown_email,
                email_normalized=normalized_email,
                display_name=clean_owner_name,
                role="owner",
                invited_by_user_id=actor_user_id,
                token_hash=token_hash,
                expires_at=timestamp + ttl,
                now=timestamp,
            )
            self._append_audit(
                connection,
                action="organization.created",
                actor_user_id=actor_user_id,
                organization_id=organization_id,
                target_type="organization",
                target_id=organization_id,
                metadata={"owner_email": normalized_email, "mode": clean_mode},
                now=timestamp,
            )
            self._append_audit(
                connection,
                action="member.invited",
                actor_user_id=actor_user_id,
                organization_id=organization_id,
                target_type="invitation",
                target_id=invitation["id"],
                metadata={"email": normalized_email, "role": "owner"},
                now=timestamp,
            )
            organization = connection.execute(
                "SELECT * FROM organizations WHERE id = ?", (organization_id,)
            ).fetchone()
            return {
                "organization": self._organization(organization),
                "invitation": self._invitation(invitation),
                "activation_token": raw_token,
            }

    def invite_member(
        self,
        actor_user_id: str,
        organization_id: str,
        email: str,
        name: str,
        role: str,
        *,
        ttl_seconds: int = DEFAULT_INVITATION_TTL_SECONDS,
        now: int | float | None = None,
    ) -> dict[str, Any]:
        clean_role = _role(role)
        shown_email, normalized_email = _normalize_email(email)
        clean_name = _display_name(name)
        ttl = _ttl(ttl_seconds, maximum=30 * 24 * 60 * 60, label="приглашения")
        timestamp = _now(now)
        raw_token, token_hash = _issue_secret()

        with self._transaction() as connection:
            platform_admin = False
            try:
                self._require_platform(connection, actor_user_id, "platform.user.manage")
                platform_admin = True
            except (NotFound, PermissionDenied):
                self._active_membership(
                    connection, actor_user_id, organization_id
                )
                self._require_organization(
                    connection,
                    actor_user_id,
                    organization_id,
                    "member.invite",
                    now=timestamp,
                )
            organization = connection.execute(
                "SELECT status, mode FROM organizations WHERE id = ?", (organization_id,)
            ).fetchone()
            if organization is None or organization["status"] != "active":
                raise NotFound("Активная компания не найдена")
            if organization["mode"] == "demo":
                raise PermissionDenied("Сотрудниками демо-компании управляет только Akeda")
            if clean_role == "owner" and not platform_admin:
                raise PermissionDenied("Владелец компании не может назначать владельцев")

            existing = connection.execute(
                """SELECT m.id FROM memberships m JOIN users u ON u.id = m.user_id
                   WHERE m.organization_id = ? AND u.email_normalized = ?""",
                (organization_id, normalized_email),
            ).fetchone()
            if existing is not None:
                raise Conflict("Пользователь уже состоит в компании")

            invitation = self._insert_invitation(
                connection,
                organization_id=organization_id,
                email=shown_email,
                email_normalized=normalized_email,
                display_name=clean_name,
                role=clean_role,
                invited_by_user_id=actor_user_id,
                token_hash=token_hash,
                expires_at=timestamp + ttl,
                now=timestamp,
            )
            self._append_audit(
                connection,
                action="member.invited",
                actor_user_id=actor_user_id,
                organization_id=organization_id,
                target_type="invitation",
                target_id=invitation["id"],
                metadata={
                    "email": normalized_email,
                    "role": clean_role,
                    "authority": "platform" if platform_admin else "organization",
                },
                now=timestamp,
            )
            return {
                "invitation": self._invitation(invitation),
                "activation_token": raw_token,
            }

    def provision_organization(
        self,
        actor_user_id: str,
        name: str,
        owner_name: str,
        owner_email: str,
        *,
        mode: str = "standard",
        now: int | float | None = None,
    ) -> dict[str, Any]:
        """Create a company and active owner managed directly by Akeda.

        The starter password is returned exactly once and only its Argon2id
        hash is persisted.  This intentionally bypasses email delivery and the
        invitation flow without weakening session, CSRF or audit boundaries.
        """

        clean_name = _organization_name(name)
        clean_mode = _organization_mode(mode)
        clean_owner_name = _display_name(owner_name)
        shown_email, normalized_email = _normalize_email(owner_email)
        starter_password = _generated_access_password()
        encoded_password = self._hasher.hash(starter_password)
        timestamp = _now(now)
        organization_id, user_id, membership_id = _uuid(), _uuid(), _uuid()

        with self._transaction() as connection:
            self._require_platform(
                connection, actor_user_id, "platform.organization.create"
            )
            if connection.execute(
                "SELECT 1 FROM users WHERE email_normalized = ?", (normalized_email,)
            ).fetchone():
                raise Conflict("Этот логин уже используется")
            connection.execute(
                """INSERT INTO organizations(id,name,status,created_at,updated_at,mode)
                   VALUES (?,?,'active',?,?,?)""",
                (organization_id, clean_name, timestamp, timestamp, clean_mode),
            )
            connection.execute(
                """INSERT INTO users
                   (id,email,email_normalized,display_name,password_hash,platform_role,status,
                    created_at,updated_at)
                   VALUES (?,?,?,?,?,NULL,'active',?,?)""",
                (
                    user_id,
                    shown_email,
                    normalized_email,
                    clean_owner_name,
                    encoded_password,
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                """INSERT INTO memberships
                   (id,organization_id,user_id,role,status,created_at,updated_at)
                   VALUES (?,?,?,'owner','active',?,?)""",
                (membership_id, organization_id, user_id, timestamp, timestamp),
            )
            self._append_audit(
                connection,
                action="organization.created",
                actor_user_id=actor_user_id,
                organization_id=organization_id,
                target_type="organization",
                target_id=organization_id,
                metadata={"owner_email": normalized_email, "mode": clean_mode, "via": "akeda"},
                now=timestamp,
            )
            self._append_audit(
                connection,
                action="member.provisioned",
                actor_user_id=actor_user_id,
                organization_id=organization_id,
                target_type="membership",
                target_id=membership_id,
                metadata={"email": normalized_email, "role": "owner"},
                now=timestamp,
            )
            organization = connection.execute(
                "SELECT * FROM organizations WHERE id = ?", (organization_id,)
            ).fetchone()
            user = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            membership = connection.execute(
                "SELECT * FROM memberships WHERE id = ?", (membership_id,)
            ).fetchone()
            return {
                "organization": self._organization(organization),
                "user": self._user(user),
                "membership": self._membership(membership),
                "login": shown_email,
                "starter_password": starter_password,
            }

    def provision_member(
        self,
        actor_user_id: str,
        organization_id: str,
        email: str,
        name: str,
        role: str,
        *,
        now: int | float | None = None,
    ) -> dict[str, Any]:
        """Create an active employee and one-time starter credentials.

        Platform operators may provision any organization role.  A company
        owner may provision only non-owner employees inside their own standard
        organization.  This keeps self-service useful without turning it into
        an ownership-transfer or platform-escalation path.
        """

        clean_role = _role(role)
        shown_email, normalized_email = _normalize_email(email)
        clean_name = _display_name(name)
        starter_password = _generated_access_password()
        encoded_password = self._hasher.hash(starter_password)
        timestamp = _now(now)
        user_id, membership_id = _uuid(), _uuid()

        with self._transaction() as connection:
            authority = "platform"
            try:
                self._require_platform(connection, actor_user_id, "platform.user.manage")
            except (NotFound, PermissionDenied):
                authority = "organization"
                self._require_organization(
                    connection,
                    actor_user_id,
                    organization_id,
                    "member.invite",
                    now=timestamp,
                )
                if clean_role == "owner":
                    raise PermissionDenied("Владелец компании не может назначать владельцев")
            organization = connection.execute(
                "SELECT * FROM organizations WHERE id = ?", (organization_id,)
            ).fetchone()
            if organization is None or organization["status"] != "active":
                raise NotFound("Активная компания не найдена")
            if organization["mode"] == "demo":
                raise PermissionDenied("Сотрудники демо-компании управляются отдельно")
            if connection.execute(
                "SELECT 1 FROM users WHERE email_normalized = ?", (normalized_email,)
            ).fetchone():
                raise Conflict("Этот логин уже используется")
            connection.execute(
                """INSERT INTO users
                   (id,email,email_normalized,display_name,password_hash,platform_role,status,
                    created_at,updated_at)
                   VALUES (?,?,?,?,?,NULL,'active',?,?)""",
                (
                    user_id,
                    shown_email,
                    normalized_email,
                    clean_name,
                    encoded_password,
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                """INSERT INTO memberships
                   (id,organization_id,user_id,role,status,created_at,updated_at)
                   VALUES (?,?,?,?,'active',?,?)""",
                (membership_id, organization_id, user_id, clean_role, timestamp, timestamp),
            )
            self._append_audit(
                connection,
                action="member.provisioned",
                actor_user_id=actor_user_id,
                organization_id=organization_id,
                target_type="membership",
                target_id=membership_id,
                metadata={
                    "email": normalized_email,
                    "role": clean_role,
                    "authority": authority,
                },
                now=timestamp,
            )
            user = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            membership = connection.execute(
                "SELECT * FROM memberships WHERE id = ?", (membership_id,)
            ).fetchone()
            return {
                "user": self._user(user),
                "membership": self._membership(membership),
                "login": shown_email,
                "starter_password": starter_password,
            }

    def update_managed_member(
        self,
        actor_user_id: str,
        organization_id: str,
        membership_id: str,
        *,
        name: str,
        email: str,
        role: str,
        status: str,
        now: int | float | None = None,
    ) -> dict[str, Any]:
        """Edit a company employee from platform or owner self-service."""

        clean_name = _display_name(name)
        shown_email, normalized_email = _normalize_email(email)
        clean_role = _role(role)
        clean_status = _status(status, MEMBERSHIP_STATUSES, "участника")
        timestamp = _now(now)

        with self._transaction() as connection:
            authority = "platform"
            try:
                self._require_platform(connection, actor_user_id, "platform.user.manage")
            except (NotFound, PermissionDenied):
                authority = "organization"
                self._require_organization(
                    connection,
                    actor_user_id,
                    organization_id,
                    "member.manage",
                    now=timestamp,
                )
            target = connection.execute(
                """SELECT m.*, u.email, u.email_normalized, u.display_name, u.status AS user_status
                   FROM memberships m JOIN users u ON u.id=m.user_id
                   WHERE m.id=? AND m.organization_id=?""",
                (membership_id, organization_id),
            ).fetchone()
            if target is None:
                raise NotFound("Сотрудник компании не найден")
            if authority == "organization" and (
                target["role"] == "owner" or clean_role == "owner"
            ):
                raise PermissionDenied("Управление владельцами доступно только Akeda")
            organization = connection.execute(
                "SELECT mode FROM organizations WHERE id = ?", (organization_id,)
            ).fetchone()
            if organization is None or organization["mode"] == "demo":
                raise PermissionDenied("Сотрудниками демо-компании управляет только Akeda")
            other_memberships = int(
                connection.execute(
                    "SELECT COUNT(*) FROM memberships WHERE user_id=? AND organization_id!=?",
                    (target["user_id"], organization_id),
                ).fetchone()[0]
            )
            identity_changes = (
                clean_name != target["display_name"]
                or normalized_email != target["email_normalized"]
            )
            if authority == "organization" and other_memberships and identity_changes:
                raise PermissionDenied(
                    "Имя и логин сотрудника с доступом к нескольким компаниям меняет Akeda"
                )
            duplicate = connection.execute(
                "SELECT id FROM users WHERE email_normalized=? AND id!=?",
                (normalized_email, target["user_id"]),
            ).fetchone()
            if duplicate is not None:
                raise Conflict("Этот логин уже используется")
            removes_active_owner = (
                target["role"] == "owner"
                and target["status"] == "active"
                and (clean_role != "owner" or clean_status != "active")
            )
            if removes_active_owner and not self._other_active_owner_count(
                connection, organization_id, membership_id
            ):
                raise SoleOwner("Нельзя отключить или понизить единственного владельца")

            changed = (
                clean_name != target["display_name"]
                or normalized_email != target["email_normalized"]
                or clean_role != target["role"]
                or clean_status != target["status"]
            )
            if changed:
                connection.execute(
                    """UPDATE users SET email=?, email_normalized=?, display_name=?, updated_at=?
                       WHERE id=?""",
                    (shown_email, normalized_email, clean_name, timestamp, target["user_id"]),
                )
                connection.execute(
                    """UPDATE memberships SET role=?, status=?, updated_at=?
                       WHERE id=? AND organization_id=?""",
                    (clean_role, clean_status, timestamp, membership_id, organization_id),
                )
                self._revoke_user_sessions(
                    connection, target["user_id"], organization_id=organization_id, now=timestamp
                )
                self._append_audit(
                    connection,
                    action="member.updated",
                    actor_user_id=actor_user_id,
                    organization_id=organization_id,
                    target_type="membership",
                    target_id=membership_id,
                    metadata={
                        "old_email": target["email_normalized"],
                        "new_email": normalized_email,
                        "old_role": target["role"],
                        "new_role": clean_role,
                        "old_status": target["status"],
                        "new_status": clean_status,
                        "authority": authority,
                    },
                    now=timestamp,
                )
            updated = connection.execute(
                """SELECT m.*, u.email, u.display_name, u.status AS user_status
                   FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.id=?""",
                (membership_id,),
            ).fetchone()
            return _row_dict(updated) or {}

    def reset_managed_member_password(
        self,
        actor_user_id: str,
        organization_id: str,
        membership_id: str,
        *,
        now: int | float | None = None,
    ) -> dict[str, Any]:
        """Rotate an employee password and return the new raw value once."""

        starter_password = _generated_access_password()
        encoded_password = self._hasher.hash(starter_password)
        timestamp = _now(now)
        with self._transaction() as connection:
            authority = "platform"
            try:
                self._require_platform(connection, actor_user_id, "platform.user.manage")
            except (NotFound, PermissionDenied):
                authority = "organization"
                self._require_organization(
                    connection,
                    actor_user_id,
                    organization_id,
                    "member.manage",
                    now=timestamp,
                )
            target = connection.execute(
                """SELECT m.id AS membership_id, m.user_id, m.role,
                          u.email, u.email_normalized, o.mode AS organization_mode
                   FROM memberships m JOIN users u ON u.id=m.user_id
                   JOIN organizations o ON o.id=m.organization_id
                   WHERE m.id=? AND m.organization_id=?""",
                (membership_id, organization_id),
            ).fetchone()
            if target is None:
                raise NotFound("Сотрудник компании не найден")
            if target["organization_mode"] == "demo":
                raise PermissionDenied("Сотрудниками демо-компании управляет только Akeda")
            if authority == "organization" and target["role"] == "owner":
                raise PermissionDenied("Пароль владельца может перевыпустить только Akeda")
            if authority == "organization" and connection.execute(
                "SELECT 1 FROM memberships WHERE user_id=? AND organization_id!=? LIMIT 1",
                (target["user_id"], organization_id),
            ).fetchone():
                raise PermissionDenied(
                    "Пароль сотрудника с доступом к нескольким компаниям перевыпускает Akeda"
                )
            connection.execute(
                "UPDATE users SET password_hash=?, updated_at=? WHERE id=?",
                (encoded_password, timestamp, target["user_id"]),
            )
            self._revoke_user_sessions(connection, target["user_id"], now=timestamp)
            self._append_audit(
                connection,
                action="member.access_rotated",
                actor_user_id=actor_user_id,
                organization_id=organization_id,
                target_type="membership",
                target_id=membership_id,
                metadata={
                    "login": target["email_normalized"],
                    "authority": authority,
                },
                now=timestamp,
            )
            return {
                "membership_id": membership_id,
                "login": target["email"],
                "starter_password": starter_password,
            }

    def activate_invitation(
        self,
        token: str,
        password: str,
        display_name: str | None = None,
        *,
        ip: str | None = None,
        user_agent: str | None = None,
        session_ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
        now: int | float | None = None,
    ) -> dict[str, Any]:
        """Consume an invitation once, create membership and issue a session.

        If the email already has an account, ``password`` authenticates that
        account and is never used to reset its password.
        """

        token_hash = _hash_secret(token)
        if not token_hash:
            raise InvalidInvitation("Приглашение недействительно или истекло")
        clean_password = _password(password)
        new_password_hash = self._hasher.hash(clean_password)
        clean_display_name = _display_name(display_name) if display_name is not None else None
        ttl = _ttl(
            session_ttl_seconds,
            maximum=30 * 24 * 60 * 60,
            label="сессии",
        )
        timestamp = _now(now)

        with self._transaction() as connection:
            invitation = connection.execute(
                "SELECT * FROM invitations WHERE token_hash = ? AND status = 'pending'",
                (token_hash,),
            ).fetchone()
            if invitation is None or invitation["expires_at"] <= timestamp:
                raise InvalidInvitation("Приглашение недействительно или истекло")
            organization = connection.execute(
                "SELECT * FROM organizations WHERE id = ?", (invitation["organization_id"],)
            ).fetchone()
            if organization is None or organization["status"] != "active":
                raise InvalidInvitation("Приглашение недействительно или истекло")

            user = connection.execute(
                "SELECT * FROM users WHERE email_normalized = ?",
                (invitation["email_normalized"],),
            ).fetchone()
            if user is not None:
                if user["status"] != "active" or not self._verify_password(
                    user["password_hash"], clean_password
                ):
                    raise AuthenticationFailed("Неверный email или пароль")
                user_id = user["id"]
            else:
                user_id = _uuid()
                chosen_name = clean_display_name or invitation["display_name"]
                connection.execute(
                    """INSERT INTO users
                       (id,email,email_normalized,display_name,password_hash,platform_role,status,
                        created_at,updated_at)
                       VALUES (?,?,?,?,?,NULL,'active',?,?)""",
                    (
                        user_id,
                        invitation["email"],
                        invitation["email_normalized"],
                        chosen_name,
                        new_password_hash,
                        timestamp,
                        timestamp,
                    ),
                )

            existing_membership = connection.execute(
                "SELECT * FROM memberships WHERE organization_id = ? AND user_id = ?",
                (invitation["organization_id"], user_id),
            ).fetchone()
            if existing_membership is not None:
                raise Conflict("Пользователь уже состоит в компании")

            consumed = connection.execute(
                """UPDATE invitations
                   SET status = 'accepted', accepted_user_id = ?, accepted_at = ?
                   WHERE id = ? AND status = 'pending' AND expires_at > ?""",
                (user_id, timestamp, invitation["id"], timestamp),
            )
            if consumed.rowcount != 1:
                raise InvalidInvitation("Приглашение недействительно или истекло")

            membership_id = _uuid()
            connection.execute(
                """INSERT INTO memberships
                   (id,organization_id,user_id,role,status,created_at,updated_at)
                   VALUES (?,?,?,?,'active',?,?)""",
                (
                    membership_id,
                    invitation["organization_id"],
                    user_id,
                    invitation["role"],
                    timestamp,
                    timestamp,
                ),
            )
            session, raw_session, raw_csrf = self._issue_session(
                connection,
                user_id=user_id,
                organization_id=invitation["organization_id"],
                ip=ip,
                user_agent=user_agent,
                ttl_seconds=ttl,
                now=timestamp,
            )
            self._append_audit(
                connection,
                action="member.invitation_accepted",
                actor_user_id=user_id,
                organization_id=invitation["organization_id"],
                target_type="membership",
                target_id=membership_id,
                metadata={"role": invitation["role"]},
                now=timestamp,
            )
            self._append_audit(
                connection,
                action="auth.login",
                actor_user_id=user_id,
                organization_id=invitation["organization_id"],
                target_type="session",
                target_id=session["id"],
                metadata={"via": "invitation"},
                now=timestamp,
            )
            return self._login_result(
                connection,
                user_id=user_id,
                organization_id=invitation["organization_id"],
                session=session,
                session_token=raw_session,
                csrf_token=raw_csrf,
            )

    # ------------------------------------------------------------------
    # Status and role mutations

    @staticmethod
    def _revoke_user_sessions(
        connection: sqlite3.Connection,
        user_id: str,
        *,
        now: int,
        organization_id: str | None = None,
    ) -> None:
        if organization_id is None:
            connection.execute(
                "UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                (now, user_id),
            )
        else:
            connection.execute(
                """UPDATE sessions SET revoked_at = ?
                   WHERE user_id = ? AND organization_id = ? AND revoked_at IS NULL""",
                (now, user_id, organization_id),
            )

    @staticmethod
    def _other_active_owner_count(
        connection: sqlite3.Connection,
        organization_id: str,
        excluded_membership_id: str,
    ) -> int:
        return int(
            connection.execute(
                """SELECT COUNT(*) FROM memberships m
                   JOIN users u ON u.id = m.user_id
                   WHERE m.organization_id = ? AND m.id != ?
                     AND m.role = 'owner' AND m.status = 'active' AND u.status = 'active'""",
                (organization_id, excluded_membership_id),
            ).fetchone()[0]
        )

    def update_membership(
        self,
        actor_user_id: str,
        organization_id: str,
        membership_id: str,
        *,
        role: str | None = None,
        status: str | None = None,
        now: int | float | None = None,
    ) -> dict[str, Any]:
        """Update an organization role/status while preserving an active owner."""

        new_role = _role(role) if role is not None else None
        new_status = (
            _status(status, MEMBERSHIP_STATUSES, "участника") if status is not None else None
        )
        if new_role is None and new_status is None:
            raise ValidationError("Нужно изменить роль или статус")
        timestamp = _now(now)

        with self._transaction() as connection:
            platform_admin = False
            actor_membership = None
            try:
                self._require_platform(connection, actor_user_id, "platform.user.manage")
                platform_admin = True
            except (NotFound, PermissionDenied):
                _actor, _organization, actor_membership = self._active_membership(
                    connection, actor_user_id, organization_id
                )
                self._require_organization(
                    connection,
                    actor_user_id,
                    organization_id,
                    "member.manage",
                    now=timestamp,
                )
            target = connection.execute(
                "SELECT * FROM memberships WHERE id = ? AND organization_id = ?",
                (membership_id, organization_id),
            ).fetchone()
            if target is None:
                raise NotFound("Участник компании не найден")

            resulting_role = new_role or target["role"]
            resulting_status = new_status or target["status"]
            if not platform_admin and (
                target["role"] == "owner" or resulting_role == "owner"
            ):
                raise PermissionDenied("Управление владельцами доступно только Akeda")

            removes_active_owner = (
                target["role"] == "owner"
                and target["status"] == "active"
                and (resulting_role != "owner" or resulting_status != "active")
            )
            if removes_active_owner and not self._other_active_owner_count(
                connection, organization_id, membership_id
            ):
                raise SoleOwner("Нельзя отключить или понизить единственного владельца")

            if resulting_role == target["role"] and resulting_status == target["status"]:
                return self._membership(target) or {}

            connection.execute(
                """UPDATE memberships SET role = ?, status = ?, updated_at = ?
                   WHERE id = ? AND organization_id = ?""",
                (resulting_role, resulting_status, timestamp, membership_id, organization_id),
            )
            self._revoke_user_sessions(
                connection,
                target["user_id"],
                organization_id=organization_id,
                now=timestamp,
            )
            if resulting_status == "disabled":
                target_user = connection.execute(
                    "SELECT email_normalized FROM users WHERE id = ?", (target["user_id"],)
                ).fetchone()
                connection.execute(
                    """UPDATE invitations SET status = 'revoked', revoked_at = ?
                       WHERE organization_id = ? AND email_normalized = ? AND status = 'pending'""",
                    (timestamp, organization_id, target_user["email_normalized"]),
                )
            self._append_audit(
                connection,
                action="member.updated",
                actor_user_id=actor_user_id,
                organization_id=organization_id,
                target_type="membership",
                target_id=membership_id,
                metadata={
                    "old_role": target["role"],
                    "new_role": resulting_role,
                    "old_status": target["status"],
                    "new_status": resulting_status,
                    "authority": "platform" if platform_admin else "organization",
                },
                now=timestamp,
            )
            updated = connection.execute(
                "SELECT * FROM memberships WHERE id = ?", (membership_id,)
            ).fetchone()
            return self._membership(updated) or {}

    def update_user_status(
        self,
        actor_user_id: str,
        user_id: str,
        status: str,
        *,
        now: int | float | None = None,
    ) -> dict[str, Any]:
        new_status = _status(status, USER_STATUSES, "пользователя")
        timestamp = _now(now)
        with self._transaction() as connection:
            self._require_platform(connection, actor_user_id, "platform.user.manage")
            target = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if target is None:
                raise NotFound("Пользователь не найден")
            if target["status"] == new_status:
                return self._user(target)
            if target["platform_role"] == "platform_owner" and new_status != "active":
                other_owners = int(
                    connection.execute(
                        """SELECT COUNT(*) FROM users
                           WHERE id != ? AND platform_role = 'platform_owner' AND status = 'active'""",
                        (user_id,),
                    ).fetchone()[0]
                )
                if not other_owners:
                    raise SoleOwner("Нельзя отключить единственного владельца платформы")
            if new_status == "disabled":
                orphaned = connection.execute(
                    """SELECT o.id, o.name
                       FROM organizations o
                       JOIN memberships mine
                         ON mine.organization_id=o.id AND mine.user_id=?
                       WHERE o.status='active' AND mine.role='owner' AND mine.status='active'
                         AND NOT EXISTS (
                           SELECT 1 FROM memberships other
                           JOIN users other_user ON other_user.id=other.user_id
                           WHERE other.organization_id=o.id AND other.id!=mine.id
                             AND other.role='owner' AND other.status='active'
                             AND other_user.status='active'
                         )
                       ORDER BY o.name, o.id""",
                    (user_id,),
                ).fetchall()
                if orphaned:
                    raise SoleOwner(
                        "Нельзя отключить единственного владельца компании",
                        details={
                            "organizations": [
                                {"id": row["id"], "name": row["name"]} for row in orphaned
                            ]
                        },
                    )

            connection.execute(
                "UPDATE users SET status = ?, updated_at = ? WHERE id = ?",
                (new_status, timestamp, user_id),
            )
            if new_status == "disabled":
                self._revoke_user_sessions(connection, user_id, now=timestamp)
                connection.execute(
                    """UPDATE support_sessions SET ended_at = ?, ended_by_user_id = ?
                       WHERE platform_user_id = ? AND ended_at IS NULL""",
                    (timestamp, actor_user_id, user_id),
                )
                connection.execute(
                    """UPDATE invitations SET status = 'revoked', revoked_at = ?
                       WHERE status = 'pending' AND email_normalized = ?""",
                    (timestamp, target["email_normalized"]),
                )
            self._append_audit(
                connection,
                action="user.status_updated",
                actor_user_id=actor_user_id,
                target_type="user",
                target_id=user_id,
                metadata={"old_status": target["status"], "new_status": new_status},
                now=timestamp,
            )
            updated = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            return self._user(updated)

    def update_organization_status(
        self,
        actor_user_id: str,
        organization_id: str,
        status: str,
        *,
        now: int | float | None = None,
    ) -> dict[str, Any]:
        new_status = _status(status, ORGANIZATION_STATUSES, "компании")
        timestamp = _now(now)
        with self._transaction() as connection:
            organization = connection.execute(
                "SELECT * FROM organizations WHERE id = ?", (organization_id,)
            ).fetchone()
            if organization is None:
                raise NotFound("Компания не найдена")
            platform_allowed = False
            try:
                self._require_platform(
                    connection, actor_user_id, "platform.organization.manage"
                )
                platform_allowed = True
            except (NotFound, PermissionDenied):
                pass
            if not platform_allowed:
                self._require_organization(
                    connection,
                    actor_user_id,
                    organization_id,
                    "organization.manage",
                    now=timestamp,
                )
            if organization["status"] == new_status:
                return self._organization(organization) or {}
            if new_status == "active":
                owners = int(
                    connection.execute(
                        """SELECT COUNT(*) FROM memberships m JOIN users u ON u.id=m.user_id
                           WHERE m.organization_id=? AND m.role='owner' AND m.status='active'
                             AND u.status='active'""",
                        (organization_id,),
                    ).fetchone()[0]
                )
                if not owners:
                    raise SoleOwner("Нельзя включить компанию без активного владельца")
            connection.execute(
                "UPDATE organizations SET status = ?, updated_at = ? WHERE id = ?",
                (new_status, timestamp, organization_id),
            )
            if new_status == "disabled":
                connection.execute(
                    """UPDATE sessions SET revoked_at = ?
                       WHERE organization_id = ? AND revoked_at IS NULL""",
                    (timestamp, organization_id),
                )
                connection.execute(
                    """UPDATE support_sessions SET ended_at = ?, ended_by_user_id = ?
                       WHERE organization_id = ? AND ended_at IS NULL""",
                    (timestamp, actor_user_id, organization_id),
                )
                connection.execute(
                    """UPDATE invitations SET status = 'revoked', revoked_at = ?
                       WHERE organization_id = ? AND status = 'pending'""",
                    (timestamp, organization_id),
                )
            self._append_audit(
                connection,
                action="organization.status_updated",
                actor_user_id=actor_user_id,
                organization_id=organization_id,
                target_type="organization",
                target_id=organization_id,
                metadata={"old_status": organization["status"], "new_status": new_status},
                now=timestamp,
            )
            updated = connection.execute(
                "SELECT * FROM organizations WHERE id = ?", (organization_id,)
            ).fetchone()
            return self._organization(updated) or {}

    # ------------------------------------------------------------------
    # Explicit, read-only support access

    def enter_company_view(
        self,
        actor_user_id: str,
        organization_id: str,
        *,
        primary_session_id: str | None = None,
        now: int | float | None = None,
    ) -> dict[str, Any]:
        """Open the company context from Akeda's admin without ticket UX.

        The underlying record remains explicit and auditable, but the operator
        does not need to invent a ticket number, reason or duration.  Its
        lifetime is an implementation guard bounded by the primary login
        session and is not presented as a separate product concept.
        """

        return self.start_support(
            actor_user_id,
            organization_id,
            "Просмотр компании из админ-панели Akeda",
            MAX_SUPPORT_DURATION_MINUTES,
            primary_session_id=primary_session_id,
            now=now,
        )

    def start_support(
        self,
        actor_user_id: str,
        organization_id: str,
        reason: str,
        duration_minutes: int,
        *,
        primary_session_id: str | None = None,
        now: int | float | None = None,
    ) -> dict[str, Any]:
        clean_reason = _bounded_text(reason, "Причина support-доступа", maximum=500)
        if clean_reason is None or len(clean_reason) < 4:
            raise ValidationError("Укажите причину support-доступа")
        duration = _ttl(
            duration_minutes,
            maximum=MAX_SUPPORT_DURATION_MINUTES,
            label="support-сессии",
        )
        timestamp = _now(now)
        with self._transaction() as connection:
            self._require_platform(connection, actor_user_id, "platform.support.start")
            organization = connection.execute(
                "SELECT * FROM organizations WHERE id = ? AND status = 'active'",
                (organization_id,),
            ).fetchone()
            if organization is None:
                raise NotFound("Активная компания не найдена")
            if primary_session_id is not None:
                primary = connection.execute(
                    """SELECT * FROM sessions WHERE id = ? AND user_id = ?
                       AND revoked_at IS NULL AND expires_at > ?""",
                    (primary_session_id, actor_user_id, timestamp),
                ).fetchone()
                if primary is None:
                    raise PermissionDenied("Основная сессия недействительна")

            active_support = connection.execute(
                """SELECT id, organization_id FROM support_sessions
                   WHERE platform_user_id = ? AND ended_at IS NULL AND expires_at > ?
                   ORDER BY created_at DESC LIMIT 1""",
                (actor_user_id, timestamp),
            ).fetchone()
            if active_support is not None:
                raise Conflict(
                    "Сначала завершите текущий сеанс поддержки",
                    details={
                        "support_session_id": active_support["id"],
                        "organization_id": active_support["organization_id"],
                    },
                )

            support_id = _uuid()
            expires_at = timestamp + duration * 60
            connection.execute(
                """INSERT INTO support_sessions
                   (id,platform_user_id,organization_id,primary_session_id,reason,scope,
                    created_at,expires_at,ended_at,ended_by_user_id)
                   VALUES (?,?,?,?,?,'read_only',?,?,NULL,NULL)""",
                (
                    support_id,
                    actor_user_id,
                    organization_id,
                    primary_session_id,
                    clean_reason,
                    timestamp,
                    expires_at,
                ),
            )
            self._append_audit(
                connection,
                action="support.started",
                actor_user_id=actor_user_id,
                organization_id=organization_id,
                support_session_id=support_id,
                target_type="support_session",
                target_id=support_id,
                metadata={"reason": clean_reason, "expires_at": expires_at, "scope": "read_only"},
                now=timestamp,
            )
            row = connection.execute(
                "SELECT * FROM support_sessions WHERE id = ?", (support_id,)
            ).fetchone()
            return self._support(row, now=timestamp) or {}

    def end_support(
        self,
        actor_user_id: str,
        support_session_id: str,
        *,
        now: int | float | None = None,
    ) -> bool:
        timestamp = _now(now)
        with self._transaction() as connection:
            support = connection.execute(
                "SELECT * FROM support_sessions WHERE id = ?", (support_session_id,)
            ).fetchone()
            if support is None:
                raise NotFound("Support-сессия не найдена")
            if support["platform_user_id"] != actor_user_id:
                try:
                    self._require_platform(
                        connection, actor_user_id, "platform.organization.manage"
                    )
                except (NotFound, PermissionDenied):
                    self._require_organization(
                        connection,
                        actor_user_id,
                        support["organization_id"],
                        "support.grant",
                        now=timestamp,
                    )
            else:
                self._active_platform_user(connection, actor_user_id)
            if support["ended_at"] is not None:
                return False
            connection.execute(
                """UPDATE support_sessions SET ended_at = ?, ended_by_user_id = ?
                   WHERE id = ? AND ended_at IS NULL""",
                (timestamp, actor_user_id, support_session_id),
            )
            self._append_audit(
                connection,
                action="support.ended",
                actor_user_id=actor_user_id,
                organization_id=support["organization_id"],
                support_session_id=support_session_id,
                target_type="support_session",
                target_id=support_session_id,
                metadata={},
                now=timestamp,
            )
            return True

    # ------------------------------------------------------------------
    # Read models for the server/admin UI

    def _platform_admin_or_org_permission(
        self,
        connection: sqlite3.Connection,
        actor_user_id: str,
        organization_id: str,
        organization_permission: str,
        *,
        support_session_id: str | None,
        primary_session_id: str | None,
        now: int,
    ) -> str:
        try:
            self._require_platform(connection, actor_user_id, "platform.user.manage")
            return "platform"
        except (NotFound, PermissionDenied):
            self._require_organization(
                connection,
                actor_user_id,
                organization_id,
                organization_permission,
                support_session_id=support_session_id,
                primary_session_id=primary_session_id,
                now=now,
            )
            return "organization"

    def list_members(
        self,
        actor_user_id: str,
        organization_id: str,
        *,
        support_session_id: str | None = None,
        primary_session_id: str | None = None,
        now: int | float | None = None,
    ) -> list[dict[str, Any]]:
        timestamp = _now(now)
        with self._read() as connection:
            self._platform_admin_or_org_permission(
                connection,
                actor_user_id,
                organization_id,
                "member.read",
                support_session_id=support_session_id,
                primary_session_id=primary_session_id,
                now=timestamp,
            )
            rows = connection.execute(
                """SELECT m.id AS membership_id, m.organization_id, m.role,
                          m.status AS membership_status, m.created_at AS membership_created_at,
                          m.updated_at AS membership_updated_at,
                          u.id AS user_id, u.email, u.display_name, u.status AS user_status
                   FROM memberships m JOIN users u ON u.id = m.user_id
                   WHERE m.organization_id = ?
                   ORDER BY CASE m.role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END,
                            u.display_name, u.id""",
                (organization_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_project_collaborators(
        self,
        actor_user_id: str,
        organization_id: str,
        *,
        support_session_id: str | None = None,
        primary_session_id: str | None = None,
        now: int | float | None = None,
    ) -> list[dict[str, Any]]:
        """Return active people that may be shown in project ownership controls.

        Catalog readers need names for authorship and responsibility filters, but
        they must not receive emails, invitation state or other member-management
        data.  This deliberately authorizes with ``project.read`` rather than
        ``member.read`` and exposes only the small public collaboration model.
        """

        timestamp = _now(now)
        with self._read() as connection:
            self._require_organization(
                connection,
                actor_user_id,
                organization_id,
                "project.read",
                support_session_id=support_session_id,
                primary_session_id=primary_session_id,
                now=timestamp,
            )
            rows = connection.execute(
                """SELECT u.id AS user_id, u.display_name, m.role
                   FROM memberships m JOIN users u ON u.id = m.user_id
                   WHERE m.organization_id = ? AND m.status = 'active'
                     AND u.status = 'active'
                   ORDER BY CASE m.role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END,
                            u.display_name, u.id""",
                (organization_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_invitations(
        self,
        actor_user_id: str,
        organization_id: str,
        *,
        include_closed: bool = False,
        now: int | float | None = None,
    ) -> list[dict[str, Any]]:
        timestamp = _now(now)
        with self._read() as connection:
            self._platform_admin_or_org_permission(
                connection,
                actor_user_id,
                organization_id,
                "member.invite",
                support_session_id=None,
                primary_session_id=None,
                now=timestamp,
            )
            clause = "" if include_closed else " AND status = 'pending' AND expires_at > ?"
            parameters: tuple[Any, ...] = (
                (organization_id,) if include_closed else (organization_id, timestamp)
            )
            rows = connection.execute(
                "SELECT * FROM invitations WHERE organization_id = ?" + clause
                + " ORDER BY created_at DESC, id DESC",
                parameters,
            ).fetchall()
            return [self._invitation(row) for row in rows]

    def list_audit(
        self,
        actor_user_id: str,
        organization_id: str | None = None,
        *,
        support_session_id: str | None = None,
        primary_session_id: str | None = None,
        limit: int = 100,
        now: int | float | None = None,
    ) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > 500:
            raise ValidationError("limit должен быть от 1 до 500")
        timestamp = _now(now)
        with self._read() as connection:
            if organization_id is None:
                self._require_platform(connection, actor_user_id, "platform.audit.read")
                rows = connection.execute(
                    """SELECT a.*, u.display_name AS actor_name, u.email AS actor_email
                       FROM audit_events a LEFT JOIN users u ON u.id=a.actor_user_id
                       ORDER BY a.created_at DESC, a.id DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
            else:
                platform_allowed = False
                try:
                    self._require_platform(connection, actor_user_id, "platform.audit.read")
                    platform_allowed = True
                except (NotFound, PermissionDenied):
                    pass
                if not platform_allowed:
                    self._require_organization(
                        connection,
                        actor_user_id,
                        organization_id,
                        "audit.read",
                        support_session_id=support_session_id,
                        primary_session_id=primary_session_id,
                        now=timestamp,
                    )
                rows = connection.execute(
                    """SELECT a.*, u.display_name AS actor_name, u.email AS actor_email
                       FROM audit_events a LEFT JOIN users u ON u.id=a.actor_user_id
                       WHERE a.organization_id = ?
                       ORDER BY a.created_at DESC, a.id DESC LIMIT ?""",
                    (organization_id, limit),
                ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["metadata"] = json.loads(item.pop("metadata_json"))
                result.append(item)
            return result

    def snapshot(
        self,
        actor_user_id: str,
        organization_id: str | None = None,
        *,
        support_session_id: str | None = None,
        primary_session_id: str | None = None,
        now: int | float | None = None,
    ) -> dict[str, Any]:
        """Return safe identity/admin data for the authenticated actor."""

        timestamp = _now(now)
        with self._read() as connection:
            actor = connection.execute("SELECT * FROM users WHERE id = ?", (actor_user_id,)).fetchone()
            if actor is None or actor["status"] != "active":
                raise PermissionDenied("Пользователь отключён или не найден")
            platform_permissions = permissions_for_platform_role(actor["platform_role"])

            organization_sql = """SELECT o.*,
                       mine.role AS role, mine.status AS membership_status,
                       (SELECT COUNT(*) FROM memberships mc
                        JOIN users uc ON uc.id=mc.user_id
                        WHERE mc.organization_id=o.id AND mc.status='active'
                          AND uc.status='active') AS member_count,
                       (SELECT uo.display_name FROM memberships mo
                        JOIN users uo ON uo.id=mo.user_id
                        WHERE mo.organization_id=o.id AND mo.role='owner'
                          AND mo.status='active' AND uo.status='active'
                        ORDER BY mo.created_at, mo.id LIMIT 1) AS owner_name,
                       (SELECT uo.email FROM memberships mo
                        JOIN users uo ON uo.id=mo.user_id
                        WHERE mo.organization_id=o.id AND mo.role='owner'
                          AND mo.status='active' AND uo.status='active'
                        ORDER BY mo.created_at, mo.id LIMIT 1) AS owner_email,
                       COALESCE((SELECT MAX(a.created_at) FROM audit_events a
                                 WHERE a.organization_id=o.id), o.updated_at) AS last_activity_at,
                       CASE WHEN EXISTS(
                           SELECT 1 FROM support_sessions ss
                           WHERE ss.organization_id=o.id AND ss.ended_at IS NULL
                             AND ss.expires_at>?
                       ) THEN 'active' ELSE 'none' END AS support_status
                FROM organizations o
                LEFT JOIN memberships mine
                  ON mine.organization_id=o.id AND mine.user_id=?"""
            parameters: list[Any] = [timestamp, actor_user_id]
            if not platform_permissions:
                organization_sql += " WHERE mine.id IS NOT NULL"
            organization_sql += " ORDER BY o.name, o.id"
            organization_rows = connection.execute(organization_sql, parameters).fetchall()
            organizations = []
            for row in organization_rows:
                item = {
                    key: row[key]
                    for key in (
                        "id",
                        "name",
                        "status",
                        "mode",
                        "created_at",
                        "updated_at",
                        "role",
                        "membership_status",
                        "member_count",
                        "owner_name",
                        "owner_email",
                        "last_activity_at",
                        "support_status",
                    )
                }
                item["migration_pending"] = True
                organizations.append(item)

            support_rows: list[sqlite3.Row] = []
            if platform_permissions:
                support_sql = """SELECT ss.*, u.display_name AS actor_name,
                                          u.email AS actor_email, o.name AS organization_name
                                   FROM support_sessions ss
                                   JOIN users u ON u.id=ss.platform_user_id
                                   JOIN organizations o ON o.id=ss.organization_id"""
                support_parameters: tuple[Any, ...] = ()
                if actor["platform_role"] != "platform_owner":
                    support_sql += " WHERE ss.platform_user_id=?"
                    support_parameters = (actor_user_id,)
                support_sql += " ORDER BY ss.created_at DESC, ss.id DESC LIMIT 100"
                support_rows = connection.execute(support_sql, support_parameters).fetchall()

            support_sessions = []
            for row in support_rows:
                item = self._support(row, now=timestamp) or {}
                item["status"] = (
                    "ended"
                    if row["ended_at"] is not None
                    else "expired"
                    if row["expires_at"] <= timestamp
                    else "active"
                )
                support_sessions.append(item)

            audit_events: list[dict[str, Any]] = []
            if "platform.audit.read" in platform_permissions:
                rows = connection.execute(
                    """SELECT a.*, u.display_name AS actor_name, u.email AS actor_email
                       FROM audit_events a LEFT JOIN users u ON u.id=a.actor_user_id
                       ORDER BY a.created_at DESC, a.id DESC LIMIT 100"""
                ).fetchall()
                for row in rows:
                    event = dict(row)
                    event["metadata"] = json.loads(event.pop("metadata_json"))
                    audit_events.append(event)

            result: dict[str, Any] = {
                "user": self._user(actor),
                "platform_permissions": sorted(platform_permissions),
                "organizations": organizations,
                "organization": None,
                "membership": None,
                "permissions": [],
                "support_session": None,
                "support_sessions": support_sessions,
                "members": [],
                "memberships": [],
                "invitations": [],
                "audit_events": audit_events,
                "blocked": {"projects": True, "studio": True},
                "migration_pending": True,
            }
            if organization_id is None:
                return result

            organization = connection.execute(
                "SELECT * FROM organizations WHERE id = ?", (organization_id,)
            ).fetchone()
            if organization is None:
                raise NotFound("Компания не найдена")
            membership = connection.execute(
                "SELECT * FROM memberships WHERE organization_id = ? AND user_id = ?",
                (organization_id, actor_user_id),
            ).fetchone()
            permissions = self._organization_permissions_in_connection(
                connection,
                actor_user_id,
                organization_id,
                support_session_id=support_session_id,
                primary_session_id=primary_session_id,
                now=timestamp,
            )
            platform_admin = "platform.user.manage" in platform_permissions
            if "organization.read" not in permissions and not platform_admin:
                raise PermissionDenied("Нет доступа к компании")
            support = None
            if support_session_id:
                support = self._active_support(
                    connection,
                    actor_user_id,
                    organization_id,
                    support_session_id,
                    primary_session_id=primary_session_id,
                    now=timestamp,
                )

            member_rows: list[sqlite3.Row] = []
            if platform_admin or "member.read" in permissions:
                member_rows = connection.execute(
                    """SELECT m.id AS membership_id, m.organization_id, m.role,
                              m.status AS membership_status, m.created_at AS membership_created_at,
                              m.updated_at AS membership_updated_at,
                              u.id AS user_id, u.email, u.display_name, u.status AS user_status,
                              (SELECT MAX(s.created_at) FROM sessions s
                               WHERE s.user_id=u.id) AS last_login_at
                       FROM memberships m JOIN users u ON u.id=m.user_id
                       WHERE m.organization_id=?
                       ORDER BY CASE m.role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END,
                                u.display_name, u.id""",
                    (organization_id,),
                ).fetchall()
            invitation_rows: list[sqlite3.Row] = []
            if platform_admin or "member.invite" in permissions:
                invitation_rows = connection.execute(
                    """SELECT * FROM invitations
                       WHERE organization_id=? AND status='pending' AND expires_at>?
                       ORDER BY created_at DESC, id DESC""",
                    (organization_id, timestamp),
                ).fetchall()
            selected_support_rows: list[sqlite3.Row] = []
            if platform_admin or "support.grant" in permissions:
                selected_support_rows = connection.execute(
                    """SELECT ss.*, u.display_name AS actor_name, u.email AS actor_email,
                              o.name AS organization_name
                       FROM support_sessions ss
                       JOIN users u ON u.id=ss.platform_user_id
                       JOIN organizations o ON o.id=ss.organization_id
                       WHERE ss.organization_id=?
                       ORDER BY ss.created_at DESC, ss.id DESC LIMIT 100""",
                    (organization_id,),
                ).fetchall()
            selected_support_sessions = []
            for row in selected_support_rows:
                item = self._support(row, now=timestamp) or {}
                item["status"] = (
                    "ended"
                    if row["ended_at"] is not None
                    else "expired"
                    if row["expires_at"] <= timestamp
                    else "active"
                )
                selected_support_sessions.append(item)

            selected_audit_events: list[dict[str, Any]] = []
            if "platform.audit.read" in platform_permissions or "audit.read" in permissions:
                rows = connection.execute(
                    """SELECT a.*, u.display_name AS actor_name, u.email AS actor_email
                       FROM audit_events a LEFT JOIN users u ON u.id=a.actor_user_id
                       WHERE a.organization_id=?
                       ORDER BY a.created_at DESC, a.id DESC LIMIT 100""",
                    (organization_id,),
                ).fetchall()
                for row in rows:
                    event = dict(row)
                    event["metadata"] = json.loads(event.pop("metadata_json"))
                    selected_audit_events.append(event)
            member_items = [dict(row) for row in member_rows]
            selected_organization = next(
                (item for item in organizations if item["id"] == organization_id),
                self._organization(organization) or {},
            )
            result.update(
                {
                    "organization": selected_organization,
                    "membership": self._membership(membership),
                    "permissions": sorted(permissions),
                    "support_session": self._support(support, now=timestamp),
                    "support_sessions": selected_support_sessions,
                    "members": member_items,
                    "memberships": member_items,
                    "invitations": [self._invitation(row) for row in invitation_rows],
                    "audit_events": selected_audit_events,
                    "admin_authority": "platform" if platform_admin else "organization",
                }
            )
            return result


__all__ = [
    "AuthenticationFailed",
    "Conflict",
    "DEFAULT_INVITATION_TTL_SECONDS",
    "DEFAULT_SESSION_TTL_SECONDS",
    "DEMO_SESSION_PERMISSIONS",
    "IdentityError",
    "IdentityStore",
    "InvalidInvitation",
    "MEMBERSHIP_STATUSES",
    "MAX_SUPPORT_DURATION_MINUTES",
    "NotFound",
    "ORGANIZATION_MODES",
    "ORGANIZATION_ROLES",
    "ORGANIZATION_ROLE_PERMISSIONS",
    "OrganizationSelectionRequired",
    "PLATFORM_ROLES",
    "PLATFORM_ROLE_PERMISSIONS",
    "PermissionDenied",
    "SCHEMA_VERSION",
    "SUPPORT_READ_ONLY_PERMISSIONS",
    "SoleOwner",
    "ValidationError",
    "permissions_for_organization_role",
    "permissions_for_platform_role",
]
