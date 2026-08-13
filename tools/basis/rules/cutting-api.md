# Cutting Public API: безопасный контракт и E2E preflight

Cutting — внешний потенциально платный производственный контур. Обычный CLI и
CI не имеют live-команды: `main.py cutting info` только печатает контракт, а
`qa/cutting_contract_harness.py` использует scripted session без сокетов.
Offline-тесты никогда не являются доказательством реального E2E.

Источник endpoint-контракта — официальный OpenAPI 3.0.1 snapshot от
2026-06-30, сохранённый в истории как
`5bc53df:docs/bazis_cloud_cutting_swagger.json`. Runtime не читает snapshot и
не обращается к Swagger.

## Транспорт и credentials

- production origin жёстко закреплён как `https://cloud.bazissoft.ru` без
  path, query, credentials, любого явного порта или redirect;
- production `apiKey` нельзя отправить по HTTP или на другой host;
- тестовый endpoint разрешён только через явный allowlist и отдельный
  `test_api_key`; production key в таком клиенте отклоняется;
- `verify=True` и `allow_redirects=False` передаются каждому запросу;
- injected session вообще не может target pinned production origin, а
  allowlisted test transport никогда не получает `live_evidence_allowed`.

Live evidence допустимо только для внутренней `requests.Session`, pinned
transport и зарегистрированного ledger-run с `mode=live`.

## Строгий порядок fixture

`qa/fixtures/cutting_test_order.json` задаёт полный offline-порядок:

1. `POST /orders`;
2. `POST /cad-models` с multipart-полем `models`, `orderId`, `count`,
   `cutModels`;
3. `GET /cad-models/{id}/materials`;
4. строгая сверка source materials с утверждённым fixture;
5. `POST /cad-models/{id}/set-link-materials`;
6. `GET /cad-models/{id}/cutting-materials`;
7. `POST /orders/{id}/run-cutting`;
8. `POST /orders/{id}/run-generation-production-files`;
9. bounded poll `GET /orders/{id}/production-files-url`;
10. `GET /cad-models/{id}/cutted-materials` и строгий post-audit.

Material links проходят единый контракт MEB-139 (`material_link_contract.py`):
точное name/article/sheet сопоставление, подтверждённые исключения, полнота
листовых материалов и post-audit. Fuzzy/LLM-подбор запрещён. Повтор source-link
запрещён; конфликтующие дубликаты с разными targets также отклоняются.

## Durable mutation ledger

Каждая мутация до сети атомарно записывается в файловый SQLite ledger. Состояния
операции: `prepared → ambiguous → success`. Переход в `ambiguous` фиксируется
до фактического request, поэтому падение процесса не создаёт ложного разрешения
на повтор. `BEGIN IMMEDIATE`, WAL и `synchronous=FULL` обеспечивают
межпроцессную сериализацию; одинаковый idempotency key блокируется между
клиентами и процессами.

Live ledger не выбирается caller-ом. Единственный machine approval читается из
canonical path `C:\ProgramData\Akeda\Cutting\approved-live.json` (Linux:
`/etc/akeda/cutting/approved-live.json`). SHA-256 байтов config, включающего
approved fixture SHA-256, однозначно задаёт ledger identity и путь
`ledgers/<approval-digest>.sqlite3`. Новый произвольный SQLite не может открыть
live run. Для одного approval digest ledger атомарно создаёт ровно один opaque
`run_<32 hex>`; caller не передаёт run ID и не может использовать его как канал
PII. Повторное открытие сохраняет исходный absolute deadline и mutation count.

Если результат неизвестен, оператор обязан сначала сверить remote state и
зафиксировать reconciliation с хешем evidence. Даже `not_applied` не разрешает
повтор старого ключа — новая попытка требует нового утверждённого ключа.
Серверный idempotency header не документирован.

`max_mutations` — durable лимит утверждённого ledger-run, а не надёжная оценка
денежной стоимости. Он ограничивает число уникально подготовленных POST между
процессами, но реальный тариф/стоимость должен быть отдельно подтверждён
оператором.

