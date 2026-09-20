"""Telegram-бот мониторинга: команды, права, выгрузка и алерты без сети."""

from __future__ import annotations

import json
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.ai_journal import AIJournal                        # noqa: E402
from src.telegram_bot import HELP_TEXT, MonitorBot           # noqa: E402

GROUP = -5558841581
MONDAY_11 = datetime(2026, 9, 21, 11, 0).timestamp()        # понедельник, локальное время


class FakeAPI:
    def __init__(self, status: str = "administrator") -> None:
        self.messages: list[tuple[int, str]] = []
        self.documents: list[tuple[int, str, str, dict]] = []
        self.files: list[tuple[int, str, str]] = []
        self.status = status

    def send_message(self, chat_id, text):
        self.messages.append((chat_id, text))

    def send_document(self, chat_id, path, caption):
        self.files.append((chat_id, Path(path).name, caption))
        if str(path).endswith(".zip"):
            with zipfile.ZipFile(path) as archive:
                manifest = json.loads(archive.read("manifest.json"))
            self.documents.append((chat_id, Path(path).name, caption, manifest))

    def get_chat_member_status(self, chat_id, user_id):
        return self.status

    def get_updates(self, offset, timeout=30):
        return []

    def set_commands(self, commands):
        pass


class Clock:
    def __init__(self, value: float) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def _bot(tmp_path, api=None, *, clock=None, health=None, balances=None, tokens_per_day=1000):
    journal = AIJournal(tmp_path / "journal.sqlite3", store_content=True)
    bot = MonitorBot(api or FakeAPI(), journal, chat_id=GROUP, out_dir=tmp_path,
                     state_path=tmp_path / "bot_state.json", tokens_per_day=tokens_per_day,
                     backup_dir=tmp_path / "backups",
                     health_fn=health, balance_fn=balances or (lambda: []),
                     now=clock or Clock(MONDAY_11 - 86400 * 3))
    return bot, journal


def _backups(tmp_path, *, big: bool = False):
    folder = tmp_path / "backups"
    folder.mkdir(exist_ok=True)
    (folder / "studio-data-2026-09-19.tgz").write_bytes(b"old")
    (folder / "studio-data-2026-09-20.tgz").write_bytes(b"x" * (60 * 1024 * 1024 if big else 2048))
    (folder / "paramspecs-2026-09-20.tgz").write_bytes(b"specs")
    return folder


def _cmd(text, chat=GROUP, user=1):
    return {"update_id": 1, "message": {"text": text, "chat": {"id": chat},
                                        "from": {"id": user}}}


def _gate_error(journal, node="edit_operations", code="production_gate_rejected"):
    journal.record_call(workflow="chat", capture={"provider": "glm", "node": node},
                        result={"error": "x", "code": code,
                                "check_report": {"errors": [{"code": "bounds.outside"}]},
                                "usage": {"model": "glm-4.5-flash", "total": 100}},
                        message="сделай шире")


def test_help_and_chatid_and_foreign_chats_are_ignored(tmp_path):
    api = FakeAPI()
    bot, _ = _bot(tmp_path, api)
    bot.handle_update(_cmd("/help@Akedastudiobot"))
    bot.handle_update(_cmd("/chatid", chat=777))
    bot.handle_update(_cmd("/stats", chat=777))                # чужой чат — тишина
    assert api.messages[0] == (GROUP, HELP_TEXT)
    assert api.messages[1][0] == 777 and "777" in api.messages[1][1]
    assert len(api.messages) == 2
    assert all(f"/{name}" in HELP_TEXT for name in ("limits", "stats", "errors", "export"))


def test_stats_errors_and_limits(tmp_path):
    api = FakeAPI()
    bot, journal = _bot(tmp_path, api, balances=lambda: [
        {"provider": "DeepSeek", "label": "Баланс аккаунта", "value": 110.5, "unit": "¥"},
        {"provider": "GLM (Zhipu)", "value": None, "note": "нет API баланса"},
    ])
    (tmp_path / "chat_tokens.json").write_text(json.dumps({
        datetime.fromtimestamp(bot.now()).strftime("%Y-%m-%d"): 250}), encoding="utf-8")
    _gate_error(journal)
    journal.record_call(workflow="import_tz", capture={"provider": "glm",
                                                       "node": "create_paramspec"},
                        result={"reply": "ok", "spec": {"a": 1}, "usage": {"total": 50}})
    for command in ("/stats 7d", "/errors", "/limits", "/stats вчера"):
        bot.handle_update(_cmd(command))
    stats, errors, limits, bad = (text for _, text in api.messages)
    assert "Запросов к ИИ: 2 · ошибок: 1" in stats and "Пришло ТЗ: 1" in stats
    assert "production_gate_rejected" in errors and "🆕" in errors
    assert "250 / 1 000" in limits and "110.50 ¥" in limits and "нет API баланса" in limits
    assert "Не понял период" in bad


