# Runbook studio.akeda.ru (AKD-264 / MEB-106)

Сервер **80.66.89.3**, SSH `root` с ключом `bazis_deploy`. Раскладка:
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

## Ключи и лимиты
`.env` на сервере (руками, в git не попадает): ключи ИИ-провайдеров + лимиты
демо `STUDIO_CHAT_RPM/RPD`, `STUDIO_TOKENS_PER_DAY`; `STUDIO_PUBLIC=1` задан в
юните. Расход токенов за день — `/opt/bazis/out/chat_tokens.json`.
