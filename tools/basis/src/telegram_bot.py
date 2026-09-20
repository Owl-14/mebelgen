"""Telegram-бот мониторинга Akeda Studio: лимиты нейросетей, ошибки, ТЗ, выгрузка базы.

Алерты уходят в одну группу (TELEGRAM_ALERT_CHAT_ID), команды принимаются только
из неё (кроме /chatid, которая помогает эту группу найти). Бот читает AI-журнал
и суточный счётчик токенов Studio; в Studio ничего не меняет. Клиентский
контент в Telegram не уходит, кроме /export — ZIP-выгрузки базы по запросу
администратора группы.
"""

from __future__ import annotations

import html
import json
import logging
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .ai_journal import AIJournal, parse_since, redact

LOGGER = logging.getLogger("akeda.bot")

MAX_TEXT = 4000
MAX_DOCUMENT_BYTES = 48 * 1024 * 1024          # у Bot API лимит 50 МБ на файл
HEALTH_FAILURES_TO_ALERT = 2
PROVIDER_FAILURE_WINDOW = timedelta(minutes=15)
PROVIDER_FAILURES_TO_ALERT = 3

COMMANDS = [
    ("help", "Справка по командам"),
    ("limits", "Балансы нейросетей и расход токенов"),
    ("stats", "Сводка: запросы, ошибки, ТЗ (24h/7d/30d)"),
    ("errors", "Частые ошибки (24h/7d/30d)"),
    ("export", "ZIP с ТЗ, ответами и ошибками (админы)"),
    ("backup", "Свежий бэкап сервера файлами (админы)"),
    ("chatid", "ID этого чата"),
]

HELP_TEXT = """<b>Бот мониторинга Akeda Studio</b>

/limits — балансы и лимиты нейросетей, расход токенов Studio за сегодня
/stats [24h|7d|30d] — сводка: запросы к ИИ, ошибки, пришедшие ТЗ, токены по провайдерам (по умолчанию 24h)
/errors [24h|7d|30d] — самые частые ошибки, 🆕 — появились впервые за период
/export — ZIP со всем новым с прошлой выгрузки: ТЗ, ответы нейросетей, ошибки, пары для дообучения
/export 7d — за период, /export all — вся база (только администраторы группы)
/backup — прислать свежий бэкап сервера файлами: изделия компаний, учётки, AI-журнал с фото ТЗ (только администраторы группы)
/chatid — ID этого чата
/help — эта справка

<b>Сам бот пишет, когда</b>
• Studio не отвечает (и когда снова заработала)
• суточный бюджет токенов Studio израсходован на 80% и 95%
• баланс нейросети упал ниже 20% и 5% от максимума
• провайдер нейросети сбоит несколько раз подряд
• появилась ошибка нового типа
По понедельникам в 10:00 — сводка за неделю и бэкап файлами."""

ERROR_CLASS_NAMES = {
    "infrastructure": "инфраструктура",
    "contract": "контракт промпта",
    "production_gate": "производственный гейт",
    "other": "прочее",
}


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _num(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _esc(value)
    text = f"{number:,.2f}" if number != int(number) else f"{int(number):,}"
    return text.replace(",", " ")


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="microseconds")


class TelegramAPI:
    """Тонкий клиент Bot API на requests (без новых зависимостей)."""

    def __init__(self, token: str, *, proxy: str | None = None, timeout: float = 60) -> None:
        import requests

        self._session = requests.Session()
        if proxy:
            self._session.proxies = {"https": proxy, "http": proxy}
        self._base = f"https://api.telegram.org/bot{token}/"
        self._timeout = timeout

    def call(self, method: str, *, files: Any = None, timeout: float | None = None,
             **params: Any) -> Any:
        response = self._session.post(self._base + method, data=params if files else None,
                                      json=None if files else params, files=files,
                                      timeout=timeout or self._timeout)
        body = response.json()
        if not body.get("ok"):
            raise RuntimeError(f"Telegram {method}: {body.get('description')}")
        return body.get("result")

    def get_updates(self, offset: int, timeout: int = 30) -> list[dict[str, Any]]:
        # long polling: HTTP-таймаут больше серверного, иначе обрыв каждые timeout секунд
        response = self._session.post(
            self._base + "getUpdates",
            json={"offset": offset, "timeout": timeout, "allowed_updates": ["message"]},
            timeout=timeout + 15,
        )
        body = response.json()
        if not body.get("ok"):
            raise RuntimeError(f"Telegram getUpdates: {body.get('description')}")
        return list(body.get("result") or [])

    def send_message(self, chat_id: int, text: str) -> None:
        self.call("sendMessage", chat_id=chat_id, text=text[:MAX_TEXT],
                  parse_mode="HTML", disable_web_page_preview=True)

    def send_document(self, chat_id: int, path: Path, caption: str) -> None:
        with Path(path).open("rb") as stream:
            self.call("sendDocument", files={"document": (Path(path).name, stream)},
                      chat_id=chat_id, caption=caption[:1000], parse_mode="HTML",
                      timeout=300)

    def get_chat_member_status(self, chat_id: int, user_id: int) -> str:
        member = self.call("getChatMember", chat_id=chat_id, user_id=user_id)
        return str((member or {}).get("status") or "")

    def set_commands(self, commands: list[tuple[str, str]]) -> None:
        self.call("setMyCommands",
                  commands=[{"command": name, "description": text} for name, text in commands])