def test_export_only_for_admins_and_incremental(tmp_path):
    api = FakeAPI(status="member")
    bot, journal = _bot(tmp_path, api)
    _gate_error(journal)
    bot.handle_update(_cmd("/export"))
    assert "только администраторам" in api.messages[-1][1] and not api.documents

    api.status = "creator"
    bot.handle_update(_cmd("/export"))
    [(chat, name, caption, manifest)] = api.documents
    assert chat == GROUP and name.endswith(".zip") and "ошибки: 1" in caption
    bot.handle_update(_cmd("/export"))                        # всё уже выгружено
    assert "Новых данных" in api.messages[-1][1] and len(api.documents) == 1
    bot.handle_update(_cmd("/export all"))                    # вся база — отметку не трогает
    assert len(api.documents) == 2


def test_backup_goes_to_group_for_admins_only(tmp_path):
    api = FakeAPI(status="member")
    bot, _ = _bot(tmp_path, api)
    _backups(tmp_path)
    bot.handle_update(_cmd("/backup"))
    assert "только администраторам" in api.messages[-1][1] and not api.files

    api.status = "creator"
    bot.handle_update(_cmd("/backup"))
    names = [name for _, name, _ in api.files]
    assert names == ["studio-data-2026-09-20.tgz", "paramspecs-2026-09-20.tgz"]  # свежие
    assert "Бэкап по запросу" in api.files[0][2]


def test_backup_too_big_for_telegram_is_reported(tmp_path):
    api = FakeAPI(status="creator")
    bot, _ = _bot(tmp_path, api)
    _backups(tmp_path, big=True)
    bot.handle_update(_cmd("/backup"))
    assert "больше лимита Telegram" in api.messages[-1][1]
    assert [name for _, name, _ in api.files] == ["paramspecs-2026-09-20.tgz"]


def test_health_alert_after_two_failures_and_recovery(tmp_path):
    api = FakeAPI()
    status = {"ok": False}
    bot, _ = _bot(tmp_path, api, health=lambda: status["ok"])
    bot.run_checks()
    assert api.messages == []                                  # одна неудача — ещё не алерт
    bot.run_checks()
    bot.run_checks()
    assert len(api.messages) == 1 and "не отвечает" in api.messages[0][1]
    status["ok"] = True
    bot.run_checks()
    assert "снова отвечает" in api.messages[-1][1] and len(api.messages) == 2


def test_budget_new_error_provider_failures_and_balance_alerts(tmp_path):
    api = FakeAPI()
    clock = Clock(MONDAY_11 - 86400 * 3)
    balance = {"value": 100.0}
    bot, journal = _bot(tmp_path, api, clock=clock, balances=lambda: [
        {"provider": "DeepSeek", "label": "", "value": balance["value"], "unit": "¥"}])
    day = datetime.fromtimestamp(clock()).strftime("%Y-%m-%d")
    (tmp_path / "chat_tokens.json").write_text(json.dumps({day: 850}), encoding="utf-8")
    clock.value += 1
    _gate_error(journal)
    for _ in range(3):
        _gate_error(journal, node="create_paramspec", code="ai_provider_failed")
    bot.run_checks()
    texts = [text for _, text in api.messages]
    assert any("85%" in text for text in texts)                # бюджет 80%
    assert sum("Новый тип ошибки" in text for text in texts) == 2
    assert any("сбоев за 15 минут" in text for text in texts)

    api.messages.clear()
    clock.value += 60
    balance["value"] = 15.0
    bot.run_checks()
    texts = [text for _, text in api.messages]
    assert texts == [next(t for t in texts if "Баланс DeepSeek" in t)]   # только баланс: 15%
    assert "15%" in texts[0]


def test_weekly_summary_and_backup_once_per_week(tmp_path):
    api = FakeAPI()
    clock = Clock(MONDAY_11)
    bot, _ = _bot(tmp_path, api, clock=clock)
    _backups(tmp_path)
    bot.run_checks()
    clock.value += 3600
    bot.run_checks()
    weekly = [text for _, text in api.messages if "Итоги недели" in text]
    assert len(weekly) == 1
    assert [name for _, name, _ in api.files] == [
        "studio-data-2026-09-20.tgz", "paramspecs-2026-09-20.tgz",
    ]
    assert "еженедельный" in api.files[0][2]


def test_state_survives_restart(tmp_path):
    api = FakeAPI()
    bot, journal = _bot(tmp_path, api)
    _gate_error(journal)
    bot.run_checks()
    alerts = len(api.messages)
    again, _ = _bot(tmp_path, api)                             # перезапуск сервиса
    again.run_checks()
    assert len(api.messages) == alerts                         # тот же алерт не повторился
