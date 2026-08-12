# Проверки по типам задач

Корневой [`AGENTS.md`](../AGENTS.md) обязателен всегда. Ниже — дополнительные
маршруты для конкретных классов задач.

## Общий минимум

| Этап | Действие |
|---|---|
| Перед правкой | status, fetch, отдельная ветка, ближайший `AGENTS.md` |
| Во время | минимальный diff, целевые тесты |
| Перед передачей | полный гейт подсистемы, `git diff --check`, просмотр diff |
| Прод | не трогать без отдельного запроса |

## Публикация результата

Для любой задачи команда «выгружай/публикуй» включает единый release-гейт:

```text
target/full tests → diff review → commit → push → PR → CI → merge master
→ backup/version checkpoint → deploy merge SHA → health/version/smoke
```

Запрещено считать публикацией push рабочей ветки или deploy несмерженного SHA.
Если задача не готова к одному из этапов, нужно назвать конкретный блокер и
оставить production на последнем принятом SHA.

## 1. Studio и визуальный интерфейс

Область:

- `tools/basis/src/studio.py`;
- `tools/basis/src/webviewer.py`;
- `tools/basis/landing/`, `tools/basis/vendor/`;
- связанные тесты и `rules/studio.md`.

Сначала читать:

- `tools/basis/AGENTS.md`;
- `tools/basis/rules/studio.md`;
- `tools/basis/STUDIO.md`;
- архитектуру в `rules/architecture.md`.

Обязательные проверки:

```bash
cd tools/basis
python -m pytest tests/test_studio.py tests/test_webviewer.py -q
python -m pytest tests/ -q
python -m tests.regression
python main.py studio paramspecs/wardrobe_demo.json --no-open --port 8765 --out <temp-out>
```

Ручной smoke:

- каталог и открытие изделия;
- 3D-вид, ракурсы, размеры и анимация;
- чат/импорт ТЗ, если затронуты;
- сохранение/версии/дубликат;
- mobile и desktop viewport;
- ошибки API и пустые состояния.

Для PR нужны скриншоты до/после. Не править production-файл напрямую.

## 2. ParamSpec, генераторы и геометрия

Область:

- `schema/paramspec.schema.json`;
- `src/paramspec.py`, `src/generators/*`;
- `paramspecs/*`, `projects/*`;
- проверки геометрии.

Сначала читать `rules/paramspec.md`, `rules/core.md`,
`rules/generators.md`.

Проверить:

```bash
cd tools/basis
python main.py generate paramspecs/<case>.json
python main.py finish projects/<case>.json
python -m pytest tests/ -q
python -m tests.regression
```

Нельзя вручную подменять placement, чтобы скрыть ошибку генератора. Старые
спеки должны либо продолжить работать, либо получить явную миграцию.

## 3. Присадки, фурнитура, `.cfrn` и `.b3d`

Область:

- `src/hardware.py`, `hardware_geometry.py`, `fasteners3d.py`;
- `src/cfrn.py`, `b3d_*`, `build_b3d.py`;
- соответствующие rules/tests/fixtures.

Сначала читать `rules/hardware.md`, `docs/CFRN_HOLES_FORMAT.md` и заметки по
реверсу.

Проверить не только placement:

- drilling/completeness;
- `check_cfrn_encoding`;
- round-trip `.cfrn/.b3d`, если задача о production-кодировании;
- регистрацию нового purpose во всех потребителях;
- регрессию и целевые fixtures.

Платные облачные сборки запускать только по явному разрешению.

## 4. Материалы и декоры

Область:

- `src/materials.py`, `materials_policy.py`, `decor_colors.py`;
- `materials/*`, правила и тесты.

Правила:

- не выдумывать артикулы;
- не форматировать базу целиком;
- не менять идентификаторы без миграции;
- проверять fallback и неоднозначные запросы.

Запускать целевые тесты материалов/декоров и полный basis-гейт.

## 5. ИИ-чат, провайдеры и импорт ТЗ

Область:

- `src/spec_chat.py`, `providers.py`, `orchestrator.py`;
- prompts, схемы, import routes и тесты.

Правила:

- не логировать prompt вместе с секретами/персональными данными;
- не коммитить реальные API-ключи;
- сохранять детерминированный mock-путь;
- учитывать public rate/token limits;
- не делать сетевой E2E платного провайдера без разрешения.

Проверить mock, schema validation, обработку timeout/error и полный basis-гейт.

## 6. Документация и исследования

- Сверять команды с текущим кодом.
- Помечать гипотезы и результаты экспериментов.
- Не выдавать Swagger/исследование за гарантированный production-контракт.
- Проверять ссылки и пути.
- Документационная задача не должна незаметно менять runtime-код.

Минимум: `git diff --check` и ручная проверка ссылок/команд.

## 7. Зависимости и CI

Область: requirements, lock-файлы, `.github/workflows/*`.

- Обновлять только нужную зависимость.
- Объяснять причину и совместимость Python/ОС.
- Не ослаблять тесты ради зелёного CI.
- Проверять чистую установку и оба basis-гейта.
- Изменение CI требует review владельца репозитория.

## 8. Инфраструктура и deploy

Область: `tools/basis/ops/*`, nginx/systemd, сертификаты, сервер.

До изменения:

- прочитать `ops/runbook.md`;
- проверить `/healthz`, `/version`, service status и backup;
- сохранить текущий SHA для отката;
- убедиться в явном разрешении.

После изменения:

- проверить nginx/systemd syntax до reload;
- не перезагружать VPS без необходимости;
- выполнить health/version/log/smoke;
- записать, что изменено на сервере;
- убедиться, что изменение отражено в Git, а не осталось ручным.

## 9. Срочная production-ошибка

Диагностика не равна разрешению на исправление.

1. Снять симптомы, время, SHA `/version`, логи и affected scenario.
2. Воспроизвести локально.
3. Создать отдельную fix-ветку.
4. Добавить регрессионный тест.
5. Пройти review/CI.
6. Получить подтверждение deploy.
7. Проверить и при необходимости откатить.
