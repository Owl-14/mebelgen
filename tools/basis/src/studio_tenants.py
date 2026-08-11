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
import shutil
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


_SAFE_ORGANIZATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SAFE_ARCHIVE_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


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
    def _archive_root(workspace: StudioWorkspace) -> Path:
        root = (
            workspace.spec_dir.parent / "archive"
            if workspace.organization_id is not None
            else workspace.spec_dir / ".archive"
        )
        root.mkdir(parents=True, exist_ok=True)
        return root

    @staticmethod
    def _delivery_slug(value: str) -> str:
        # Keep this byte-for-byte compatible with delivery._slug without
        # importing the delivery/viewer stack into tenant storage.
        keep: list[str] = []
        for character in str(value or "").lower():
            if character.isalnum():
                keep.append(character)
            elif character in " -_./\\":
                keep.append("_")
        slug = "".join(keep).strip("_")
        while "__" in slug:
            slug = slug.replace("__", "_")
        return slug or "model"

    @staticmethod
    def _archive_candidate(workspace: StudioWorkspace, spec_path: Path) -> Path:
        candidate = spec_path.resolve()
        if (
            candidate.parent != workspace.spec_dir.resolve()
            or candidate.suffix != ".json"
            or not candidate.is_file()
        ):
            raise FileNotFoundError("Изделие не найдено")
        return candidate

    @staticmethod
    def _copy_archive_item(source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)

    def archive_product(
        self,
        auth: Mapping[str, Any] | None,
        spec_path: Path,
        *,
        actor_user_id: str,
        actor_name: str,
        reason: str,
    ) -> dict[str, Any]:
        # Studio uses ThreadingHTTPServer.  Mutations of one filesystem-backed
        # catalog must not interleave with another archive/restore request.
        with self._lock:
            return self._archive_product_unlocked(
                auth,
                spec_path,
                actor_user_id=actor_user_id,
                actor_name=actor_name,
                reason=reason,
            )

    def _archive_product_unlocked(
        self,
        auth: Mapping[str, Any] | None,
        spec_path: Path,
        *,
        actor_user_id: str,
        actor_name: str,
        reason: str,
    ) -> dict[str, Any]:
        """Create a recoverable product bundle, then remove it from the active catalog.

        The bundle is completed before the active ParamSpec is removed.  Any copy
        or cleanup failure rolls missing active files back from that complete
        bundle, so a partial archive cannot destroy a product.
        """

        workspace = self.workspace(auth)
        candidate = self._archive_candidate(workspace, spec_path)
        try:
            spec = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as error:
            raise ValueError("Изделие не удалось прочитать") from error
        if not isinstance(spec, dict) or spec.get("schemaVersion") != "paramspec-v1":
            raise ValueError("Некорректное изделие")

        archive_id = str(uuid.uuid4())
        archive_root = self._archive_root(workspace)
        staging = archive_root / f".{archive_id}.staging"
        final = archive_root / archive_id
        if staging.exists() or final.exists():
            raise FileExistsError("Архив уже существует")

        entries: list[dict[str, Any]] = []

        def add(source: Path, *, root: str, relative: Path, remove: bool) -> None:
            if not source.exists():
                return
            archive_relative = Path("data") / root / relative
            entries.append(
                {
                    "archive_path": archive_relative.as_posix(),
                    "restore_root": root,
                    "restore_path": relative.as_posix(),
                    "remove_on_archive": remove,
                }
            )
            self._copy_archive_item(source, staging / archive_relative)

        stem = candidate.stem
        project_name = str(spec.get("project_name") or stem)
        catalog = _mapping(spec.get("catalog"))
        try:
            staging.mkdir(parents=True)
            add(candidate, root="spec", relative=Path(candidate.name), remove=True)
            add(
                candidate.with_suffix(".versions.json"),
                root="spec",
                relative=Path(candidate.with_suffix(".versions.json").name),
                remove=True,
            )
            add(
                workspace.spec_dir / ".previews" / f"{stem}.png",
                root="spec",
                relative=Path(".previews") / f"{stem}.png",
                remove=True,
            )
            add(
                workspace.spec_dir / ".history" / f"{stem}.ai.json",
                root="spec",
                relative=Path(".history") / f"{stem}.ai.json",
                remove=True,
            )
            for suffix in (".project.json", ".cfrn", ".b3d"):
                add(
                    workspace.out_dir / f"{stem}{suffix}",
                    root="out",
                    relative=Path(f"{stem}{suffix}"),
                    remove=False,
                )
            delivery = workspace.out_dir / "deliveries" / self._delivery_slug(project_name)
            add(
                delivery,
                root="out",
                relative=Path("deliveries") / delivery.name,
                remove=False,
            )
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            manifest = {
                "schema_version": 1,
                "id": archive_id,
                "state": "archived",
                "organization_id": workspace.organization_id,
                "project_file": candidate.name,
                "project_name": project_name,
                "draft": bool(spec.get("draft")),
                "archetype": str(spec.get("archetype") or ""),
                "furniture_type": str(spec.get("furniture_type") or ""),
                "dimensions": dict(_mapping(spec.get("dimensions"))),
                "materials": dict(_mapping(spec.get("materials"))),
                "creator_user_id": str(catalog.get("creator_user_id") or ""),
                "responsible_user_id": str(catalog.get("responsible_user_id") or ""),
                "author": str(catalog.get("author") or ""),
                "responsible": str(catalog.get("responsible") or ""),
                "archived_at": now,
                "archived_by_user_id": str(actor_user_id or ""),
                "archived_by": str(actor_name or "")[:160],
                "reason": str(reason or "")[:240],
                "revision": self._spec_revision(spec),
                "entries": entries,
            }
            (staging / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            staging.replace(final)

            # Companions go first; the ParamSpec disappears from the active
            # catalog only after its complete archive is already durable.
            removable = [
                entry for entry in entries if bool(entry.get("remove_on_archive"))
            ]
            removable.sort(key=lambda entry: entry["restore_path"] == candidate.name)
            for entry in removable:
                root = workspace.spec_dir if entry["restore_root"] == "spec" else workspace.out_dir
                source = (root / str(entry["restore_path"])).resolve()
                if source.exists():
                    source.unlink()
        except Exception:
            bundle = final if final.exists() else staging
            for entry in entries:
                if not bool(entry.get("remove_on_archive")):
                    continue
                root = workspace.spec_dir if entry["restore_root"] == "spec" else workspace.out_dir
                target = (root / str(entry["restore_path"])).resolve()
                archived = bundle / str(entry["archive_path"])
                if not target.exists() and archived.exists():
                    self._copy_archive_item(archived, target)
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            if final.exists():
                shutil.rmtree(final, ignore_errors=True)
            raise

        with self._lock:
            selected = self._current.get(self.session_key(auth))
            if selected is not None and selected.resolve() == candidate:
                self._current.pop(self.session_key(auth), None)
        return dict(manifest)

    def list_archived_products(
        self,
        auth: Mapping[str, Any] | None,
    ) -> list[dict[str, Any]]:
        workspace = self.workspace(auth)
        root = self._archive_root(workspace)
        result: list[dict[str, Any]] = []
        for path in sorted(root.iterdir()):
            if not path.is_dir() or not _SAFE_ARCHIVE_ID.fullmatch(path.name):
                continue
            try:
                manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                continue
            if not isinstance(manifest, dict) or manifest.get("state") != "archived":
                continue
            if manifest.get("organization_id") != workspace.organization_id:
                continue
            item = dict(manifest)
            item["preview"] = any(
                str(entry.get("restore_path") or "").endswith(".png")
                for entry in item.get("entries") or []
                if isinstance(entry, Mapping)
            )
            result.append(item)
        result.sort(key=lambda item: str(item.get("archived_at") or ""), reverse=True)
        return result

    def archived_preview_path(
        self,
        auth: Mapping[str, Any] | None,
        archive_id: str,
    ) -> Path:
        if not _SAFE_ARCHIVE_ID.fullmatch(str(archive_id or "")):
            raise FileNotFoundError("Превью не найдено")
        workspace = self.workspace(auth)
        entry = self._archive_root(workspace) / str(archive_id)
        try:
            manifest = json.loads((entry / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as error:
            raise FileNotFoundError("Превью не найдено") from error
        if (
            not isinstance(manifest, dict)
            or manifest.get("state") != "archived"
            or manifest.get("organization_id") != workspace.organization_id
        ):
            raise FileNotFoundError("Превью не найдено")
        for item in manifest.get("entries") or []:
            if not isinstance(item, Mapping):
                continue
            if str(item.get("restore_path") or "").endswith(".png"):
                candidate = (entry / str(item.get("archive_path") or "")).resolve()
                if candidate.is_file() and entry.resolve() in candidate.parents:
                    return candidate
        raise FileNotFoundError("Превью не найдено")

    def restore_product(
        self,
        auth: Mapping[str, Any] | None,
        archive_id: str,
        *,
        actor_user_id: str,
        actor_name: str,
    ) -> dict[str, Any]:
        with self._lock:
            return self._restore_product_unlocked(
                auth,
                archive_id,
                actor_user_id=actor_user_id,
                actor_name=actor_name,
            )

    def _restore_product_unlocked(
        self,
        auth: Mapping[str, Any] | None,
        archive_id: str,
        *,
        actor_user_id: str,
        actor_name: str,
    ) -> dict[str, Any]:
        """Restore one complete archive without overwriting an active product."""

        if not _SAFE_ARCHIVE_ID.fullmatch(str(archive_id or "")):
            raise FileNotFoundError("Архив не найден")
        workspace = self.workspace(auth)
        entry = self._archive_root(workspace) / str(archive_id)
        try:
            manifest = json.loads((entry / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as error:
            raise FileNotFoundError("Архив не найден") from error
        if (
            not isinstance(manifest, dict)
            or manifest.get("state") != "archived"
            or manifest.get("organization_id") != workspace.organization_id
        ):
            raise FileNotFoundError("Архив не найден")
        target_spec = (workspace.spec_dir / str(manifest.get("project_file") or "")).resolve()
        if target_spec.parent != workspace.spec_dir.resolve() or target_spec.suffix != ".json":
            raise ValueError("Некорректный archive manifest")
        if target_spec.exists():
            raise FileExistsError("Файл этого изделия уже занят")

        staged: list[tuple[Path, Path, bool]] = []
        try:
            for item in manifest.get("entries") or []:
                if not isinstance(item, Mapping):
                    raise ValueError("Некорректный archive manifest")
                restore_root = item.get("restore_root")
                if restore_root not in {"spec", "out"}:
                    raise ValueError("Некорректный archive manifest")
                root = workspace.spec_dir if restore_root == "spec" else workspace.out_dir
                target = (root / str(item.get("restore_path") or "")).resolve()
                if root.resolve() not in target.parents:
                    raise ValueError("Архив вышел за границу компании")
                source = (entry / str(item.get("archive_path") or "")).resolve()
                if entry.resolve() not in source.parents or not source.exists():
                    raise ValueError("Архив изделия неполон")
                # Production outputs remain in place while archived to preserve
                # builds.json links.  Restore only fills a missing output.
                if target.exists() and item.get("restore_root") == "out":
                    continue
                if target.exists():
                    raise FileExistsError("Данные изделия уже существуют")
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(f".{target.name}.{archive_id}.restore")
                self._copy_archive_item(source, temporary)
                staged.append((temporary, target, source.is_dir()))

            # ParamSpec becomes visible last, after every companion is ready.
            staged.sort(key=lambda pair: pair[1] == target_spec)
            for temporary, target, _is_dir in staged:
                temporary.replace(target)
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            manifest["state"] = "restored"
            manifest["restored_at"] = now
            manifest["restored_by_user_id"] = str(actor_user_id or "")
            manifest["restored_by"] = str(actor_name or "")[:160]
            temporary_manifest = entry / ".manifest.restore.tmp"
            temporary_manifest.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary_manifest.replace(entry / "manifest.json")
        except Exception:
            for temporary, target, is_dir in staged:
                if temporary.exists():
                    shutil.rmtree(temporary, ignore_errors=True) if is_dir else temporary.unlink()
                # If a later replace failed, remove only files restored by this
                # operation; the complete archived bundle still exists.
                if target.exists() and target != target_spec:
                    shutil.rmtree(target, ignore_errors=True) if target.is_dir() else target.unlink()
            if target_spec.exists():
                target_spec.unlink()
            raise
        return dict(manifest)

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
        trace_id: str | None = None,
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
        clean_trace_id = str(trace_id or "").lower()
        if len(clean_trace_id) != 32 or any(char not in "0123456789abcdef" for char in clean_trace_id):
            clean_trace_id = ""
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
            "trace_id": clean_trace_id,
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
