"""Tenant-scoped filesystem workspaces for Akeda Studio.

The existing Studio persists ParamSpec files, previews and production outputs on
disk.  This module keeps that storage model for the first multi-tenant release,
but makes the organization boundary explicit and keeps the selected ParamSpec
per authenticated session instead of in process-global state.

No geometry or viewer concerns belong here.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


_SAFE_ORGANIZATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True)
class StudioWorkspace:
    """Resolved filesystem roots for one request authority."""

    organization_id: str | None
    spec_dir: Path
    out_dir: Path
    mode: str = "standard"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _valid_specs(spec_dir: Path) -> list[Path]:
    result: list[Path] = []
    for path in sorted(spec_dir.glob("*.json")):
        if path.name.endswith((".project.json", ".versions.json")):
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(value, dict) and value.get("schemaVersion") == "paramspec-v1":
            result.append(path)
    return result


class TenantWorkspaceManager:
    """Resolve tenant roots and remember the active ParamSpec per session."""

    def __init__(
        self,
        legacy_spec_path: Path,
        legacy_out_dir: Path,
        *,
        tenant_root: Path | None = None,
    ) -> None:
        self.legacy_spec_path = legacy_spec_path.resolve()
        self.legacy_spec_dir = self.legacy_spec_path.parent
        self.legacy_out_dir = legacy_out_dir.resolve()
        self.tenant_root = tenant_root.resolve() if tenant_root is not None else None
        self._current: dict[str, Path] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _context(auth: Mapping[str, Any] | None) -> Mapping[str, Any]:
        return _mapping(_mapping(auth).get("context"))

    @classmethod
    def organization_id(cls, auth: Mapping[str, Any] | None) -> str | None:
        organization = _mapping(cls._context(auth).get("organization"))
        raw = organization.get("id")
        if raw is None:
            return None
        value = str(raw)
        if not _SAFE_ORGANIZATION_ID.fullmatch(value):
            raise ValueError("Некорректный идентификатор компании")
        return value

    @classmethod
    def session_key(cls, auth: Mapping[str, Any] | None) -> str:
        session = _mapping(cls._context(auth).get("session"))
        raw = session.get("id")
        if raw:
            return f"session:{raw}"
        organization_id = cls.organization_id(auth)
        return f"organization:{organization_id}" if organization_id else "legacy"

    def workspace(self, auth: Mapping[str, Any] | None) -> StudioWorkspace:
        organization_id = self.organization_id(auth)
        if self.tenant_root is None or organization_id is None:
            return StudioWorkspace(None, self.legacy_spec_dir, self.legacy_out_dir)

        organization = _mapping(self._context(auth).get("organization"))
        mode = str(organization.get("mode") or "standard")
        root = (self.tenant_root / organization_id).resolve()
        if root.parent != self.tenant_root:
            raise ValueError("Каталог компании вышел за tenant-root")
        spec_dir = root / "paramspecs"
        out_dir = root / "out"
        spec_dir.mkdir(parents=True, exist_ok=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        return StudioWorkspace(organization_id, spec_dir, out_dir, mode)

    @staticmethod
    def _blank_spec() -> dict[str, Any]:
        return {
            "schemaVersion": "paramspec-v1",
            "draft": True,
            "project_name": "Новое изделие",
        }

    def current_spec_path(self, auth: Mapping[str, Any] | None) -> Path:
        workspace = self.workspace(auth)
        key = self.session_key(auth)
        with self._lock:
            selected = self._current.get(key)
            if selected is not None:
                selected = selected.resolve()
                if selected.parent == workspace.spec_dir.resolve() and selected.is_file():
                    return selected

            candidates = _valid_specs(workspace.spec_dir)
            if candidates:
                selected = candidates[0].resolve()
            elif workspace.organization_id is None:
                selected = self.legacy_spec_path
            else:
                selected = (workspace.spec_dir / "new_product.json").resolve()
                if not selected.exists():
                    selected.write_text(
                        json.dumps(self._blank_spec(), ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
            self._current[key] = selected
            return selected

    def select(self, auth: Mapping[str, Any] | None, file_name: str) -> Path:
        workspace = self.workspace(auth)
        raw = str(file_name or "")
        if not raw or Path(raw).name != raw or "/" in raw or "\\" in raw:
            raise FileNotFoundError("Изделие не найдено")
        candidate = (workspace.spec_dir / raw).resolve()
        if candidate.parent != workspace.spec_dir.resolve() or candidate.suffix != ".json":
            raise FileNotFoundError("Изделие не найдено")
        if not candidate.is_file():
            raise FileNotFoundError("Изделие не найдено")
        with self._lock:
            self._current[self.session_key(auth)] = candidate
        return candidate

    def set_current(self, auth: Mapping[str, Any] | None, path: Path) -> Path:
        workspace = self.workspace(auth)
        candidate = path.resolve()
        if candidate.parent != workspace.spec_dir.resolve() or candidate.suffix != ".json":
            raise ValueError("Файл вне каталога компании")
        with self._lock:
            self._current[self.session_key(auth)] = candidate
        return candidate

    def clear_session(self, auth: Mapping[str, Any] | None) -> None:
        with self._lock:
            self._current.pop(self.session_key(auth), None)

    @staticmethod
    def _spec_revision(spec: Mapping[str, Any] | None) -> str:
        encoded = json.dumps(
            dict(spec or {}),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:20]

    def _history_file(
        self,
        auth: Mapping[str, Any] | None,
        spec_path: Path,
    ) -> Path:
        workspace = self.workspace(auth)
        candidate = spec_path.resolve()
        if candidate.parent != workspace.spec_dir.resolve() or candidate.suffix != ".json":
            raise ValueError("История относится к файлу вне каталога компании")
        history_dir = workspace.spec_dir / ".history"
        history_dir.mkdir(parents=True, exist_ok=True)
        return history_dir / f"{candidate.stem}.ai.json"

    def read_ai_history(
        self,
        auth: Mapping[str, Any] | None,
        spec_path: Path,
        *,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        """Return recent server-owned AI operations for the selected product."""

        if self.organization_id(auth) is None:
            return []
        history_file = self._history_file(auth, spec_path)
        with self._lock:
            try:
                raw = json.loads(history_file.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                raw = []
        if not isinstance(raw, list):
            return []
        safe_limit = max(1, min(int(limit), 200))
        return [dict(item) for item in raw[-safe_limit:] if isinstance(item, Mapping)]

    def append_ai_history(
        self,
        auth: Mapping[str, Any] | None,
        spec_path: Path,
        *,
        message: str,
        reply: str,
        before_spec: Mapping[str, Any] | None,
        after_spec: Mapping[str, Any] | None,
        changes: list[Any] | None = None,
        provider: str | None = None,
        usage: Mapping[str, Any] | None = None,
        context: Mapping[str, Any] | None = None,
        image_count: int = 0,
        keep: int = 200,
    ) -> dict[str, Any]:
        """Persist a compact AI operation without trusting a browser tenant id."""

        organization_id = self.organization_id(auth)
        if organization_id is None:
            raise ValueError("AI-история требует контекст компании")
        auth_context = self._context(auth)
        user = _mapping(auth_context.get("user"))
        selected_part = _mapping(_mapping(context).get("selected_part"))
        clean_context: dict[str, Any] = {"scope": "model"}
        if selected_part.get("name"):
            clean_context = {
                "scope": "part",
                "part_name": str(selected_part.get("name"))[:240],
                "part_type": str(selected_part.get("type") or "")[:120],
            }
        clean_usage: dict[str, int] = {}
        for key in ("prompt", "completion", "total"):
            try:
                value = int(_mapping(usage).get(key) or 0)
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                clean_usage[key] = value
        entry = {
            "id": str(uuid.uuid4()),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "organization_id": organization_id,
            "actor_user_id": str(user.get("id") or ""),
            "project_file": spec_path.name,
            "before_revision": self._spec_revision(before_spec),
            "after_revision": self._spec_revision(after_spec or before_spec),
            "message": str(message or "")[:12000],
            "reply": str(reply or "")[:24000],
            "changes": [str(item)[:2000] for item in (changes or [])[:100]],
            "provider": str(provider or "")[:120],
            "usage": clean_usage,
            "context": clean_context,
            "image_count": max(0, min(int(image_count or 0), 20)),
            "changed": bool(after_spec),
        }
        history_file = self._history_file(auth, spec_path)
        safe_keep = max(1, min(int(keep), 1000))
        with self._lock:
            try:
                raw = json.loads(history_file.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                raw = []
            history = raw if isinstance(raw, list) else []
            history.append(entry)
            temporary = history_file.with_suffix(history_file.suffix + ".tmp")
            temporary.write_text(
                json.dumps(history[-safe_keep:], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(history_file)
        return dict(entry)

    def ai_messages(
        self,
        auth: Mapping[str, Any] | None,
        spec_path: Path,
        *,
        limit: int = 16,
    ) -> list[dict[str, str]]:
        """Flatten persisted operations into the short context expected by providers."""

        operations = self.read_ai_history(auth, spec_path, limit=max(1, limit // 2))
        messages: list[dict[str, str]] = []
        for operation in operations:
            message = str(operation.get("message") or "").strip()
            reply = str(operation.get("reply") or "").strip()
            if message:
                messages.append({"role": "user", "text": message})
            if reply:
                messages.append({"role": "assistant", "text": reply})
        return messages[-max(1, min(int(limit), 64)):]


__all__ = ["StudioWorkspace", "TenantWorkspaceManager"]
