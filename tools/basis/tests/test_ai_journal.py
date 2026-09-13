"""AI-журнал: запись вызовов, приватность ключей, сводка и выгрузка для дообучения."""

from __future__ import annotations

import base64
import json
import sys
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.ai_journal import AIJournal, fingerprint, parse_since   # noqa: E402

GATE_REPORT = {"ok": False, "errors": [{"code": "bounds.outside"},
                                       {"code": "drilling.system32"}]}


def _gate_failure() -> dict:
    return {
        "reply": "Правка отклонена производственным гейтом",
        "error": "Предложенная AI-правка не прошла производственный гейт.",
        "code": "production_gate_rejected",
        "spec": None,
        "check_report": GATE_REPORT,
        "usage": {"model": "glm-4.5-flash", "prompt": 100, "completion": 20, "total": 120},
    }


def _row(journal: AIJournal, sql: str):
    with closing(journal.connect()) as connection:
        return connection.execute(sql).fetchall()


def test_records_errors_with_content_and_groups_them(tmp_path):
    journal = AIJournal(tmp_path / "journal.sqlite3", store_content=True)
    capture = {"provider": "glm", "node": "edit_operations", "prompt_version": "1.0.0",
               "raw_response": '{"operations": []}'}
    for _ in range(2):
        assert journal.record_call(workflow="chat", result=_gate_failure(), capture=capture,
                                   organization_id="org-1", message="сделай шире",
                                   before_spec={"width": 800})
    journal.record_call(workflow="chat", capture={"provider": "glm", "node": "edit_operations"},
                        result={"reply": "Готово.", "spec": {"width": 900},
                                "usage": {"total": 30}}, message="ещё шире")

    summary = journal.summary("1d")
    assert (summary["calls"], summary["errors"], summary["tokens_total"]) == (3, 2, 270)
    [top] = summary["top_errors"]
    assert top["count"] == 2 and top["error_class"] == "production_gate" and top["new"] is True
    assert top["fingerprint"] == fingerprint(
        "production_gate_rejected", "edit_operations", GATE_REPORT
    )
    [error] = _row(journal, "SELECT * FROM ai_calls WHERE outcome = 'error' LIMIT 1")
    assert error["raw_response"] == '{"operations": []}'
    assert error["message"] == "сделай шире" and error["model"] == "glm-4.5-flash"
    assert json.loads(error["check_report"]) == GATE_REPORT


def test_content_off_keeps_only_metadata(tmp_path):
    journal = AIJournal(tmp_path / "journal.sqlite3", store_content=False)
    journal.save_tz_images("call-1", [{"mime": "image/png",
                                       "data": base64.b64encode(b"PNGDATA").decode()}],
                           source="import_tz", organization_id="org-1")
    journal.record_call(call_id="call-1", workflow="import_tz", result=_gate_failure(),
                        capture={"raw_response": "ответ модели"}, message="ТЗ")

    [call] = _row(journal, "SELECT * FROM ai_calls")
    assert call["message"] is None and call["raw_response"] is None
    assert call["check_report"] is None and call["tokens_total"] == 120
    [intake] = _row(journal, "SELECT * FROM tz_intake")
    assert intake["file"] is None and intake["bytes"] == 7 and intake["call_id"] == "call-1"
    assert not (tmp_path / "ai_journal_files").exists()


def test_exception_text_never_keeps_provider_keys(tmp_path):
    journal = AIJournal(tmp_path / "journal.sqlite3")
    journal.record_call(
        workflow="import_tz", result={}, error_code="ai_provider_failed",
        exception=RuntimeError("403 for url https://g.test/m:generateContent?key=AIzaSECRET "
                               "Authorization: Bearer abc.def-123"),
    )
    [call] = _row(journal, "SELECT * FROM ai_calls")
    assert "AIzaSECRET" not in call["exception"] and "abc.def-123" not in call["exception"]
    assert "key=***" in call["exception"]
    assert call["error_class"] == "infrastructure" and call["outcome"] == "error"


def test_broken_journal_never_raises(tmp_path):
    journal = AIJournal(tmp_path)                       # каталог вместо файла БД
    assert journal.record_call(workflow="chat", result={"reply": "ok"}) is None
    assert journal.save_tz_images("call", [{"data": "QUJD"}], source="chat_photo") == []


def test_export_zip_for_training_and_incremental_mark(tmp_path):
    journal = AIJournal(tmp_path / "journal.sqlite3", store_content=True)
    journal.save_tz_images("call-ok", [{"mime": "image/jpeg", "name": "tz.jpg",
                                        "data": base64.b64encode(b"JPEG").decode()}],
                           source="import_tz", organization_id="org-1")
    journal.record_call(call_id="call-ok", workflow="import_tz", message="tz.jpg",
                        image_count=1, capture={"node": "create_paramspec",
                                                "vision_facts": "тумба 600×450×550"},
                        result={"reply": "Создано.", "spec": {"project_name": "Тумба"}})
    journal.record_call(workflow="chat", result=_gate_failure(), message="сделай шире",
                        capture={"node": "edit_operations", "raw_response": "{}"})

    manifest = journal.export_zip(tmp_path / "export.zip")
    assert manifest["counts"] == {"tz": 1, "errors": 1, "pairs": 1, "images": 1}
    with zipfile.ZipFile(tmp_path / "export.zip") as archive:
        names = set(archive.namelist())
        assert {"tz.jsonl", "errors.jsonl", "pairs.jsonl", "manifest.json"} <= names
        [pair] = [json.loads(line) for line in archive.read("pairs.jsonl").decode().splitlines()]
        assert pair["type"] == "tz_to_paramspec"
        assert pair["after_spec"] == {"project_name": "Тумба"}
        assert pair["vision_facts"] == "тумба 600×450×550"
        assert pair["images"][0]["file"] in names
        assert archive.read(pair["images"][0]["file"]) == b"JPEG"
        [error] = [json.loads(line) for line in archive.read("errors.jsonl").decode().splitlines()]
        assert error["check_report"] == GATE_REPORT and error["raw_response"] == "{}"

    again = journal.export_zip(tmp_path / "again.zip")
    assert again["counts"] == {"tz": 0, "errors": 0, "pairs": 0, "images": 0}
    everything = journal.export_zip(tmp_path / "all.zip", since="", mark=False)
    assert everything["counts"]["errors"] == 1


def test_parse_since_accepts_periods_and_dates():
    now = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
    assert parse_since("7d", now=now).startswith("2026-09-06T12:00:00")
    assert parse_since("24h", now=now).startswith("2026-09-12T12:00:00")
    assert parse_since("2026-09-01").startswith("2026-09-01T00:00:00")
    assert parse_since("") == ""