def collect_balances() -> list[dict[str, Any]]:
    """Балансы всех нейросетей, для которых в .env есть ключ."""
    from .spec_chat import available_providers, token_balance

    result: list[dict[str, Any]] = []
    for provider in available_providers()["providers"]:
        if provider["id"] == "mock":
            continue
        balance = token_balance(provider["id"])
        name = provider["name"]
        if balance.get("error"):
            result.append({"provider": name, "error": redact(balance["error"], 200)})
            continue
        items = balance.get("items") or []
        if not items:
            result.append({"provider": name, "value": None,
                           "note": "у провайдера нет API баланса — смотрите расход в /stats"})
        for item in items:
            result.append({"provider": name, "label": item.get("label"),
                           "value": item.get("value"), "unit": item.get("unit") or ""})
    return result


def check_health(url: str) -> bool:
    import requests

    try:
        response = requests.get(url, timeout=10)
        return response.ok and bool(response.json().get("ok"))
    except Exception:  # noqa: BLE001 - любая ошибка = «не отвечает»
        return False


class MonitorBot:
    def __init__(
        self,
        api: Any,
        journal: AIJournal,
        *,
        chat_id: int | None,
        out_dir: Path,
        state_path: Path,
        tokens_per_day: int,
        backup_dir: Path | None = None,
        health_fn: Callable[[], bool] | None = None,
        balance_fn: Callable[[], list[dict[str, Any]]] | None = None,
        now: Callable[[], float] = time.time,
        check_interval_s: int = 600,
    ) -> None:
        self.api = api
        self.journal = journal
        self.chat_id = chat_id
        self.out_dir = Path(out_dir)
        self.state_path = Path(state_path)
        self.tokens_per_day = int(tokens_per_day)
        self.backup_dir = Path(backup_dir) if backup_dir else None
        self.health_fn = health_fn
        self.balance_fn = balance_fn or collect_balances
        self.now = now
        self.check_interval_s = check_interval_s
        self.state = self._load_state()
        self.state.setdefault("errors_checked_at", _iso(self.now()))
        self.next_check = 0.0

    # ------------------------------------------------------------ state

    def _load_state(self) -> dict[str, Any]:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.state_path)

    def _alert(self, key: str, text: str, cooldown_s: float) -> bool:
        """Отправить алерт, если этот же ключ не отправлялся в пределах cooldown."""
        if self.chat_id is None:
            return False
        sent = self.state.setdefault("alerts", {})
        now = self.now()
        if now - float(sent.get(key, 0)) < cooldown_s:
            return False
        self.api.send_message(self.chat_id, text)
        sent[key] = now
        return True

    # ------------------------------------------------------------ commands

    def handle_update(self, update: Mapping[str, Any]) -> None:
        message = update.get("message") or {}
        text = str(message.get("text") or "").strip()
        chat = message.get("chat") or {}
        if not text.startswith("/") or "id" not in chat:
            return
        chat_id = int(chat["id"])
        command, *args = text.split()
        command = command.split("@", 1)[0].lower()
        if command == "/chatid":
            self.api.send_message(chat_id, f"ID этого чата: <code>{chat_id}</code>")
            return
        if self.chat_id is None or chat_id != self.chat_id:
            return                                   # чужие чаты игнорируем молча
        handlers = {
            "/help": lambda: HELP_TEXT, "/start": lambda: HELP_TEXT,
            "/limits": self.limits_text,
            "/stats": lambda: self.stats_text(args[0] if args else "24h"),
            "/errors": lambda: self.errors_text(args[0] if args else "24h"),
        }
        if command == "/export":
            self.export(message, args)
            return
        if command == "/backup":
            if not self._is_admin(message):
                self.api.send_message(chat_id, "Бэкап доступен только администраторам группы.")
                return
            self.send_backups(reason="по запросу")
            return
        handler = handlers.get(command)
        if handler is None:
            return
        try:
            reply = handler()
        except ValueError:
            reply = "Не понял период. Примеры: 24h, 7d, 30d."
        self.api.send_message(chat_id, reply)

    def tokens_used_today(self) -> int:
        day = time.strftime("%Y-%m-%d", time.localtime(self.now()))
        try:
            data = json.loads((self.out_dir / "chat_tokens.json").read_text(encoding="utf-8"))
            return int(data.get(day, 0)) if isinstance(data, dict) else 0
        except (OSError, ValueError, TypeError):
            return 0

    def limits_text(self) -> str:
        used = self.tokens_used_today()
        percent = round(used * 100 / self.tokens_per_day) if self.tokens_per_day else 0
        lines = ["<b>Лимиты</b>",
                 f"Суточный бюджет Studio: {_num(used)} / {_num(self.tokens_per_day)} "
                 f"токенов ({percent}%)", ""]
        balances = self.balance_fn()
        if not balances:
            lines.append("Ключи нейросетей не заданы — Studio работает только в базовом режиме.")
        for item in balances:
            name = f"<b>{_esc(item['provider'])}</b>"
            if item.get("error"):
                lines.append(f"{name}: ⚠️ не удалось получить баланс — {_esc(item['error'])}")
            elif item.get("value") is None:
                lines.append(f"{name}: {_esc(item.get('note') or 'баланс неизвестен')}")
            else:
                label = f" ({_esc(item['label'])})" if item.get("label") else ""
                lines.append(f"{name}{label}: {_num(item['value'])} {_esc(item.get('unit'))}")
        return "\n".join(lines)

    def stats_text(self, period: str) -> str:
        summary = self.journal.summary(period)
        calls, errors = summary["calls"], summary["errors"]
        share = f" ({round(errors * 100 / calls)}%)" if calls else ""
        lines = [f"<b>Сводка за {_esc(period)}</b>",
                 f"Запросов к ИИ: {calls} · ошибок: {errors}{share}",
                 f"Пришло ТЗ: {summary['tz']}",
                 f"Токенов: {_num(summary['tokens_total'])}"]
        if summary["by_provider"]:
            lines.append("")
            lines.append("По провайдерам:")
            for row in summary["by_provider"]:
                title = " / ".join(part for part in (row["provider"], row["model"]) if part)
                lines.append(f"• {_esc(title or 'без провайдера')} — {row['calls']} запр., "
                             f"{row['errors'] or 0} ош., {_num(row['tokens'])} ток.")
        return "\n".join(lines)

    def errors_text(self, period: str) -> str:
        summary = self.journal.summary(period)
        if not summary["top_errors"]:
            return f"За {_esc(period)} ошибок нет 👌"
        lines = [f"<b>Ошибки за {_esc(period)}</b> (всего {summary['errors']})"]
        for row in summary["top_errors"]:
            mark = "🆕 " if row.get("new") else ""
            kind = ERROR_CLASS_NAMES.get(row["error_class"], row["error_class"])
            lines.append(f"{mark}<code>{_esc(row['error_code'])}</code> · {_esc(kind)} · "
                         f"{_esc(row['node'] or '—')} — {row['count']}")
        lines.append("")
        lines.append("Полные ответы нейросети и отчёты гейта — в /export.")
        return "\n".join(lines)

    def _is_admin(self, message: Mapping[str, Any]) -> bool:
        chat_id = int(message["chat"]["id"])
        user_id = int((message.get("from") or {}).get("id") or 0)
        if not user_id:
            return False
        return self.api.get_chat_member_status(chat_id, user_id) in ("creator", "administrator")

    def send_backups(self, *, reason: str) -> None:
        """Отправить в группу свежие архивы бэкапа: вторая копия вне сервера."""
        if self.chat_id is None or self.backup_dir is None:
            return
        archives = []
        for pattern in ("studio-data-*.tgz", "paramspecs-*.tgz"):
            found = sorted(self.backup_dir.glob(pattern), key=lambda p: p.stat().st_mtime)
            if found:
                archives.append(found[-1])
        if not archives:
            self.api.send_message(self.chat_id, "Бэкапов пока нет: ночная копия ещё не делалась.")
            return
        for path in archives:
            size = path.stat().st_size
            if size > MAX_DOCUMENT_BYTES:
                self.api.send_message(
                    self.chat_id,
                    f"⚠️ Бэкап <code>{_esc(path.name)}</code> весит {size // (1024 * 1024)} МБ — "
                    "больше лимита Telegram (50 МБ). Забирайте его с сервера "
                    "(<code>/opt/bazis/backups</code>) или настроим выгрузку в хранилище.")
                continue
            made = time.strftime("%d.%m %H:%M", time.localtime(path.stat().st_mtime))
            self.api.send_document(self.chat_id, path,
                                   f"💾 Бэкап {_esc(reason)}: <code>{_esc(path.name)}</code>, "
                                   f"{size // 1024} КБ, от {made}")

    def export(self, message: Mapping[str, Any], args: list[str]) -> None:
        chat_id = int(message["chat"]["id"])
        if not self._is_admin(message):
            self.api.send_message(chat_id, "Выгрузка базы доступна только администраторам группы.")
            return
        since: str | None = None
        if args:
            raw = args[0].lower()
            since = "" if raw in ("all", "всё", "все") else raw
            if since:
                try:
                    parse_since(since)
                except ValueError:
                    self.api.send_message(chat_id, "Не понял период. Примеры: /export, "
                                                   "/export 7d, /export all.")
                    return
        with tempfile.TemporaryDirectory() as folder:
            stamp = time.strftime("%Y%m%d_%H%M", time.localtime(self.now()))
            path = Path(folder) / f"akeda_ai_{stamp}.zip"
            manifest = self.journal.export_zip(path, since=since, mark=False)
            counts = manifest["counts"]
            if not any(counts[key] for key in ("tz", "errors", "pairs")):
                self.api.send_message(chat_id, "Новых данных с прошлой выгрузки нет."
                                      if since is None else "За этот период данных нет.")
                return
            size = path.stat().st_size
            if size > MAX_DOCUMENT_BYTES:
                self.api.send_message(
                    chat_id,
                    f"Архив {size // (1024 * 1024)} МБ — больше лимита Telegram (50 МБ). "
                    "Выгрузите на сервере: <code>main.py ai-journal export --since 7d</code> "
                    "или запросите период короче.")
                return
            caption = (f"ТЗ: {counts['tz']} · ошибки: {counts['errors']} · "
                       f"пары для обучения: {counts['pairs']} · фото: {counts['images']}")
            self.api.send_document(chat_id, path, caption)
        if since is None:                           # отметку сдвигаем только после доставки
            self.journal.mark_export(manifest["until"])

    # ------------------------------------------------------------ checks

    def run_checks(self) -> None:
        for check in (self._check_health, self._check_budget, self._check_balances,
                      self._check_new_errors, self._check_provider_failures, self._weekly):
            try:
                check()
            except Exception as error:  # noqa: BLE001 - одна проверка не валит остальные
                LOGGER.warning("bot_check_failed check=%s error=%s",
                               check.__name__, redact(error, 300))
        self.save_state()

    def _check_health(self) -> None:
        if self.health_fn is None:
            return
        if self.health_fn():
            if self.state.get("health_alerted"):
                self._alert("health_up", "✅ Studio снова отвечает.", 0)
            self.state["health_failures"] = 0
            self.state["health_alerted"] = False
            return
        failures = int(self.state.get("health_failures", 0)) + 1
        self.state["health_failures"] = failures
        if failures >= HEALTH_FAILURES_TO_ALERT and not self.state.get("health_alerted"):
            if self._alert("health_down", "🔴 Studio не отвечает уже несколько минут. "
                                          "systemd перезапускает сервис; если не поднимется — "
                                          "нужна ручная проверка сервера.", 0):
                self.state["health_alerted"] = True

    def _check_budget(self) -> None:
        if not self.tokens_per_day:
            return
        used = self.tokens_used_today()
        percent = used * 100 / self.tokens_per_day
        day = time.strftime("%Y-%m-%d", time.localtime(self.now()))
        for level in (95, 80):
            if percent >= level:
                self._alert(f"budget{level}:{day}",
                            f"🟠 Суточный бюджет токенов Studio израсходован на {round(percent)}% "
                            f"({_num(used)} из {_num(self.tokens_per_day)}). При 100% чат ИИ "
                            "до конца дня отключится.", 86400)
                break

    def _check_balances(self) -> None:
        peaks = self.state.setdefault("balance_peaks", {})
        for item in self.balance_fn():
            try:
                value = float(item.get("value"))
            except (TypeError, ValueError):
                continue
            key = f"{item.get('provider')}|{item.get('label') or ''}"
            peak = max(float(peaks.get(key, 0)), value)
            peaks[key] = peak
            if peak <= 0:
                continue
            ratio = value / peak
            label = f"{item['provider']}" + (f" ({item['label']})" if item.get("label") else "")
            text = (f"{{icon}} Баланс {_esc(label)}: {_num(value)} {_esc(item.get('unit'))} — "
                    f"{round(ratio * 100)}% от максимума. Пополните, иначе ИИ в Studio встанет.")
            if ratio < 0.05:
                self._alert(f"balance5:{key}", text.format(icon="🔴"), 6 * 3600)
            elif ratio < 0.2:
                self._alert(f"balance20:{key}", text.format(icon="🟠"), 24 * 3600)

    def _check_new_errors(self) -> None:
        since = str(self.state.get("errors_checked_at") or _iso(self.now()))
        self.state["errors_checked_at"] = _iso(self.now())
        for row in self.journal.new_error_types(since)[:5]:
            kind = ERROR_CLASS_NAMES.get(row["error_class"], row["error_class"])
            self._alert(f"new_error:{row['fingerprint']}",
                        f"🆕 Новый тип ошибки: <code>{_esc(row['error_code'])}</code>\n"
                        f"Класс: {_esc(kind)} · узел: {_esc(row['node'] or '—')} · "
                        f"случаев: {row['count']}\nПодробности — /errors, полные данные — /export.",
                        30 * 86400)

    def _check_provider_failures(self) -> None:
        since = _iso(self.now() - PROVIDER_FAILURE_WINDOW.total_seconds())
        count = self.journal.count_errors(["ai_provider_failed", "invalid_provider_response"],
                                          since)
        if count >= PROVIDER_FAILURES_TO_ALERT:
            self._alert("provider_failures",
                        f"🔴 Нейросеть не отвечает: {count} сбоев за 15 минут. Частые причины — "
                        "кончились деньги/лимит, неверный ключ, недоступен провайдер. /limits",
                        30 * 60)

    def _weekly(self) -> None:
        local = time.localtime(self.now())
        if local.tm_wday != 0 or local.tm_hour < 10:
            return
        week = time.strftime("%G-W%V", local)
        if self.state.get("weekly_sent") == week:
            return
        if self.chat_id is not None:
            self.api.send_message(self.chat_id, "📊 Итоги недели\n\n" + self.stats_text("7d")
                                  + "\n\n" + self.errors_text("7d"))
            self.send_backups(reason="еженедельный")
        self.state["weekly_sent"] = week

    # ------------------------------------------------------------ loop

    def poll_once(self, timeout: int = 30) -> None:
        offset = int(self.state.get("offset", 0))
        for update in self.api.get_updates(offset, timeout=timeout):
            self.state["offset"] = int(update["update_id"]) + 1
            try:
                self.handle_update(update)
            except Exception as error:  # noqa: BLE001 - плохая команда не роняет бота
                LOGGER.warning("bot_update_failed error=%s", redact(error, 300))
        if self.now() >= self.next_check:
            self.run_checks()
            self.next_check = self.now() + self.check_interval_s
        self.save_state()

    def run_forever(self) -> None:
        try:
            self.api.set_commands(COMMANDS)
        except Exception as error:  # noqa: BLE001
            LOGGER.warning("bot_set_commands_failed error=%s", redact(error, 300))
        while True:
            try:
                self.poll_once()
            except Exception as error:  # noqa: BLE001 - сеть моргнула — пробуем снова
                LOGGER.warning("bot_poll_failed error=%s", redact(error, 300))
                time.sleep(10)


