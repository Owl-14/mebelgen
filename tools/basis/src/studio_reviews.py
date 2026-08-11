"""Managed client-review links for Akeda Studio.

Possession of a high-entropy review token grants read-only access either to one
frozen product snapshot or to the latest saved revision of the same tenant
product.  Raw tokens are never stored: the SHA-256 digest is the lookup key.
The public review surface cannot change the source ParamSpec.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO, Mapping, Sequence


_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
_SCOPE_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_DECISIONS = {"approved", "changes_requested"}
_LINK_MODES = {"snapshot", "live"}
_EXPIRY_DAYS = {7, 30, 90}
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_REVIEW_ATTACHMENT_BYTES = 25 * 1024 * 1024
MAX_DECISION_ATTACHMENTS = 5
_ATTACHMENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_time(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _expired(record: Mapping[str, Any]) -> bool:
    expires_at = _parse_time(record.get("expires_at"))
    return expires_at is not None and expires_at <= datetime.now(timezone.utc)


def _copy_json(value: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(dict(value), ensure_ascii=False))


def _revision(spec: Mapping[str, Any]) -> str:
    payload = json.dumps(
        dict(spec), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _safe_filename(value: str) -> str:
    name = unicodedata.normalize("NFKC", str(value or "")).replace("\\", "/")
    name = name.rsplit("/", 1)[-1]
    name = "".join(
        ch
        for ch in name
        if not unicodedata.category(ch).startswith("C") and ch != '"'
    )
    return name.strip(" .")[:120] or "вложение"


def _detected_attachment_type(prefix: bytes) -> str | None:
    if prefix.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(prefix) >= 12 and prefix[:4] == b"RIFF" and prefix[8:12] == b"WEBP":
        return "image/webp"
    if prefix.startswith(b"%PDF-"):
        return "application/pdf"
    return None


class ReviewNotFound(FileNotFoundError):
    """The token is invalid, unknown or no longer active."""


class ReviewAttachmentError(ValueError):
    """A public review attachment failed validation or quota checks."""


class ReviewRevisionConflict(ValueError):
    """A live review changed after the client opened or started answering it."""


class StudioReviewStore:
    """Thread-safe filesystem store for snapshot and live review links."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    @staticmethod
    def token_hash(token: str) -> str:
        value = str(token or "")
        if not _TOKEN_RE.fullmatch(value):
            raise ReviewNotFound("Ссылка на согласование не найдена")
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _path(self, token: str) -> Path:
        return self.root / f"{self.token_hash(token)}.json"

    def _write(self, path: Path, record: Mapping[str, Any]) -> None:
        temporary = self.root / f".{path.stem}.{uuid.uuid4().hex}.tmp"
        temporary.write_text(
            json.dumps(dict(record), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(path)

    @staticmethod
    def _link_mode(record: Mapping[str, Any]) -> str:
        mode = str(record.get("link_mode") or "snapshot")
        return mode if mode in _LINK_MODES else "snapshot"

    @staticmethod
    def _decision_history(record: Mapping[str, Any]) -> list[dict[str, Any]]:
        history = [
            dict(item)
            for item in list(record.get("decision_history") or [])
            if isinstance(item, dict)
        ]
        legacy = record.get("decision")
        if not history and isinstance(legacy, dict):
            history.append(dict(legacy))
        return history

    def resolve_record(
        self,
        record: Mapping[str, Any],
        *,
        current_spec: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return the exact public revision and its decision state."""

        resolved = _copy_json(record)
        mode = self._link_mode(record)
        if mode == "live":
            if not isinstance(current_spec, Mapping):
                raise ReviewNotFound("Обновляемое изделие недоступно")
            spec = _copy_json(current_spec)
            revision = _revision(spec)
        else:
            stored = record.get("spec")
            if not isinstance(stored, Mapping):
                raise ReviewNotFound("Версия изделия недоступна")
            spec = _copy_json(stored)
            revision = str(record.get("revision") or _revision(spec))

        history = self._decision_history(record)
        matching = [
            item for item in history if str(item.get("revision") or "") == revision
        ]
        decision = matching[-1] if matching else None
        resolved.update({
            "link_mode": mode,
            "project_name": str(
                spec.get("project_name") or record.get("project_name") or "Изделие"
            ),
            "revision": revision,
            "spec": spec,
            "status": str((decision or {}).get("status") or "pending"),
            "decision": decision,
            "decision_history": history,
        })
        return resolved

    def _attachment_dir(self, record: Mapping[str, Any]) -> Path:
        organization_id = str(record.get("organization_id") or "local")
        review_id = str(record.get("id") or "")
        if not _SCOPE_RE.fullmatch(organization_id) or not _ID_RE.fullmatch(review_id):
            raise ReviewNotFound("Ссылка на согласование не найдена")
        directory = self.root / "attachments" / organization_id / review_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    @staticmethod
    def _public_attachment(attachment: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "id": str(attachment.get("id") or ""),
            "name": str(attachment.get("name") or "вложение"),
            "content_type": str(attachment.get("content_type") or "application/octet-stream"),
            "bytes": int(attachment.get("bytes") or 0),
            "sha256": str(attachment.get("sha256") or ""),
            "created_at": str(attachment.get("created_at") or ""),
        }

    def _attachment_from_record(
        self, record: Mapping[str, Any], attachment_id: str
    ) -> tuple[dict[str, Any], Path]:
        value = str(attachment_id or "")
        if not _ID_RE.fullmatch(value):
            raise ReviewNotFound("Вложение не найдено")
        attachment = next(
            (
                item
                for item in list(record.get("attachments") or [])
                if isinstance(item, dict) and str(item.get("id") or "") == value
            ),
            None,
        )
        if attachment is None:
            raise ReviewNotFound("Вложение не найдено")
        suffix = _ATTACHMENT_TYPES.get(str(attachment.get("content_type") or ""))
        if suffix is None:
            raise ReviewNotFound("Вложение не найдено")
        path = self._attachment_dir(record) / f"{value}{suffix}"
        if not path.is_file():
            raise ReviewNotFound("Вложение не найдено")
        return dict(attachment), path

    def create(
        self,
        spec: Mapping[str, Any],
        *,
        organization_id: str | None,
        organization_name: str,
        project_file: str,
        actor_user_id: str | None,
        actor_name: str,
        responsible_user_id: str | None = None,
        responsible_name: str = "",
        link_mode: str = "snapshot",
        expires_in_days: int | None = None,
    ) -> tuple[str, dict[str, Any]]:
        mode = str(link_mode or "snapshot")
        if mode not in _LINK_MODES:
            raise ValueError("Выберите тип ссылки")
        if expires_in_days is not None and expires_in_days not in _EXPIRY_DAYS:
            raise ValueError("Выберите срок действия ссылки")
        snapshot = _copy_json(spec)
        created_at = _utc_now()
        expires_at = (
            (datetime.now(timezone.utc) + timedelta(days=expires_in_days)).isoformat(
                timespec="seconds"
            )
            if expires_in_days is not None
            else None
        )
        record = {
            "schema_version": 2,
            "id": str(uuid.uuid4()),
            "organization_id": str(organization_id or ""),
            "organization_name": str(organization_name or ""),
            "project_file": str(project_file),
            "project_name": str(snapshot.get("project_name") or Path(project_file).stem),
            "revision": _revision(snapshot),
            "link_mode": mode,
            "created_at": created_at,
            "expires_at": expires_at,
            "created_by_user_id": str(actor_user_id or ""),
            "created_by_name": str(actor_name or ""),
            "responsible_user_id": str(responsible_user_id or actor_user_id or ""),
            "responsible_name": str(responsible_name or actor_name or ""),
            "status": "pending",
            "decision": None,
            "decision_history": [],
            "attachments": [],
            "revoked_at": None,
            "spec": snapshot,
        }
        with self._lock:
            while True:
                token = secrets.token_urlsafe(32)
                path = self._path(token)
                try:
                    with path.open("x", encoding="utf-8") as target:
                        json.dump(record, target, ensure_ascii=False, indent=2)
                    return token, _copy_json(record)
                except FileExistsError:
                    continue

    def load(self, token: str) -> dict[str, Any]:
        path = self._path(token)
        with self._lock:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError) as error:
                raise ReviewNotFound("Ссылка на согласование не найдена") from error
        if (
            not isinstance(record, dict)
            or record.get("revoked_at")
            or _expired(record)
        ):
            raise ReviewNotFound("Ссылка на согласование не найдена")
        return record

    def stage_attachment(
        self,
        token: str,
        *,
        filename: str,
        content_length: int,
        source: BinaryIO,
        expected_revision: str = "",
        current_spec: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if content_length <= 0:
            raise ReviewAttachmentError("Файл пустой")
        if content_length > MAX_ATTACHMENT_BYTES:
            raise ReviewAttachmentError("Один файл должен быть не больше 10 МБ")

        path = self._path(token)
        with self._lock:
            record = self.load(token)
            resolved = self.resolve_record(record, current_spec=current_spec)
            active_revision = str(resolved.get("revision") or "")
            submitted_revision = str(expected_revision or "")
            if self._link_mode(record) == "live" and not submitted_revision:
                raise ReviewRevisionConflict(
                    "Версия изделия не указана. Обновите страницу и повторите."
                )
            if submitted_revision and submitted_revision != active_revision:
                raise ReviewRevisionConflict(
                    "Изделие уже обновилось. Откройте актуальную версию и проверьте её заново."
                )
            attachments = [
                dict(item) for item in list(record.get("attachments") or [])
                if isinstance(item, dict)
            ]
            staged = [item for item in attachments if item.get("status") == "staged"]
            if len(staged) >= MAX_DECISION_ATTACHMENTS:
                raise ReviewAttachmentError("К одному решению можно приложить до 5 файлов")
            total_bytes = sum(int(item.get("bytes") or 0) for item in attachments)
            if total_bytes + content_length > MAX_REVIEW_ATTACHMENT_BYTES:
                raise ReviewAttachmentError("Общий размер вложений этой версии не должен превышать 25 МБ")

            attachment_id = str(uuid.uuid4())
            directory = self._attachment_dir(record)
            temporary = directory / f".{attachment_id}.upload"
            digest = hashlib.sha256()
            prefix = bytearray()
            remaining = content_length
            try:
                with temporary.open("xb") as target:
                    while remaining:
                        chunk = source.read(min(64 * 1024, remaining))
                        if not chunk:
                            raise ReviewAttachmentError("Файл передан не полностью")
                        remaining -= len(chunk)
                        if len(prefix) < 32:
                            prefix.extend(chunk[: 32 - len(prefix)])
                        digest.update(chunk)
                        target.write(chunk)
                detected_type = _detected_attachment_type(bytes(prefix))
                if detected_type is None:
                    raise ReviewAttachmentError(
                        "Поддерживаются JPG, PNG, WEBP и PDF"
                    )
                suffix = _ATTACHMENT_TYPES[detected_type]
                final_path = directory / f"{attachment_id}{suffix}"
                temporary.replace(final_path)
            except Exception:
                try:
                    temporary.unlink()
                except OSError:
                    pass
                raise

            attachment = {
                "id": attachment_id,
                "name": _safe_filename(filename),
                "content_type": detected_type,
                "bytes": content_length,
                "sha256": digest.hexdigest(),
                "created_at": _utc_now(),
                "revision": active_revision,
                "status": "staged",
                "decision_id": None,
            }
            attachments.append(attachment)
            record["attachments"] = attachments
            self._write(path, record)
            return self._public_attachment(attachment)

    def attachment_for_token(
        self, token: str, attachment_id: str
    ) -> tuple[dict[str, Any], Path]:
        with self._lock:
            record = self.load(token)
            attachment, path = self._attachment_from_record(record, attachment_id)
            if attachment.get("status") != "attached":
                raise ReviewNotFound("Вложение не найдено")
            return attachment, path

    def list_for_project(
        self,
        *,
        organization_id: str | None,
        project_file: str,
        current_spec: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        expected_organization = str(organization_id or "")
        expected_file = str(project_file or "")
        records: list[dict[str, Any]] = []
        with self._lock:
            for path in self.root.glob("*.json"):
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError, TypeError):
                    continue
                if not isinstance(record, dict):
                    continue
                if str(record.get("organization_id") or "") != expected_organization:
                    continue
                if str(record.get("project_file") or "") != expected_file:
                    continue
                history = []
                for event in self._decision_history(record):
                    if not isinstance(event, dict):
                        continue
                    public_event = dict(event)
                    public_event["attachments"] = [
                        self._public_attachment(item)
                        for item in list(event.get("attachments") or [])
                        if isinstance(item, dict)
                    ]
                    history.append(public_event)
                mode = self._link_mode(record)
                try:
                    resolved = self.resolve_record(
                        record,
                        current_spec=current_spec if mode == "live" else None,
                    )
                    active_revision = str(resolved.get("revision") or "")
                    current_decision = resolved.get("decision")
                    current_status = str(resolved.get("status") or "pending")
                except ReviewNotFound:
                    active_revision = str(record.get("revision") or "")
                    current_decision = None
                    current_status = "pending"
                access_status = (
                    "revoked" if record.get("revoked_at")
                    else "expired" if _expired(record)
                    else "active"
                )
                records.append({
                    "id": str(record.get("id") or ""),
                    "project_name": str(record.get("project_name") or "Изделие"),
                    "project_file": str(record.get("project_file") or ""),
                    "revision": active_revision,
                    "initial_revision": str(record.get("revision") or ""),
                    "link_mode": mode,
                    "created_at": str(record.get("created_at") or ""),
                    "expires_at": str(record.get("expires_at") or ""),
                    "revoked_at": str(record.get("revoked_at") or ""),
                    "access_status": access_status,
                    "created_by_user_id": str(record.get("created_by_user_id") or ""),
                    "created_by_name": str(record.get("created_by_name") or ""),
                    "responsible_user_id": str(record.get("responsible_user_id") or ""),
                    "responsible_name": str(record.get("responsible_name") or ""),
                    "status": current_status,
                    "decision": current_decision,
                    "decision_history": history,
                })
        return sorted(records, key=lambda item: item["created_at"], reverse=True)

    def attachment_for_project(
        self,
        *,
        review_id: str,
        attachment_id: str,
        organization_id: str | None,
        project_file: str,
    ) -> tuple[dict[str, Any], Path]:
        value = str(review_id or "")
        if not _ID_RE.fullmatch(value):
            raise ReviewNotFound("Вложение не найдено")
        expected_organization = str(organization_id or "")
        expected_file = str(project_file or "")
        with self._lock:
            for path in self.root.glob("*.json"):
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError, TypeError):
                    continue
                if not isinstance(record, dict) or str(record.get("id") or "") != value:
                    continue
                if str(record.get("organization_id") or "") != expected_organization:
                    break
                if str(record.get("project_file") or "") != expected_file:
                    break
                attachment, attachment_path = self._attachment_from_record(
                    record, attachment_id
                )
                if attachment.get("status") != "attached":
                    break
                return attachment, attachment_path
        raise ReviewNotFound("Вложение не найдено")

    def decide(
        self,
        token: str,
        *,
        decision: str,
        reviewer_name: str,
        comment: str = "",
        attachment_ids: Sequence[str] | None = None,
        expected_revision: str = "",
        current_spec: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        status = str(decision or "")
        if status not in _DECISIONS:
            raise ValueError("Выберите результат согласования")
        name = str(reviewer_name or "").strip()[:120]
        note = str(comment or "").strip()[:2000]
        if not name:
            raise ValueError("Укажите имя согласующего")
        if status == "changes_requested" and not note:
            raise ValueError("Опишите, что нужно изменить")
        selected_ids = [str(value or "") for value in list(attachment_ids or [])]
        if len(selected_ids) > MAX_DECISION_ATTACHMENTS:
            raise ValueError("К одному решению можно приложить до 5 файлов")
        if len(selected_ids) != len(set(selected_ids)):
            raise ValueError("Один файл выбран несколько раз")

        path = self._path(token)
        with self._lock:
            record = self.load(token)
            resolved = self.resolve_record(record, current_spec=current_spec)
            active_revision = str(resolved.get("revision") or "")
            submitted_revision = str(expected_revision or "")
            if self._link_mode(record) == "live" and not submitted_revision:
                raise ReviewRevisionConflict(
                    "Версия изделия не указана. Обновите страницу и повторите."
                )
            if submitted_revision and submitted_revision != active_revision:
                raise ReviewRevisionConflict(
                    "Изделие уже обновилось. Откройте актуальную версию и проверьте её заново."
                )
            all_attachments = [
                dict(item) for item in list(record.get("attachments") or [])
                if isinstance(item, dict)
            ]
            selected: list[dict[str, Any]] = []
            for attachment_id in selected_ids:
                attachment = next(
                    (
                        item for item in all_attachments
                        if str(item.get("id") or "") == attachment_id
                        and item.get("status") == "staged"
                        and str(item.get("revision") or active_revision) == active_revision
                    ),
                    None,
                )
                if attachment is None:
                    raise ValueError("Одно из вложений недоступно")
                selected.append(attachment)
            event = {
                "id": str(uuid.uuid4()),
                "status": status,
                "reviewer_name": name,
                "comment": note,
                "created_at": _utc_now(),
                "revision": active_revision,
                "attachments": [self._public_attachment(item) for item in selected],
            }
            for item in selected:
                item["status"] = "attached"
                item["decision_id"] = event["id"]
            history = list(record.get("decision_history") or [])
            history.append(event)
            record["status"] = status
            record["decision"] = event
            record["decision_history"] = history[-50:]
            record["attachments"] = all_attachments
            self._write(path, record)
            return self.resolve_record(record, current_spec=current_spec)

    def revoke(
        self,
        *,
        review_id: str,
        organization_id: str | None,
        project_file: str,
    ) -> dict[str, Any]:
        value = str(review_id or "")
        if not _ID_RE.fullmatch(value):
            raise ReviewNotFound("Ссылка на согласование не найдена")
        expected_organization = str(organization_id or "")
        expected_file = str(project_file or "")
        with self._lock:
            for path in self.root.glob("*.json"):
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError, TypeError):
                    continue
                if not isinstance(record, dict) or str(record.get("id") or "") != value:
                    continue
                if str(record.get("organization_id") or "") != expected_organization:
                    break
                if str(record.get("project_file") or "") != expected_file:
                    break
                if not record.get("revoked_at"):
                    record["revoked_at"] = _utc_now()
                    self._write(path, record)
                return _copy_json(record)
        raise ReviewNotFound("Ссылка на согласование не найдена")


__all__ = [
    "MAX_ATTACHMENT_BYTES",
    "MAX_DECISION_ATTACHMENTS",
    "ReviewAttachmentError",
    "ReviewNotFound",
    "ReviewRevisionConflict",
    "StudioReviewStore",
]
