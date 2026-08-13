# Cutting Public API: безопасный контракт и E2E preflight

Этот документ описывает интеграцию после готового `.b3d`. Cutting — внешний
контур, а его мутации могут быть платными. Ни unit-тесты, ни offline harness не
являются доказательством реального производственного E2E.

Источник контракта: официальный OpenAPI 3.0.1 snapshot от 2026-06-30,
сохранённый в истории репозитория как
`5bc53df:docs/bazis_cloud_cutting_swagger.json`. Snapshot нужен только для
исследования; runtime не читает файл из Git history и не обращается к Swagger.

## Порядок тестового заказа

Строгий порядок в `qa/fixtures/cutting_test_order.json` и
`qa/cutting_contract_harness.py`:

1. `POST /orders` — создать неперсональный тестовый заказ.
2. `POST /cad-models` — загрузить один `.b3d`; multipart-поле называется
   **`models`**, параметры `orderId`, `count`, `cutModels`.
3. `GET /cad-models/{id}/materials` — получить исходные строки материалов.
4. Проверить точное равенство утверждённому fixture и полноту связок.
5. `POST /cad-models/{id}/set-link-materials` — отправить только проверенные
   `originalMaterialFullName`, `materialType`, `linkedMaterialFullName`.
6. `POST /orders/{id}/run-cutting`.
7. `POST /orders/{id}/run-generation-production-files`.
8. С ограниченным timeout читать `GET /orders/{id}/production-files-url`.

Нельзя угадывать связь материала, автоматически выбирать похожее название или
переходить к раскрою при несовпадении списка материалов.

## Обязательные клиентские предусловия

OpenAPI помечает многие nullable-поля заказа необязательными, поэтому ниже не
выдумывается серверная обязательность. Клиент жёстко проверяет безопасные
предусловия, без которых запрос неоднозначен:

- положительные `orderId`, `modelId`, `taskId` и `count`;
- существующий непустой файл одного из расширений из OpenAPI, с лимитом размера;
- upload использует `models[]`, не legacy-поле `file`;
- непустой массив material links, все три поля в каждой записи, тип `0..5`;
- внешний доступ только при `allow_live`;
- каждый POST только при `allow_mutations`, в рамках точного бюджета и с
  непустым idempotency key.

API не документирует серверный idempotency header. Поэтому ключ защищает от
повтора внутри одного запуска. После timeout/transport failure мутация имеет
состояние `ambiguous`: автоматический повтор запрещён, сначала оператор сверяет
удалённый заказ. Скрытых retry у клиента нет.

## Error taxonomy

| Код | Значение | Повтор |
|---|---|---|
| `cutting.live_guard.required` | live-доступ не разрешён | только после разрешения |
| `cutting.cost_guard.required` / `.exhausted` | нет разрешения или исчерпан бюджет POST | нет |
| `cutting.idempotency.required` / `.collision` / `.ambiguous` | отсутствует, переиспользован иначе или результат прошлого POST неизвестен | нет до аудита |
| `cutting.transport.timeout` / `.unavailable` | GET не дошёл/не ответил | допустим операторский повтор |
| `cutting.transport.ambiguous` | POST мог дойти до сервера | нет до сверки remote state |
| `cutting.http.auth` | 401/403 | после исправления доступа |
| `cutting.http.validation` | 400/422 | после исправления fixture |
| `cutting.http.not_found` | 404 | для URL архива допустим bounded poll |
| `cutting.http.conflict` | 409 | только после проверки состояния |
| `cutting.http.rate_limited` / `.server` | 429 / 5xx | только GET автоматически не повторяется, но помечен retryable |
| `cutting.response.invalid_json` / `.contract` | ответ нарушил ожидаемую форму | нет, нужен аудит контракта |
| `cutting.long_task.failed` / `.timeout` | длинная задача упала/не уложилась в deadline | операторское решение |

Пользователю не отдаётся upstream `detail`/exception text. Trace содержит только
`trace_id`, step, HTTP method, route-template без ID, status, duration, outcome,
error code и использованный счётчик мутаций. В нём нет ключа, JSON body, названий
материалов, локального пути, order/model ID или signed download URL.

## Offline contract harness

Из `tools/basis`:

```powershell
python qa/cutting_contract_harness.py
```

Команда использует только `_ScriptedSession`, локальный
`qa/fixtures/wardrobe_demo_production.b3d` и синтетические material links. В
результате явно стоит `mode=offline_contract`. Это доказательство сериализации,
guard-ов, порядка вызовов и sanitization, но не существования заказа или архива
в БАЗИС-Облаке.

## Разрешённый live smoke (не запускать без отдельного разрешения)

После выдачи отдельного разрешения, тестового API key и утверждённого
неперсонального fixture с реальными source→linked material names команда имеет
вид:

```powershell
$env:BAZIS_API_KEY = '<test Cutting key>'
python qa/cutting_contract_harness.py --live --fixture '<absolute path to approved live fixture.json>' --allow-mutations --max-mutations 5 --idempotency-prefix 'MEB-140-<unique-approved-run-id>' --production-timeout 600 --production-interval 5
```

Один запуск разрешает ровно пять POST: order, upload, links, run-cutting и
production generation. Повтор с тем же prefix в новом процессе не считается
безопасным, потому что серверная идемпотентность не документирована.

Текущий blocker live E2E: в задаче нет отдельного разрешения на Cutting API,
тестового `BAZIS_API_KEY`, подтверждённой лицензии/тарифа и утверждённой таблицы
реальных material links для загружаемого `.b3d`. Синтетические значения offline
fixture намеренно непригодны для live. Пока все четыре условия не выполнены,
нельзя честно получить order/model IDs, Cutting result или production archive.
