# Review of zazabag/mebel-bazis

Источник: https://github.com/zazabag/mebel-bazis

Проверено: ветка `main` и ветка `docs/research-and-vps-results`.

## 1. Что в репозитории есть

### Основная ветка

- `Анализ_автоматизации_Базис.md` — хороший первичный обзор API БАЗИС-скриптов.
- `universal_corpus.js` — универсальный генератор корпусной мебели через `objects3d.NewPanel`.
- `universal_table.js` — универсальный генератор столов.
- `CONFIGS_ТЗ_Коми.js` — набор ручных конфигов под позиции ТЗ Коми.
- `knowledge_base/SYSTEM_PROMPT.md` — системный промпт для разбора ТЗ в JSON.
- `knowledge_base/construction_principles.md` — правила конструирования корпусной мебели.
- `knowledge_base/standards.md` — нормы по размерам, зазорам, толщине, петлям, полкам.
- `knowledge_base/hardware_logic.md` — логика подбора фурнитуры.
- `knowledge_base/materials.json` — каталог материалов с `basis_name`.
- `knowledge_base/hardware.json` — каталог фурнитуры с `basis_name`.
- `knowledge_base/examples/*.json` — regression-примеры: шкаф, тумба, секционная тумба, столы, кашпо, стойка.

### Ветка `docs/research-and-vps-results`

Дополнительно содержит:

- `pipeline/` — Python-пайплайн `ТЗ -> LLM -> JSON -> JS скрипты`.
- `test_apilist/API_FINDINGS.md` — разбор реального OpenAPI Tasks API.
- `test_apilist/reference/Tasks_swagger.json` — сохраненная спецификация.
- `test_vps_ARCHIVED_v1/` — материалы Windows-VPS/headless-теста.
- `Архитектура_агента.md` и `Архитектура_агента_v1_VPS_2026-05-26.md` — архитектурные варианты.

## 2. Главное, что стоит перенести в наш план

### БАЗИС реально автоматизируется скриптами

Предыдущий анализ совпадает с нашим: БАЗИС-Мебельщик имеет JavaScript/TypeScript API,
умеет создавать блоки, панели, контуры, кромку, пазы, фурнитуру, читать файлы через
`require('fs')` и сохранять модели.

### Нужен не генератор произвольного JS, а стабильный импортер JSON

В старом репозитории pipeline генерирует готовые `.js` путем подстановки `CONFIG`
в `universal_corpus.js` или `universal_table.js`.

Для производственного контура надежнее другая архитектура:

```text
LLM/парсер -> валидируемый JSON -> один постоянный импортер БАЗИСа -> .b3d
```

Причина: постоянный импортер проще тестировать, версионировать и сертифицировать с технологом.
LLM не должна писать JS и не должна менять код БАЗИС-скрипта на каждый заказ.

### Knowledge base полезна

Документы `construction_principles.md`, `standards.md`, `hardware_logic.md`,
`materials.json`, `hardware.json` стоит использовать как начальную базу правил.

Особенно полезны:

- правила пролета полок;
- правила зазоров фасадов;
- петли по высоте фасада;
- направляющие по глубине корпуса;
- кромка по видимости торца;
- классификация покупной/производимой мебели;
- примеры ТЗ с ожидаемым JSON.

### Примеры можно сделать regression-набором

Минимальный набор будущих тестов:

- `shkaf_dokumenty.json`;
- `tumba_podkatnaya.json`;
- `tumba_moderatora.json`;
- `stol_pismenny.json`;
- `stol_peregovorny_popup.json`;
- `kashpo.json`;
- `stoyka_ohrany.json` как complex/manual case.

Наша задача: перевести эти примеры из старого формата `CONFIG`/`output_json`
в новый `BasisProductionModel`.

## 3. Что в старом коде ограничено

### `universal_corpus.js`

Плюсы:

- строит базовый корпус;
- поддерживает полки, перегородки, фасады, ящики, секции, цоколь;
- использует новый API `objects3d.NewPanel`;
- хорош как прототип геометрических правил.

Ограничения:

