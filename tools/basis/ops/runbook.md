# Runbook демо studio.akeda.ru (AKD-264)

Сервер **80.66.89.3**, SSH `root` с ключом `bazis_deploy`. Раскладка:
`/opt/bazis/basis` — приложение (копия tools/basis, НЕ git), `/opt/bazis/venv`,
`/opt/bazis/out` — артефакты и журнал токенов, `/opt/bazis/backups`.
Сервис — `bazis.service` (см. [bazis.service](bazis.service)), nginx —
[nginx-bazis.conf](nginx-bazis.conf) + certbot (автопродление certbot.timer).

## Обновить код
`./ops/deploy-update.sh` из tools/basis (пишет DEPLOY_SHA, тарит код, рестартит
сервис, показывает /version). Изделия посетителей, .env и out не трогаются.

## Проверить состояние
- `curl https://studio.akeda.ru/healthz` — `{"ok":true,...}`;
- `curl https://studio.akeda.ru/version` — какой SHA развёрнут, public-режим;
- `systemctl status bazis`, `journalctl -u bazis -n 50`.

## Откат
Развернуть предыдущий SHA: `git checkout <sha> && ./ops/deploy-update.sh`
(или распаковать прошлый /tmp/basis_update.tgz на сервере и рестартнуть).

## Бэкапы изделий
[backup-paramspecs.sh](backup-paramspecs.sh) → `/opt/bazis/backups`, cron 04:00,
глубина 14 суток. Восстановление: `tar xzf paramspecs-<дата>.tgz -C /opt/bazis/basis`.

## Ключи и лимиты
`.env` на сервере (руками, в git не попадает): ключи ИИ-провайдеров + лимиты
демо `STUDIO_CHAT_RPM/RPD`, `STUDIO_TOKENS_PER_DAY`; `STUDIO_PUBLIC=1` задан в
юните. Расход токенов за день — `/opt/bazis/out/chat_tokens.json`.