## Deadline и ошибки

Live/offline-authorized клиент требует положительный `overall_timeout`. Перед
каждым request вычисляется оставшееся общее время; connect/read timeout не
может его превышать. Истёкший deadline блокирует запрос. Для POST transport
failure или возврат после deadline означает `cutting.transport.ambiguous` и
обязательную remote reconciliation.

Основные стабильные коды:

| Код | Значение |
|---|---|
| `cutting.live_guard.required` | нет явного разрешения на доступ |
| `cutting.transport.attestation` | ledger mode не соответствует подтверждённому transport |
| `cutting.deadline.required` / `.exceeded` | нет общего deadline или он истёк |
| `cutting.ledger.required` / `.run_missing` | нет durable approval ledger |
| `cutting.cost_guard.required` / `.exhausted` | мутации не разрешены / durable лимит исчерпан |
| `cutting.idempotency.required` / `.collision` / `.duplicate` / `.ambiguous` | невалидный, конфликтующий, использованный или неизвестный ключ |
| `cutting.transport.timeout` / `.unavailable` | read-only transport failure |
| `cutting.transport.ambiguous` | POST мог дойти до сервера |
| `cutting.http.auth` / `.validation` / `.not_found` / `.conflict` | 401/403, 400/422, 404, 409 |
| `cutting.http.rate_limited` / `.server` / `.redirect_blocked` | 429, 5xx, 3xx |
| `cutting.response.invalid_json` / `.contract` | ответ нарушил контракт |
| `cutting.long_task.failed` / `.timeout` | production task завершилась ошибкой / не уложилась |

Upstream body, exception text и secrets не возвращаются пользователю.
Внутренний trace ID — 32-символьный UUID hex, не принимаемый извне. Sanitized
trace/evidence содержит только server-generated opaque run ID, timestamps, transport attestation, fixture/model
SHA-256, route-template и outcome; в нём нет API key, JSON body, customer names,
локальных путей, remote IDs или signed URL.

## Offline contract harness

Из `tools/basis`:

```powershell
python qa/cutting_contract_harness.py
```

Harness использует synthetic fixture, отдельный test key, allowlisted `.invalid`
origin и временный файловый ledger. Результат всегда имеет
`mode=offline_contract` и `live_transport_confirmed=false`.

## Разрешённый live smoke — только после отдельного approval

Operator entrypoint намеренно вынесен из обычного CLI/CI. Команда ниже —
шаблон для будущего отдельно разрешённого запуска; в MEB-140 она **не
выполнялась**:

```powershell
$env:BAZIS_API_KEY = '<approved test Cutting key>'
python operator/cutting_live_smoke.py `
  --authorization 'MEB-140-CUTTING-LIVE-APPROVED'
```

До команды независимый approver устанавливает canonical config с exact schema:
`contractVersion`, `authorizationScope`, `approvedForLive`, absolute
`fixturePath`, `fixtureSha256`, `maxMutations=5`, positive `overallTimeout` и
`productionInterval`. CLI не принимает их как аргументы. До первой mutation
entrypoint повторно читает config, fixture и model, проверяет hashes,
`scope=approved-live-cutting-smoke`, отсутствие synthetic sentinels, canonical
ledger identity и внутренний pinned HTTPS session. Transport/session immutable
после construction. Production archive принимается только как чистый absolute
credential-free HTTPS URL с default port.

Точный текущий blocker: отсутствуют (1) отдельное разрешение на платный Cutting
smoke, (2) выделенный тестовый `BAZIS_API_KEY`, (3) подтверждённая лицензия и
тариф/стоимость, (4) несинтетический `.b3d` fixture с отдельно утверждённым
SHA-256 и строгой таблицей реальных source→linked материалов и (5) установленный
независимым approver-ом canonical machine config/ledger identity. Поэтому реальных order/model/run IDs,
production archive и live E2E evidence нет и не заявляется.