def run_bot() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("Нет TELEGRAM_BOT_TOKEN в .env")
    raw_chat = os.environ.get("TELEGRAM_ALERT_CHAT_ID", "").strip()
    out_dir = Path(os.environ.get("STUDIO_OUT_DIR", "out"))
    journal = AIJournal(os.environ.get("AI_JOURNAL_DB") or out_dir / "ai_journal.sqlite3")
    health_url = os.environ.get("BOT_HEALTH_URL", "").strip()
    bot = MonitorBot(
        TelegramAPI(token, proxy=os.environ.get("TELEGRAM_PROXY") or None),
        journal,
        chat_id=int(raw_chat) if raw_chat else None,
        out_dir=out_dir,
        state_path=out_dir / "bot_state.json",
        tokens_per_day=int(os.environ.get("STUDIO_TOKENS_PER_DAY", "400000")),
        backup_dir=Path(os.environ.get("BOT_BACKUP_DIR", "/opt/bazis/backups")),
        health_fn=(lambda: check_health(health_url)) if health_url else None,
        check_interval_s=int(os.environ.get("BOT_CHECK_INTERVAL_S", "300")),
    )
    LOGGER.info("bot started chat_id=%s", raw_chat or "not configured")
    bot.run_forever()


__all__ = ["COMMANDS", "HELP_TEXT", "MonitorBot", "TelegramAPI", "collect_balances", "run_bot"]
