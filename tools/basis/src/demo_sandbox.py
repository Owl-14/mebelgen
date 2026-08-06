"""In-memory, session-isolated workspace for the public Akeda demo.

The current Studio stores its active ParamSpec in process-wide mutable state and
writes saves, previews, versions and exports to shared filesystem directories.
That is appropriate for the existing local editor, but it is not a safe base for
a public account whose credentials are intentionally shared.

This module is the storage boundary for that future public demo:

* :class:`DemoTemplate` owns an immutable JSON snapshot of example projects and
  optional scripted chat history;
* each :class:`DemoSession` gets a copy-on-write overlay which is never visible
  to another session;
* all state is memory-only and expires after a bounded lifetime;
* optimistic revisions make the API suitable for a later tenant-aware project
  repository without silently losing concurrent edits.

There is deliberately no ``Path``/filesystem API here.  In particular, this
module cannot write to ``paramspecs/``, ``projects/``, previews or ``out/``.
HTTP/auth integration belongs in a later layer, which must bind one unguessable
demo session ID to one authenticated browser session.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol, Sequence, runtime_checkable


DEFAULT_TTL_SECONDS = 60 * 60
DEFAULT_MAX_SESSIONS = 1_000
DEFAULT_MAX_PROJECTS_PER_SESSION = 100
DEFAULT_MAX_PROJECT_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_CHAT_MESSAGES_PER_PROJECT = 200
DEFAULT_MAX_CHAT_MESSAGE_BYTES = 32 * 1024
DEFAULT_MAX_CHAT_METADATA_BYTES = 8 * 1024

_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_CHAT_ROLES = frozenset({"user", "assistant", "system", "tool"})


class DemoSandboxError(RuntimeError):
    """Base class for expected demo repository errors."""


class InvalidDemoData(DemoSandboxError, ValueError):
    """A template, project or chat payload violates the JSON contract."""


class UnknownDemoSession(DemoSandboxError, KeyError):
    """The requested session does not exist (or has already been purged)."""


class ExpiredDemoSession(DemoSandboxError):
    """The requested session reached its absolute expiry boundary."""


class UnknownDemoProject(DemoSandboxError, KeyError):
    """The requested project is absent in this session."""


class DemoRevisionConflict(DemoSandboxError):
    """A write used a stale optimistic revision."""

    def __init__(self, project_id: str, expected: int, current: int):
        self.project_id = project_id
        self.expected = expected
        self.current = current
        super().__init__(
            f"stale demo project revision for {project_id!r}: "
            f"expected {expected}, current {current}"
        )


class DemoCapacityExceeded(DemoSandboxError):
    """A configured session/project/chat safety bound was reached."""


def _validate_positive_int(value: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise InvalidDemoData(f"{label} must be a positive integer")
    return value


def _validate_project_id(project_id: str) -> str:
    if not isinstance(project_id, str) or not _PROJECT_ID_RE.fullmatch(project_id):
        raise InvalidDemoData(
            "project_id must be 1..128 ASCII letters, digits, '.', '_' or '-', "
            "and start with a letter or digit"
        )
    if project_id in {".", ".."} or ".." in project_id:
        # IDs are never interpreted as paths here.  Rejecting traversal-shaped
        # values now prevents a future filesystem repository from inheriting a
        # dangerous contract.
        raise InvalidDemoData("project_id must not contain '..'")
    return project_id


def _canonical_json(
    value: Any,
    *,
    label: str,
    max_bytes: int | None = None,
    require_object: bool = False,
) -> bytes:
    if require_object and not isinstance(value, Mapping):
        raise InvalidDemoData(f"{label} must be a JSON object")
    if isinstance(value, Mapping) and not isinstance(value, dict):
        value = dict(value)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise InvalidDemoData(f"{label} must contain JSON-compatible data") from exc
    if max_bytes is not None and len(encoded) > max_bytes:
        raise DemoCapacityExceeded(
            f"{label} is {len(encoded)} bytes; limit is {max_bytes} bytes"
        )
    return encoded


def _decode_json(payload: bytes) -> Any:
    """Return a detached value so callers never receive an internal reference."""

    return json.loads(payload.decode("utf-8"))


def _validate_clock_value(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidDemoData("clock must return a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise InvalidDemoData("clock must return a finite number")
    return result


def _validate_chat_message(message: Mapping[str, Any], *, label: str) -> None:
    role = message.get("role")
    text = message.get("text")
    if role not in _CHAT_ROLES:
        raise InvalidDemoData(f"{label}.role must be one of {sorted(_CHAT_ROLES)}")
    if not isinstance(text, str) or not text.strip():
        raise InvalidDemoData(f"{label}.text must be a non-empty string")
    if len(text.encode("utf-8")) > DEFAULT_MAX_CHAT_MESSAGE_BYTES:
        raise DemoCapacityExceeded(
            f"{label}.text exceeds {DEFAULT_MAX_CHAT_MESSAGE_BYTES} bytes"
        )


@dataclass(frozen=True, init=False)
class DemoTemplate:
    """Immutable baseline for all sessions of one public demo company.

    JSON values are canonicalised to immutable ``bytes`` during construction.
    Mutating constructor inputs or values returned by :meth:`snapshot` therefore
    cannot alter the baseline.
    """

    template_id: str
    digest: str
    _projects: Mapping[str, bytes]
    _chat_history: Mapping[str, tuple[bytes, ...]]
    _metadata: bytes

    def __init__(
        self,
        projects: Mapping[str, Mapping[str, Any]],
        *,
        chat_history: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
        metadata: Mapping[str, Any] | None = None,
        template_id: str | None = None,
    ) -> None:
        if not isinstance(projects, Mapping) or not projects:
            raise InvalidDemoData("demo template must contain at least one project")
        if chat_history is not None and not isinstance(chat_history, Mapping):
            raise InvalidDemoData("chat_history must be an object keyed by project_id")
        if metadata is not None and not isinstance(metadata, Mapping):
            raise InvalidDemoData("template metadata must be a JSON object")

        frozen_projects: dict[str, bytes] = {}
        for raw_project_id, spec in projects.items():
            project_id = _validate_project_id(raw_project_id)
            frozen_projects[project_id] = _canonical_json(
                spec,
                label=f"projects[{project_id!r}]",
                max_bytes=DEFAULT_MAX_PROJECT_BYTES,
                require_object=True,
            )

        frozen_chat: dict[str, tuple[bytes, ...]] = {}
        for raw_project_id, messages in (chat_history or {}).items():
            project_id = _validate_project_id(raw_project_id)
            if project_id not in frozen_projects:
                raise InvalidDemoData(
                    f"chat history refers to unknown project {project_id!r}"
                )
            if isinstance(messages, (str, bytes)) or not isinstance(messages, Sequence):
                raise InvalidDemoData(f"chat_history[{project_id!r}] must be a sequence")
            if len(messages) > DEFAULT_MAX_CHAT_MESSAGES_PER_PROJECT:
                raise DemoCapacityExceeded(
                    f"chat_history[{project_id!r}] exceeds "
                    f"{DEFAULT_MAX_CHAT_MESSAGES_PER_PROJECT} messages"
                )
            encoded_messages: list[bytes] = []
            for index, message in enumerate(messages):
                if not isinstance(message, Mapping):
                    raise InvalidDemoData(
                        f"chat_history[{project_id!r}][{index}] must be an object"
                    )
                _validate_chat_message(
                    message, label=f"chat_history[{project_id!r}][{index}]"
                )
                encoded_messages.append(
                    _canonical_json(
                        message,
                        label=f"chat_history[{project_id!r}][{index}]",
                        max_bytes=(
                            DEFAULT_MAX_CHAT_MESSAGE_BYTES
                            + DEFAULT_MAX_CHAT_METADATA_BYTES
                        ),
                        require_object=True,
                    )
                )
            frozen_chat[project_id] = tuple(encoded_messages)

        encoded_metadata = _canonical_json(
            metadata or {},
            label="template metadata",
            max_bytes=DEFAULT_MAX_CHAT_METADATA_BYTES,
            require_object=True,
        )
        digest_payload = _canonical_json(
            {
                "projects": {
                    project_id: _decode_json(payload)
                    for project_id, payload in sorted(frozen_projects.items())
                },
                "chat_history": {
                    project_id: [_decode_json(payload) for payload in messages]
                    for project_id, messages in sorted(frozen_chat.items())
                },
                "metadata": _decode_json(encoded_metadata),
            },
            label="template digest",
        )
        digest = hashlib.sha256(digest_payload).hexdigest()
        resolved_template_id = template_id or f"demo-{digest[:16]}"
        if (
            not isinstance(resolved_template_id, str)
            or not resolved_template_id.strip()
            or len(resolved_template_id) > 128
        ):
            raise InvalidDemoData("template_id must be a non-empty string up to 128 chars")

        object.__setattr__(self, "template_id", resolved_template_id)
        object.__setattr__(self, "digest", digest)
        object.__setattr__(self, "_projects", MappingProxyType(frozen_projects))
        object.__setattr__(self, "_chat_history", MappingProxyType(frozen_chat))
        object.__setattr__(self, "_metadata", encoded_metadata)

    @property
    def project_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._projects))

    def snapshot(self) -> dict[str, Any]:
        """Return a fully detached baseline snapshot for diagnostics/seeding."""

        return {
            "template_id": self.template_id,
            "digest": self.digest,
            "metadata": _decode_json(self._metadata),
            "projects": {
                project_id: _decode_json(payload)
                for project_id, payload in self._projects.items()
            },
            "chat_history": {
                project_id: [_decode_json(payload) for payload in messages]
                for project_id, messages in self._chat_history.items()
            },
        }


@dataclass(frozen=True)
class DemoProjectSummary:
    project_id: str
    revision: int
    source: str  # "template" or "session"


@dataclass(frozen=True)
class DemoProject:
    project_id: str
    revision: int
    source: str
    spec: dict[str, Any]


@dataclass(frozen=True)
class DemoSessionInfo:
    session_id: str
    template_id: str
    template_digest: str
    principal_id: str | None
    created_at: float
    expires_at: float
    last_accessed_at: float


@dataclass(frozen=True)
class _ProjectOverlay:
    payload: bytes | None  # None is a session-local tombstone.
    revision: int


@dataclass
class _SessionState:
    session_id: str
    principal_id: str | None
    created_at: float
    expires_at: float
    last_accessed_at: float
    projects: dict[str, _ProjectOverlay] = field(default_factory=dict)
    chat_history: dict[str, list[bytes]] = field(default_factory=dict)


@runtime_checkable
class DemoProjectRepository(Protocol):
    """Minimal repository surface intended for later Studio integration."""

    @property
    def session_id(self) -> str: ...

    def list_projects(self) -> tuple[DemoProjectSummary, ...]: ...

    def read_project(self, project_id: str) -> DemoProject: ...

    def save_project(
        self,
        project_id: str,
        spec: Mapping[str, Any],
        *,
        expected_revision: int | None = None,
    ) -> DemoProject: ...

    def delete_project(
        self, project_id: str, *, expected_revision: int | None = None
    ) -> int: ...

    def reset_project(
        self, project_id: str, *, expected_revision: int | None = None
    ) -> DemoProject | None: ...

    def chat_messages(self, project_id: str) -> list[dict[str, Any]]: ...

    def append_chat_message(
        self,
        project_id: str,
        role: str,
        text: str,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]: ...


class DemoSession:
    """Capability-style repository facade bound to exactly one demo session."""

    __slots__ = ("_sandbox", "_session_id")

    def __init__(self, sandbox: DemoSandbox, session_id: str):
        self._sandbox = sandbox
        self._session_id = session_id

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def info(self) -> DemoSessionInfo:
        return self._sandbox._session_info(self._session_id)

    def list_projects(self) -> tuple[DemoProjectSummary, ...]:
        return self._sandbox._list_projects(self._session_id)

    def read_project(self, project_id: str) -> DemoProject:
        return self._sandbox._read_project(self._session_id, project_id)

    def save_project(
        self,
        project_id: str,
        spec: Mapping[str, Any],
        *,
        expected_revision: int | None = None,
    ) -> DemoProject:
        return self._sandbox._save_project(
            self._session_id,
            project_id,
            spec,
            expected_revision=expected_revision,
        )

    def delete_project(
        self, project_id: str, *, expected_revision: int | None = None
    ) -> int:
        return self._sandbox._delete_project(
            self._session_id, project_id, expected_revision=expected_revision
        )

    def reset_project(
        self, project_id: str, *, expected_revision: int | None = None
    ) -> DemoProject | None:
        """Restore one template project; remove a session-created project."""

        return self._sandbox._reset_project(
            self._session_id,
            project_id,
            expected_revision=expected_revision,
        )

    def chat_messages(self, project_id: str) -> list[dict[str, Any]]:
        return self._sandbox._chat_messages(self._session_id, project_id)

    def append_chat_message(
        self,
        project_id: str,
        role: str,
        text: str,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._sandbox._append_chat_message(
            self._session_id,
            project_id,
            role,
            text,
            metadata=metadata,
        )

    def clear_chat(self, project_id: str) -> None:
        self._sandbox._clear_chat(self._session_id, project_id)

    def reset(self) -> None:
        """Discard all session changes and return to the pristine template."""

        self._sandbox._reset_session(self._session_id)

    def snapshot(self) -> dict[str, Any]:
        return self._sandbox._session_snapshot(self._session_id)

    def close(self) -> bool:
        """Destroy the overlay immediately (normally logout does this)."""

        return self._sandbox.close_session(self._session_id)


class DemoSandbox:
    """Thread-safe owner of ephemeral demo sessions.

    Expiry is absolute rather than sliding: a busy public browser cannot retain
    memory forever simply by polling.  ``cleanup_expired`` can be called by a
    server timer, while every lookup also performs lazy expiry.
    """

    def __init__(
        self,
        template: DemoTemplate | Mapping[str, Mapping[str, Any]],
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
        max_projects_per_session: int = DEFAULT_MAX_PROJECTS_PER_SESSION,
        max_project_bytes: int = DEFAULT_MAX_PROJECT_BYTES,
        max_chat_messages_per_project: int = DEFAULT_MAX_CHAT_MESSAGES_PER_PROJECT,
        max_chat_message_bytes: int = DEFAULT_MAX_CHAT_MESSAGE_BYTES,
        clock: Callable[[], float] = time.time,
        session_id_factory: Callable[[], str] | None = None,
        message_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._template = (
            template if isinstance(template, DemoTemplate) else DemoTemplate(template)
        )
        self.ttl_seconds = _validate_positive_int(ttl_seconds, "ttl_seconds")
        self.max_sessions = _validate_positive_int(max_sessions, "max_sessions")
        self.max_projects_per_session = _validate_positive_int(
            max_projects_per_session, "max_projects_per_session"
        )
        self.max_project_bytes = _validate_positive_int(
            max_project_bytes, "max_project_bytes"
        )
        self.max_chat_messages_per_project = _validate_positive_int(
            max_chat_messages_per_project, "max_chat_messages_per_project"
        )
        self.max_chat_message_bytes = _validate_positive_int(
            max_chat_message_bytes, "max_chat_message_bytes"
        )
        if len(self.template.project_ids) > self.max_projects_per_session:
            raise DemoCapacityExceeded(
                "template has more projects than max_projects_per_session"
            )
        for project_id, payload in self.template._projects.items():
            if len(payload) > self.max_project_bytes:
                raise DemoCapacityExceeded(
                    f"template project {project_id!r} exceeds max_project_bytes"
                )
        for project_id, messages in self.template._chat_history.items():
            if len(messages) > self.max_chat_messages_per_project:
                raise DemoCapacityExceeded(
                    f"template chat {project_id!r} exceeds configured message limit"
                )
            for message in messages:
                decoded = _decode_json(message)
                if len(decoded["text"].encode("utf-8")) > self.max_chat_message_bytes:
                    raise DemoCapacityExceeded(
                        f"template chat {project_id!r} contains a message exceeding "
                        "the configured byte limit"
                    )

        self._clock = clock
        self._session_id_factory = session_id_factory or (
            lambda: secrets.token_urlsafe(32)
        )
        self._message_id_factory = message_id_factory or (
            lambda: secrets.token_urlsafe(18)
        )
        self._sessions: dict[str, _SessionState] = {}
        self._lock = threading.RLock()

    @property
    def template(self) -> DemoTemplate:
        """Pinned immutable publication used by every session in this manager."""

        return self._template

    def _now(self) -> float:
        return _validate_clock_value(self._clock())

    @staticmethod
    def _validate_principal_id(principal_id: str | None) -> str | None:
        if principal_id is None:
            return None
        if not isinstance(principal_id, str) or not principal_id.strip():
            raise InvalidDemoData("principal_id must be a non-empty string")
        if len(principal_id) > 256:
            raise InvalidDemoData("principal_id must be at most 256 characters")
        return principal_id

    def start_session(
        self, *, principal_id: str | None = None, ttl_seconds: int | None = None
    ) -> DemoSession:
        """Create a clean overlay.  Calling this for the same user is a reset."""

        principal = self._validate_principal_id(principal_id)
        ttl = self.ttl_seconds
        if ttl_seconds is not None:
            ttl = _validate_positive_int(ttl_seconds, "ttl_seconds")
        with self._lock:
            now = self._now()
            self._cleanup_locked(now)
            if len(self._sessions) >= self.max_sessions:
                raise DemoCapacityExceeded("maximum active demo sessions reached")
            session_id = self._unique_id_locked(
                self._session_id_factory, existing=self._sessions, label="session_id"
            )
            self._sessions[session_id] = _SessionState(
                session_id=session_id,
                principal_id=principal,
                created_at=now,
                expires_at=now + ttl,
                last_accessed_at=now,
            )
        return DemoSession(self, session_id)

    def resume_session(self, session_id: str) -> DemoSession:
        """Return a facade only after proving that the session is still active."""

        with self._lock:
            self._state_locked(session_id)
        return DemoSession(self, session_id)

    def close_session(self, session_id: str) -> bool:
        if not isinstance(session_id, str):
            return False
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def cleanup_expired(self) -> int:
        """Destroy expired overlays and return the number released."""

        with self._lock:
            return self._cleanup_locked(self._now())

    @property
    def active_session_count(self) -> int:
        with self._lock:
            self._cleanup_locked(self._now())
            return len(self._sessions)

    def _cleanup_locked(self, now: float) -> int:
        expired = [
            session_id
            for session_id, state in self._sessions.items()
            if now >= state.expires_at
        ]
        for session_id in expired:
            del self._sessions[session_id]
        return len(expired)

    @staticmethod
    def _unique_id_locked(
        factory: Callable[[], str], *, existing: Mapping[str, Any], label: str
    ) -> str:
        for _ in range(16):
            candidate = factory()
            if (
                not isinstance(candidate, str)
                or not candidate
                or len(candidate) > 256
                or any(ch.isspace() for ch in candidate)
            ):
                raise InvalidDemoData(f"{label} factory returned an invalid value")
            if candidate not in existing:
                return candidate
        raise DemoSandboxError(f"{label} factory produced too many collisions")

    def _state_locked(self, session_id: str) -> _SessionState:
        if not isinstance(session_id, str) or not session_id:
            raise UnknownDemoSession("unknown demo session")
        state = self._sessions.get(session_id)
        if state is None:
            raise UnknownDemoSession("unknown demo session")
        now = self._now()
        if now >= state.expires_at:
            del self._sessions[session_id]
            raise ExpiredDemoSession("demo session expired")
        state.last_accessed_at = now
        return state

    def _session_info(self, session_id: str) -> DemoSessionInfo:
        with self._lock:
            state = self._state_locked(session_id)
            return DemoSessionInfo(
                session_id=state.session_id,
                template_id=self.template.template_id,
                template_digest=self.template.digest,
                principal_id=state.principal_id,
                created_at=state.created_at,
                expires_at=state.expires_at,
                last_accessed_at=state.last_accessed_at,
            )

    def _current_project_locked(
        self, state: _SessionState, project_id: str
    ) -> tuple[bytes, int, str] | None:
        overlay = state.projects.get(project_id)
        if overlay is not None:
            if overlay.payload is None:
                return None
            return overlay.payload, overlay.revision, "session"
        payload = self.template._projects.get(project_id)
        if payload is None:
            return None
        return payload, 0, "template"

    @staticmethod
    def _current_revision_locked(state: _SessionState, project_id: str) -> int:
        overlay = state.projects.get(project_id)
        return overlay.revision if overlay is not None else 0

    @staticmethod
    def _check_expected_revision(
        project_id: str, expected_revision: int | None, current_revision: int
    ) -> None:
        if expected_revision is None:
            return
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
            raise InvalidDemoData("expected_revision must be a non-negative integer")
        if expected_revision < 0:
            raise InvalidDemoData("expected_revision must be a non-negative integer")
        if expected_revision != current_revision:
            raise DemoRevisionConflict(project_id, expected_revision, current_revision)

    def _list_projects(self, session_id: str) -> tuple[DemoProjectSummary, ...]:
        with self._lock:
            state = self._state_locked(session_id)
            project_ids = set(self.template._projects) | set(state.projects)
            result: list[DemoProjectSummary] = []
            for project_id in sorted(project_ids):
                current = self._current_project_locked(state, project_id)
                if current is None:
                    continue
                _, revision, source = current
                result.append(DemoProjectSummary(project_id, revision, source))
            return tuple(result)

    def _read_project(self, session_id: str, project_id: str) -> DemoProject:
        project_id = _validate_project_id(project_id)
        with self._lock:
            state = self._state_locked(session_id)
            current = self._current_project_locked(state, project_id)
            if current is None:
                raise UnknownDemoProject(project_id)
            payload, revision, source = current
            return DemoProject(project_id, revision, source, _decode_json(payload))

    def _save_project(
        self,
        session_id: str,
        project_id: str,
        spec: Mapping[str, Any],
        *,
        expected_revision: int | None,
    ) -> DemoProject:
        project_id = _validate_project_id(project_id)
        payload = _canonical_json(
            spec,
            label=f"project {project_id!r}",
            max_bytes=self.max_project_bytes,
            require_object=True,
        )
        with self._lock:
            state = self._state_locked(session_id)
            current = self._current_project_locked(state, project_id)
            revision = self._current_revision_locked(state, project_id)
            self._check_expected_revision(project_id, expected_revision, revision)
            if current is None:
                visible_count = sum(
                    self._current_project_locked(state, item) is not None
                    for item in (set(self.template._projects) | set(state.projects))
                )
                if visible_count >= self.max_projects_per_session:
                    raise DemoCapacityExceeded(
                        "maximum projects in one demo session reached"
                    )
            next_revision = revision + 1
            state.projects[project_id] = _ProjectOverlay(payload, next_revision)
            return DemoProject(
                project_id, next_revision, "session", _decode_json(payload)
            )

    def _delete_project(
        self,
        session_id: str,
        project_id: str,
        *,
        expected_revision: int | None,
    ) -> int:
        project_id = _validate_project_id(project_id)
        with self._lock:
            state = self._state_locked(session_id)
            current = self._current_project_locked(state, project_id)
            if current is None:
                raise UnknownDemoProject(project_id)
            revision = current[1]
            self._check_expected_revision(project_id, expected_revision, revision)
            next_revision = revision + 1
            state.projects[project_id] = _ProjectOverlay(None, next_revision)
            # If the same session recreates this ID, old conversation must not
            # unexpectedly reappear.
            state.chat_history[project_id] = []
            return next_revision

    def _reset_project(
        self,
        session_id: str,
        project_id: str,
        *,
        expected_revision: int | None,
    ) -> DemoProject | None:
        project_id = _validate_project_id(project_id)
        with self._lock:
            state = self._state_locked(session_id)
            if (
                project_id not in self.template._projects
                and project_id not in state.projects
            ):
                raise UnknownDemoProject(project_id)
            current = self._current_project_locked(state, project_id)
            revision = self._current_revision_locked(state, project_id)
            self._check_expected_revision(project_id, expected_revision, revision)
            baseline = self.template._projects.get(project_id)
            if baseline is not None and current is None:
                visible_count = sum(
                    self._current_project_locked(state, item) is not None
                    for item in (set(self.template._projects) | set(state.projects))
                )
                if visible_count >= self.max_projects_per_session:
                    raise DemoCapacityExceeded(
                        "maximum projects in one demo session reached"
                    )
            state.chat_history.pop(project_id, None)
            overlay = state.projects.get(project_id)
            if baseline is None:
                if overlay is not None and overlay.payload is not None:
                    state.projects[project_id] = _ProjectOverlay(
                        None, overlay.revision + 1
                    )
                return None
            if overlay is None:
                return DemoProject(project_id, 0, "template", _decode_json(baseline))
            next_revision = overlay.revision + 1
            state.projects[project_id] = _ProjectOverlay(baseline, next_revision)
            return DemoProject(
                project_id, next_revision, "session", _decode_json(baseline)
            )

    def _chat_messages(
        self, session_id: str, project_id: str
    ) -> list[dict[str, Any]]:
        project_id = _validate_project_id(project_id)
        with self._lock:
            state = self._state_locked(session_id)
            if self._current_project_locked(state, project_id) is None:
                raise UnknownDemoProject(project_id)
            messages = state.chat_history.get(project_id)
            if messages is None:
                messages = list(self.template._chat_history.get(project_id, ()))
            return [_decode_json(message) for message in messages]

    def _append_chat_message(
        self,
        session_id: str,
        project_id: str,
        role: str,
        text: str,
        *,
        metadata: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        project_id = _validate_project_id(project_id)
        if role not in _CHAT_ROLES:
            raise InvalidDemoData(f"role must be one of {sorted(_CHAT_ROLES)}")
        if not isinstance(text, str) or not text.strip():
            raise InvalidDemoData("chat text must be a non-empty string")
        if len(text.encode("utf-8")) > self.max_chat_message_bytes:
            raise DemoCapacityExceeded(
                f"chat text exceeds {self.max_chat_message_bytes} bytes"
            )
        encoded_metadata = _canonical_json(
            metadata or {},
            label="chat metadata",
            max_bytes=DEFAULT_MAX_CHAT_METADATA_BYTES,
            require_object=True,
        )
        detached_metadata = _decode_json(encoded_metadata)

        with self._lock:
            state = self._state_locked(session_id)
            if self._current_project_locked(state, project_id) is None:
                raise UnknownDemoProject(project_id)
            messages = state.chat_history.get(project_id)
            if messages is None:
                messages = list(self.template._chat_history.get(project_id, ()))
                state.chat_history[project_id] = messages
            if len(messages) >= self.max_chat_messages_per_project:
                raise DemoCapacityExceeded(
                    "maximum chat messages for one demo project reached"
                )
            existing_ids = set()
            for decoded in (_decode_json(message) for message in messages):
                if (
                    isinstance(decoded, dict)
                    and isinstance(decoded.get("id"), str)
                ):
                    existing_ids.add(decoded["id"])
            message_id = self._unique_id_locked(
                self._message_id_factory,
                existing=existing_ids,
                label="message_id",
            )
            record = {
                "id": message_id,
                "role": role,
                "text": text,
                "created_at": state.last_accessed_at,
                "sequence": len(messages) + 1,
                "metadata": detached_metadata,
            }
            payload = _canonical_json(
                record,
                label="chat message",
                max_bytes=(
                    self.max_chat_message_bytes + DEFAULT_MAX_CHAT_METADATA_BYTES + 1_024
                ),
                require_object=True,
            )
            messages.append(payload)
            return _decode_json(payload)

    def _clear_chat(self, session_id: str, project_id: str) -> None:
        project_id = _validate_project_id(project_id)
        with self._lock:
            state = self._state_locked(session_id)
            if self._current_project_locked(state, project_id) is None:
                raise UnknownDemoProject(project_id)
            state.chat_history[project_id] = []

    def _reset_session(self, session_id: str) -> None:
        with self._lock:
            state = self._state_locked(session_id)
            for project_id, overlay in tuple(state.projects.items()):
                baseline = self.template._projects.get(project_id)
                state.projects[project_id] = _ProjectOverlay(
                    baseline, overlay.revision + 1
                )
            state.chat_history.clear()

    def _session_snapshot(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            state = self._state_locked(session_id)
            project_ids = sorted(set(self.template._projects) | set(state.projects))
            projects: dict[str, Any] = {}
            chat_history: dict[str, list[Any]] = {}
            revisions: dict[str, int] = {}
            for project_id in project_ids:
                current = self._current_project_locked(state, project_id)
                if current is None:
                    continue
                payload, revision, _ = current
                projects[project_id] = _decode_json(payload)
                revisions[project_id] = revision
                messages = state.chat_history.get(project_id)
                if messages is None:
                    messages = list(self.template._chat_history.get(project_id, ()))
                chat_history[project_id] = [
                    _decode_json(message) for message in messages
                ]
            return {
                "session": {
                    "session_id": state.session_id,
                    "principal_id": state.principal_id,
                    "created_at": state.created_at,
                    "expires_at": state.expires_at,
                    "last_accessed_at": state.last_accessed_at,
                },
                "template_id": self.template.template_id,
                "template_digest": self.template.digest,
                "metadata": _decode_json(self.template._metadata),
                "projects": projects,
                "project_revisions": revisions,
                "chat_history": chat_history,
            }


__all__ = [
    "DemoCapacityExceeded",
    "DemoProject",
    "DemoProjectRepository",
    "DemoProjectSummary",
    "DemoRevisionConflict",
    "DemoSandbox",
    "DemoSandboxError",
    "DemoSession",
    "DemoSessionInfo",
    "DemoTemplate",
    "ExpiredDemoSession",
    "InvalidDemoData",
    "UnknownDemoProject",
    "UnknownDemoSession",
]
