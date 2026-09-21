# Runbook studio.akeda.ru (AKD-264 / MEB-106)

Сервер **144.31.50.188** (serv.host, MSK-R7-2, с 19.09.2026; старый 80.66.89.3
удалён за неоплату), SSH `root` только по ключу `bazis_deploy`. Раскладка:
`/opt/bazis/basis` — приложение (копия tools/basis, НЕ git), `/opt/bazis/venv`,
`/opt/bazis/out` — артефакты и журнал токенов, `/opt/bazis/backups`.
Сервисы — `bazis.service` (клиентская Studio) и `bazis-admin.service`
(администрирование аккаунтов). Nginx —
[nginx-bazis.conf](nginx-bazis.conf) + certbot (автопродление certbot.timer).
Данные вне деплоя кода: `/opt/bazis/data/identity.sqlite3`,
`/opt/bazis/tenants/<organization-id>/`, `/opt/bazis/basis/paramspecs/`
(старый каталог) и `/opt/bazis/out`.

## Обновить код
`./ops/deploy-update.sh` из tools/basis (пишет DEPLOY_SHA, тарит код, рестартит
сервис, показывает /version). Изделия посетителей, .env и out не трогаются.
Перед распаковкой в рабочий каталог скрипт проверяет новой версией контракта
все активные `paramspecs/*.json` и `/opt/bazis/tenants/*/paramspecs/*.json`,
а для не-черновиков строит viewer payload. Ошибка останавливает выкладку до
рестарта; проверка работает read-only и не мигрирует пользовательские файлы.

## Проверить состояние
- `curl https://studio.akeda.ru/healthz` — `{"ok":true,...}`;
- `curl https://studio.akeda.ru/version` — какой SHA развёрнут, public-режим;
- `systemctl status bazis bazis-admin`;
- `journalctl -u bazis -u bazis-admin -n 80`;
- `https://studio.akeda.ru/admin` — вход владельца платформы.

## Откат
Развернуть предыдущий SHA: `git checkout <sha> && ./ops/deploy-update.sh`
(или распаковать прошлый /tmp/basis_update.tgz на сервере и рестартнуть).

## Миграция каталога в компанию

Сначала dry-run, затем тот же вызов с `--apply`. На production миграцию запускать
от системного пользователя Studio, иначе новые каталоги окажутся недоступны для
записи сервису:

```bash
sudo -u bazis /opt/bazis/venv/bin/python scripts/migrate_tenant_catalog.py \
  --source paramspecs \
  --tenant-root /opt/bazis/tenants \
  --organization-id <uuid>
```

Исходники не удаляются; черновики и дубли попадают в архив tenant-а.

## Бэкапы изделий
[backup-paramspecs.sh](backup-paramspecs.sh) → `/opt/bazis/backups`, cron 04:00,
глубина 14 суток. Скрипт создаёт отдельную consistent SQLite backup и архивирует
`tenants/`; legacy-каталог остаётся в `paramspecs-<дата>.tgz`.

## AI-журнал (ТЗ, ответы нейросетей, ошибки)
Каждый вызов нейросети из чата и импорта ТЗ пишется в
`/opt/bazis/data/ai_journal.sqlite3`, фото ТЗ — в `/opt/bazis/data/ai_journal_files/`
(оба в ежедневном бэкапе). Содержимое хранится при `AI_JOURNAL_STORE_CONTENT=1`
(по умолчанию); `0` — только метаданные и счётчики. Путь меняет `AI_JOURNAL_DB`.

```bash
cd /opt/bazis/basis
sudo -u bazis /opt/bazis/venv/bin/python main.py ai-journal stats --db /opt/bazis/data/ai_journal.sqlite3 --since 7d
sudo -u bazis /opt/bazis/venv/bin/python main.py ai-journal export --db /opt/bazis/data/ai_journal.sqlite3 -o /tmp/ai.zip
```
`export` без `--since` отдаёт всё новое с прошлой выгрузки (`--all` — вся база,
`--no-mark` — не сдвигать отметку). В ZIP: `tz.jsonl`, `errors.jsonl`,
`pairs.jsonl`, `images/`, `manifest.json`.

## Telegram-бот мониторинга
`bazis-bot.service` (`main.py bot`, юнит в `ops/`) пишет в группу «Сервер» и
отвечает только там. В `.env`: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALERT_CHAT_ID`
(ID группы — команда `/chatid`), при блокировке Telegram — `TELEGRAM_PROXY`.
Команды: `/help`, `/limits`, `/stats`, `/errors`, `/export` (только админы
группы). Алерты: Studio не отвечает, бюджет токенов 80/95%, баланс нейросети
< 20/5% от максимума, серия сбоев провайдера, новый тип ошибки; по понедельникам
сводка. Состояние — `/opt/bazis/out/bot_state.json`; логи — `journalctl -u bazis-bot`.

## Ключи и лимиты
`.env` на сервере (руками, в git не попадает): ключи ИИ-провайдеров + лимиты
демо `STUDIO_CHAT_RPM/RPD`, `STUDIO_TOKENS_PER_DAY`; `STUDIO_PUBLIC=1` задан в
юните. Расход токенов за день — `/opt/bazis/out/chat_tokens.json`.
