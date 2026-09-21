"""Журнал AI-вызовов Studio: ТЗ, ответы нейросетей и ошибки.

База для починки движка и дообучения. Одна строка ``ai_calls`` на каждый вызов
``chat_edit`` (чат-правка или импорт ТЗ): провайдер, модель, узел, версия
промпта, расход, исход и код ошибки. Содержимое — сообщение, ответ модели как
есть, спеки до/после, отчёт производственного гейта — пишется при
``AI_JOURNAL_STORE_CONTENT=1`` (по умолчанию). Фото/сканы ТЗ лежат файлами рядом
с БД, в каталоге компании.

В отличие от telemetry.py (трейсы наружу, без контента) журнал живёт только на
своём сервере. Запись никогда не роняет пользовательский запрос: любая ошибка
журнала — warning в лог.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import uuid
import zipfile
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

LOGGER = logging.getLogger("akeda.studio.ai_journal")

_MAX_TEXT = 200_000
_SCOPE_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SECRET_RES = (
    (re.compile(r"(?i)([?&](?:key|api_key|token)=)[^&\s'\"]+"), r"\1***"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+"), r"\1***"),
    (re.compile(r"sk-[A-Za-z0-9_\-]{8,}"), "sk-***"),
)
_IMAGE_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
              "image/gif": ".gif", "application/pdf": ".pdf"}

# Код ошибки → кто её чинит (и годится ли она для обучения).
ERROR_CLASSES = {
    "ai_provider_failed": "infrastructure",
    "invalid_provider_response": "infrastructure",
    "import_tz_internal_error": "infrastructure",
    "production_gate_rejected": "production_gate",
    "create_paramspec_missing": "contract",
    "create_paramspec_failed": "contract",
    "operation_validation_failed": "contract",
    "llm_coordinates_forbidden": "contract",
    "part_edit_contract_violation": "contract",
    "part_edit_operation_forbidden": "contract",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_calls (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    organization_id TEXT NOT NULL DEFAULT '',
    actor_user_id TEXT NOT NULL DEFAULT '',
    workflow TEXT NOT NULL,
    project_file TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    node TEXT NOT NULL DEFAULT '',
    prompt_version TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL,
    error_code TEXT NOT NULL DEFAULT '',
    error_class TEXT NOT NULL DEFAULT '',
    fingerprint TEXT NOT NULL DEFAULT '',
    tokens_prompt INTEGER,
    tokens_completion INTEGER,
    tokens_total INTEGER,
    latency_ms INTEGER,
    image_count INTEGER NOT NULL DEFAULT 0,
    trace_id TEXT NOT NULL DEFAULT '',
    exception TEXT,
    message TEXT,
    reply TEXT,
    raw_response TEXT,
    vision_facts TEXT,
    before_spec TEXT,
    after_spec TEXT,
    check_report TEXT,
    review_status TEXT NOT NULL DEFAULT 'new'
);
CREATE INDEX IF NOT EXISTS ai_calls_created ON ai_calls(created_at);
CREATE INDEX IF NOT EXISTS ai_calls_fingerprint ON ai_calls(fingerprint);
CREATE TABLE IF NOT EXISTS tz_intake (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    call_id TEXT NOT NULL,
    organization_id TEXT NOT NULL DEFAULT '',
    actor_user_id TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    mime TEXT NOT NULL DEFAULT '',
    bytes INTEGER NOT NULL DEFAULT 0,
    sha256 TEXT NOT NULL DEFAULT '',
    file TEXT
);
CREATE INDEX IF NOT EXISTS tz_intake_call ON tz_intake(call_id);
CREATE TABLE IF NOT EXISTS journal_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    # микросекунды: отметка «выгружено до» не должна съедать записи той же секунды
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _truthy(value: str | None, default: bool) -> bool:
    if value is None or not value.strip():
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def redact(text: Any, limit: int = 2000) -> str:
    """Убрать ключи и токены из текста исключения (URL Gemini несёт ?key=...)."""
    value = str(text or "")
    for pattern, replacement in _SECRET_RES:
        value = pattern.sub(replacement, value)
    return value[:limit]


def parse_since(value: str | None, *, now: datetime | None = None) -> str:
    """«7d» / «24h» / «30m» / ISO-дата → ISO UTC; пусто → ''."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    match = re.fullmatch(r"(\d+)\s*([dhm])", raw.casefold())
    if match:
        amount, unit = int(match.group(1)), match.group(2)
        delta = {"d": timedelta(days=amount), "h": timedelta(hours=amount),
                 "m": timedelta(minutes=amount)}[unit]
        return _iso((now or _now()) - delta)
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return _iso(parsed)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)[:_MAX_TEXT]