- материалы из `CONFIG` почти не применяются к объектам через `materialData`;
- кромка не наносится через `panelOperations.AddButt`;
- пазы/присадка/фурнитура не создаются;
- фасады это просто фронтальные панели, без петель/направляющих/замков;
- нет чтения JSON на входе, только inline `CONFIG`;
- нет `modelIOOperations.SaveModelToFile`;
- нет отчета импорта;
- нет валидации;
- позиционирование и ориентации требуют проверки в реальном БАЗИСе.

### `universal_table.js`

Плюсы:

- быстро строит столешницу и опоры;
- покрывает базовые письменные/переговорные столы.

Ограничения:

- металлические трубы моделируются панелями, не профилями/экструзиями;
- нет материалов/цветов/кромки;
- нет кабельных отверстий, POP-UP вырезов, фурнитуры;
- не сохраняет `.b3d`.

### `pipeline/`

Плюсы:

- есть готовая мысль `ТЗ -> LLM -> структурированный результат`;
- есть простой extractor и генератор скриптов.

Ограничения:

- `config.py` содержит placeholder API key, нельзя использовать как есть;
- extractor завязан на текстовый формат через LibreOffice/txt;
- генератор подставляет строки в JS через regex;
- структура JSON не совпадает с текущим `mebelgen` и будущим `BasisProductionModel`;
- нет JSON Schema и строгого валидатора.

## 4. Самая важная корректировка по АпиЛисту

В репозитории есть противоречие:

- `Архитектура_агента.md` говорит, что АпиЛист подтвержденно запускает клиентские JS-скрипты по REST.
- `test_apilist/API_FINDINGS.md` на основе реального Swagger уточняет: тип задачи
  `ExecuteClientScript` есть в enum, но публичного `POST` для создания такой задачи нет.

Сохраненный `Tasks_swagger.json` подтверждает публичные endpoints:

- `GET /api-cloud-tasks-public/tasks`;
- `GET/DELETE /api-cloud-tasks-public/tasks/{id}`;
- `POST /api-cloud-tasks-public/tasks/drawing-convert`;
- `POST /api-cloud-tasks-public/tasks/model-convert`;
- `GET /api-cloud-tasks-public/tasks/{id}/download-result`.

Вывод: для планирования считать `API_FINDINGS.md` более надежным источником.
АпиЛист пока нельзя считать подтвержденным исполнителем JS-скриптов. Он полезен для
конвертации/экспорта и требует отдельного вопроса в поддержку:

> Как программно создать задачу `ExecuteClientScript`? Есть ли отдельный endpoint,
> партнерский API или запуск доступен только через веб-кабинет?

## 5. Что показал VPS-тест

Из `test_vps_ARCHIVED_v1/RESULTS_2026-05-27.md`:

- БАЗИС-Мебельщик 2026.5.6 стартовал на Windows Server 2022 без GPU.
- VPS fallback технически жизнеспособен.
- CLI-флаги автозапуска скриптов не проверены.
- `application.SaveAs` без UI не доведен до конца.
- установка/активация БАЗИСа требует UI-шагов.
- silent install через `/VERYSILENT` не сработал.

Вывод: Windows-VPS можно держать как fallback, но это не быстрый happy path.
Для MVP безопаснее начинать с ручного запуска импортера в БАЗИСе и параллельно выяснять
автозапуск/облако.

## 6. Что делаем дальше с учетом этого repo

1. Перевести старые примеры `knowledge_base/examples/*.json` в новый `BasisProductionModel`.
2. Расширить наш импортер:
   - применять `materialData`;
   - наносить кромку по именованным сторонам;
   - сохранять через `modelIOOperations.SaveModelToFile`;
   - писать `import-report.json`.
3. Взять правила из knowledge base как первую версию production rules.
4. На машине с БАЗИСом сначала запустить `detect_basis_api.js`.
5. После диагностики версии API адаптировать импортер под фактическую среду.
6. Прогнать `wardrobe-basic.json`, затем старые примеры как regression set.
7. Отдельно открыть вопрос к БАЗИС-Центру по `ExecuteClientScript` в АпиЛисте.