def _json_text(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)[:_MAX_TEXT * 5]
    except (TypeError, ValueError):
        return None


def _json_value(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except ValueError:
        return value


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def fingerprint(error_code: str, node: str, check_report: Mapping[str, Any] | None) -> str:
    """Одинаковые по сути ошибки → один отпечаток (для «новая ошибка, 14 случаев»)."""
    if not error_code:
        return ""
    codes = sorted({
        str(item.get("code"))
        for item in (check_report or {}).get("errors") or []
        if isinstance(item, Mapping) and item.get("code")
    })
    raw = f"{error_code}|{node}|{','.join(codes)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class AIJournal:
    """SQLite-журнал (WAL, соединение на операцию — как identity/cutting_ledger)."""

    def __init__(self, path: str | Path, *, files_dir: str | Path | None = None,
                 store_content: bool | None = None) -> None:
        self.path = Path(path)
        self.files_dir = Path(files_dir) if files_dir else self.path.parent / "ai_journal_files"
        self.store_content = (
            _truthy(os.environ.get("AI_JOURNAL_STORE_CONTENT"), True)
            if store_content is None else bool(store_content)
        )
        self._schema_lock = threading.Lock()
        self._schema_ready = False

    @classmethod
    def for_studio(cls, out_dir: Path, identity_db: Path | None) -> "AIJournal":
        """AI_JOURNAL_DB, иначе рядом с identity DB (/opt/bazis/data), иначе в out."""
        configured = os.environ.get("AI_JOURNAL_DB", "").strip()
        if configured:
            return cls(Path(configured))
        base = Path(identity_db).parent if identity_db else Path(out_dir)
        return cls(base / "ai_journal.sqlite3")

    # ------------------------------------------------------------ storage

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        with self._schema_lock:
            if not self._schema_ready:
                connection.executescript(_SCHEMA)
                self._schema_ready = True
        return connection

    def _state(self, connection: sqlite3.Connection, key: str) -> str:
        row = connection.execute(
            "SELECT value FROM journal_state WHERE key = ?", (key,)
        ).fetchone()
        return str(row["value"]) if row else ""

    def _set_state(self, connection: sqlite3.Connection, key: str, value: str) -> None:
        connection.execute(
            "INSERT INTO journal_state(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # ------------------------------------------------------------ writes

    def save_tz_images(
        self,
        call_id: str,
        images: Iterable[Mapping[str, Any]],
        *,
        source: str,
        organization_id: str = "",
        actor_user_id: str = "",
    ) -> list[str]:
        """Сохранить фото/сканы ТЗ, пришедшие в запросе. Никогда не бросает."""
        saved: list[str] = []
        try:
            scope = organization_id if _SCOPE_RE.fullmatch(organization_id or "") else "_public"
            month = _now().strftime("%Y-%m")
            with closing(self.connect()) as connection:
                for image in images:
                    if not isinstance(image, Mapping):
                        continue
                    try:
                        raw = base64.b64decode(str(image.get("data") or ""), validate=False)
                    except (binascii.Error, ValueError):
                        raw = b""
                    mime = str(image.get("mime") or "application/octet-stream")[:80]
                    intake_id = str(uuid.uuid4())
                    relative = None
                    if self.store_content and raw:
                        relative = f"{scope}/{month}/{intake_id}{_IMAGE_EXT.get(mime, '.bin')}"
                        target = self.files_dir / relative
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(raw)
                    connection.execute(
                        "INSERT INTO tz_intake(id, created_at, call_id, organization_id, "
                        "actor_user_id, source, name, mime, bytes, sha256, file) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (intake_id, _iso(_now()), call_id, organization_id or "",
                         actor_user_id or "", source, str(image.get("name") or "")[:240],
                         mime, len(raw), hashlib.sha256(raw).hexdigest(), relative),
                    )
                    saved.append(intake_id)
        except Exception as error:  # noqa: BLE001 - журнал не роняет запрос
            LOGGER.warning("ai_journal_tz_failed error=%s", redact(error, 300))
        return saved

    def record_call(
        self,
        *,
        call_id: str | None = None,
        workflow: str,
        result: Mapping[str, Any] | None,
        capture: Mapping[str, Any] | None = None,
        organization_id: str = "",
        actor_user_id: str = "",
        project_file: str = "",
        message: str = "",
        before_spec: Any = None,
        image_count: int = 0,
        trace_id: str = "",
        latency_ms: int | None = None,
        error_code: str | None = None,
        exception: BaseException | str | None = None,
    ) -> str | None:
        """Записать один AI-вызов. Возвращает id или None, если журнал недоступен."""
        try:
            res = dict(result or {})
            cap = dict(capture or {})
            code = str(error_code or (res.get("code") if res.get("error") else "") or "")
            node = str(cap.get("node") or (res.get("trace") or {}).get("router", {}).get("node") or "")
            report = res.get("check_report") if isinstance(res.get("check_report"), Mapping) else None
            usage = res.get("usage") if isinstance(res.get("usage"), Mapping) else {}
            exception_text = cap.get("exception") or exception
            content = self.store_content
            row_id = call_id or str(uuid.uuid4())
            after_spec = res.get("spec") if isinstance(res.get("spec"), Mapping) else None
            with closing(self.connect()) as connection:
                connection.execute(
                    "INSERT INTO ai_calls(id, created_at, organization_id, actor_user_id, workflow, "
                    "project_file, provider, model, node, prompt_version, outcome, error_code, "
                    "error_class, fingerprint, tokens_prompt, tokens_completion, tokens_total, "
                    "latency_ms, image_count, trace_id, exception, message, reply, raw_response, "
                    "vision_facts, before_spec, after_spec, check_report) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        row_id, _iso(_now()), organization_id or "", actor_user_id or "",
                        workflow, project_file or "",
                        str(cap.get("provider") or "")[:120],
                        str(usage.get("model") or cap.get("model") or "")[:120],
                        node[:120], str(cap.get("prompt_version") or "")[:80],
                        "error" if code else "ok", code[:120],
                        (ERROR_CLASSES.get(code, "other") if code else ""),
                        fingerprint(code, node, report),
                        _int(usage.get("prompt")), _int(usage.get("completion")),
                        _int(usage.get("total")), _int(latency_ms),
                        max(0, int(image_count or 0)), str(trace_id or "")[:64],
                        redact(exception_text) if exception_text else None,
                        _text(message) if content else None,
                        _text(res.get("reply")) if content else None,
                        _text(cap.get("raw_response")) if content else None,
                        _text(cap.get("vision_facts")) if content else None,
                        _json_text(before_spec) if content else None,
                        _json_text(after_spec) if content else None,
                        _json_text(report) if content else None,
                    ),
                )
            return row_id
        except Exception as error:  # noqa: BLE001 - журнал не роняет запрос
            LOGGER.warning("ai_journal_record_failed workflow=%s error=%s",
                           workflow, redact(error, 300))
            return None

    # ------------------------------------------------------------ reads

    def summary(self, since: str = "") -> dict[str, Any]:
        """Сводка для бота: вызовы, ошибки, расход, топ ошибок (новые помечены)."""
        since_iso = parse_since(since)
        with closing(self.connect()) as connection:
            where, params = ("WHERE created_at >= ?", (since_iso,)) if since_iso else ("", ())
            totals = connection.execute(
                f"SELECT COUNT(*) AS calls, SUM(outcome = 'error') AS errors, "
                f"SUM(COALESCE(tokens_total, 0)) AS tokens FROM ai_calls {where}", params,
            ).fetchone()
            tz_count = connection.execute(
                f"SELECT COUNT(*) FROM ai_calls {where}{' AND' if where else 'WHERE'} "
                "(workflow = 'import_tz' OR node = 'create_paramspec' OR image_count > 0)",
                params,
            ).fetchone()[0]
            providers = connection.execute(
                f"SELECT provider, model, COUNT(*) AS calls, SUM(outcome = 'error') AS errors, "
                f"SUM(COALESCE(tokens_total, 0)) AS tokens FROM ai_calls {where} "
                "GROUP BY provider, model ORDER BY calls DESC", params,
            ).fetchall()
            errors = connection.execute(
                f"SELECT fingerprint, error_code, error_class, node, COUNT(*) AS count, "
                f"MAX(created_at) AS last_at, "
                f"(SELECT MIN(created_at) FROM ai_calls AS first "
                f" WHERE first.fingerprint = ai_calls.fingerprint) AS first_at "
                f"FROM ai_calls {where}{' AND' if where else 'WHERE'} outcome = 'error' "
                "GROUP BY fingerprint ORDER BY count DESC LIMIT 10", params,
            ).fetchall()
        return {
            "since": since_iso,
            "calls": int(totals["calls"] or 0),
            "errors": int(totals["errors"] or 0),
            "tokens_total": int(totals["tokens"] or 0),
            "tz": int(tz_count or 0),
            "by_provider": [dict(row) for row in providers],
            "top_errors": [
                {**dict(row), "new": bool(since_iso and row["first_at"] >= since_iso)}
                for row in errors
            ],
        }

    def new_error_types(self, since: str) -> list[dict[str, Any]]:
        """Отпечатки ошибок, впервые появившиеся после since (для алерта «новая ошибка»)."""
        with closing(self.connect()) as connection:
            rows = connection.execute(
                "SELECT fingerprint, error_code, error_class, node, COUNT(*) AS count, "
                "MIN(created_at) AS first_at FROM ai_calls "
                "WHERE outcome = 'error' AND fingerprint != '' GROUP BY fingerprint "
                "HAVING MIN(created_at) > ? ORDER BY first_at", (since,),
            ).fetchall()
        return [dict(row) for row in rows]

    def count_errors(self, codes: Iterable[str], since: str) -> int:
        codes = list(codes)
        if not codes:
            return 0
        marks = ", ".join("?" for _ in codes)
        with closing(self.connect()) as connection:
            row = connection.execute(
                f"SELECT COUNT(*) FROM ai_calls WHERE error_code IN ({marks}) "
                "AND created_at > ?", (*codes, since),
            ).fetchone()
        return int(row[0] or 0)

    def mark_export(self, until: str) -> None:
        """Сдвинуть отметку «выгружено до» — после того как архив реально доставлен."""
        with closing(self.connect()) as connection:
            self._set_state(connection, "last_export_at", until)

    def export_zip(
        self,
        destination: str | Path,
        *,
        since: str | None = None,
        mark: bool = True,
    ) -> dict[str, Any]:
        """ZIP для разбора и дообучения: tz.jsonl, errors.jsonl, pairs.jsonl, images/.

        since=None — всё новое с прошлой выгрузки; '' — вся база; «7d»/ISO — период.
        mark=True запоминает момент выгрузки для следующего since=None.
        """
        destination = Path(destination)
        until = _iso(_now())
        with closing(self.connect()) as connection:
            since_iso = (self._state(connection, "last_export_at") if since is None
                         else parse_since(since))
            params = (since_iso, until)
            calls = connection.execute(
                "SELECT * FROM ai_calls WHERE created_at > ? AND created_at <= ? "
                "ORDER BY created_at", params,
            ).fetchall()
            intake = connection.execute(
                "SELECT * FROM tz_intake WHERE created_at > ? AND created_at <= ? "
                "ORDER BY created_at", params,
            ).fetchall()

            images_by_call: dict[str, list[dict[str, Any]]] = {}
            for row in intake:
                images_by_call.setdefault(row["call_id"], []).append(dict(row))

            destination.parent.mkdir(parents=True, exist_ok=True)
            counts = {"tz": 0, "errors": 0, "pairs": 0, "images": 0}
            with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                tz_lines: list[str] = []
                error_lines: list[str] = []
                pair_lines: list[str] = []
                for row in calls:
                    call = dict(row)
                    images = []
                    for image in images_by_call.get(call["id"], []):
                        entry = None
                        source = self.files_dir / image["file"] if image.get("file") else None
                        if source is not None and source.is_file():
                            entry = f"images/{Path(image['file']).name}"
                            archive.write(source, entry)
                            counts["images"] += 1
                        images.append({"id": image["id"], "name": image["name"],
                                       "mime": image["mime"], "sha256": image["sha256"],
                                       "file": entry})
                    base = {
                        "call_id": call["id"], "created_at": call["created_at"],
                        "organization_id": call["organization_id"],
                        "workflow": call["workflow"], "node": call["node"],
                        "provider": call["provider"], "model": call["model"],
                        "prompt_version": call["prompt_version"],
                        "trace_id": call["trace_id"],
                    }
                    is_tz = (call["workflow"] == "import_tz" or call["node"] == "create_paramspec"
                             or bool(images))
                    if is_tz:
                        tz_lines.append(json.dumps({
                            **base, "text": call["message"], "images": images,
                            "vision_facts": call["vision_facts"], "outcome": call["outcome"],
                            "error_code": call["error_code"],
                            "paramspec": _json_value(call["after_spec"]),
                        }, ensure_ascii=False))
                    if call["outcome"] == "error":
                        error_lines.append(json.dumps({
                            **base, "error_code": call["error_code"],
                            "error_class": call["error_class"],
                            "fingerprint": call["fingerprint"],
                            "message": call["message"], "reply": call["reply"],
                            "raw_response": call["raw_response"],
                            "vision_facts": call["vision_facts"],
                            "exception": call["exception"],
                            "before_spec": _json_value(call["before_spec"]),
                            "check_report": _json_value(call["check_report"]),
                            "images": images, "review_status": call["review_status"],
                        }, ensure_ascii=False))
                    elif call["after_spec"]:
                        pair_lines.append(json.dumps({
                            **base,
                            "type": "tz_to_paramspec" if is_tz else "edit",
                            "input_text": call["message"], "images": images,
                            "vision_facts": call["vision_facts"],
                            "before_spec": _json_value(call["before_spec"]),
                            "after_spec": _json_value(call["after_spec"]),
                            "reviewed": False,
                        }, ensure_ascii=False))
                counts.update(tz=len(tz_lines), errors=len(error_lines), pairs=len(pair_lines))
                for name, lines in (("tz.jsonl", tz_lines), ("errors.jsonl", error_lines),
                                    ("pairs.jsonl", pair_lines)):
                    archive.writestr(name, "\n".join(lines) + ("\n" if lines else ""))
                manifest = {"generated_at": until, "since": since_iso, "until": until,
                            "store_content": self.store_content, "counts": counts}
                archive.writestr("manifest.json",
                                 json.dumps(manifest, ensure_ascii=False, indent=2))
            if mark:
                self._set_state(connection, "last_export_at", until)
        return {**manifest, "path": str(destination)}


__all__ = ["AIJournal", "ERROR_CLASSES", "fingerprint", "parse_since", "redact"]
