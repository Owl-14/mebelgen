# Архитектура: карта модулей, поток данных, проверки

Куда смотреть, когда нужно что-то найти или поменять. Один источник правды —
ParamSpec; координаты всегда считает детерминированный Python-код.

## Поток данных

```
ТЗ (фото/текст) ──convert/чат──► ParamSpec (paramspecs/<x>.json)
    │                                │
    │            generate_from_paramspec (src/generators/registry.py)
    │                                ▼
    │                        project.json  (panels[] c placement, drawers, hardware)
    │                                │
    │        ┌── materials_policy (дозаполнение дефолтов, warnings)
    │        ├── resolve_project_materials → material_refs (артикулы базы)
    │        ├── compute_drilling (присадки ЧПУ) / hardware_geometry (3D механизмов)
    │        ▼
    │   ПРОВЕРКИ (6 бейджей, см. ниже)
    │        ▼
    ├── Studio (src/studio.py, локальный HTTP) — показ/правки/чат/смета/каталог
    ├── viewer .html (src/webviewer.py) — автономный файл
    ├── techview SVG (src/techview.py) — чертёж фронт+бок, деталировка
    ├── nesting (src/nesting.py) — раскрой по листам, estimate (src/estimate.py) — смета
    ├── delivery (src/delivery.py) — лист согласования (версии, PDF/PNG)
    └── cfrn.py → .cfrn ──(облако БАЗИС, ПЛАТНО ~10₽)──► .b3d  (build_b3d.py)
                                     └── b3d_verify.py — паритет .b3d ↔ Studio-модель
```

## Модули src/ (что где лежит)

| Модуль | Ответственность |
|---|---|
| `paramspec.py` | схема/валидация ParamSpec (`validate_paramspec`) |
| `paramspec_versioning.py`, `paramspec_migration.py` | v1 envelope: отделение domain/catalog metadata, tolerant-read/strict-write и read-only dry-run equivalence без фиктивного v2 |
| `generators/registry.py` | `generate_from_paramspec` — диспетчер архетипов |
| `generators/{corpus,cabinet,shelving,drawer_unit,door_unit,desk,round_table,composite}.py` | генераторы архетипов (см. rules/generators.md) |
| `generators/helpers.py` | фасадная полоса, колонки, общие расчёты |
| `generators/base.py` | общий каркас (корпус, задник, цоколь, опоры, штанга) |
| `overrides.py` | точечные правки деталей поверх генератора (`spec.overrides[]`) |
| `edit_engine.py` | чистый детерминированный резолвер semantic add/move полок и перегородок → legacy-compatible overrides + полный quality-gate; координаты от LLM отклоняет |
| `materials_policy.py` | дозаполнение материалов/фурнитуры дефолтами из ТЗ (rules/materials.md) |
| `materials.py` | производственная база (≈5000 позиций): `search_base`, `by_article`, `list_sheet_decors`, `resolve_project_materials` |
| `decor_colors.py` | декор → цвет показа (палитра корпуса/фасадов) |
| `hardware.py` | `compute_drilling` — координаты присадок (система 32, rules/hardware.md) |
| `hardware_geometry.py` | видимые тела механизмов (направляющие, петли, ручки) для 3D |
| `fasteners3d.py` | `_PURPOSE_KIND`: purpose присадки → 3D-метиз (OBJ) в .cfrn/.b3d |
| `consistency_check.py` | встык: пересечения объёмов, placement↔dimensions |
| `geometry_check.py` | геометрия placement (нахлёсты, выход за габарит) |
| `drilling_check.py` | сверловка: отверстие в теле панели, шаг 32, планки на уровне полок |
| `completeness_check.py` | полнота: каждая деталь закреплена, заявленное (полки/штанги/опоры) построено |
| `production_gate.py` | атомарный гейт AI/reducer-кандидата: Pydantic → JSON Schema → генерация → consistency/geometry/bounds → CFRN → drilling/system 32/purpose registry → completeness/materials; возвращает `CheckReport`, не применяя красную ревизию |
| `bounds_check.py` | структурные панели внутри заявленного W×D×H; overlay-фасад/задник может выйти только по Z и не дальше своей заявленной толщины |
| `cfrn.py` | project.json → `.cfrn` (родная ЛЕВОсторонняя конвенция БАЗИС, разворот фасадами к камере) + `check_cfrn_encoding` / `check_cfrn_holes` |
| `b3d_format.py`, `b3d_verify.py` | чтение .b3d, паритет .b3d ↔ модель |
| `build_b3d.py`, `cloud_api.py`, `cloud_cutting.py` | облако БАЗИС (ПЛАТНО, только по явной просьбе); pinned Cutting transport и API contract — `rules/cutting-api.md` |
| `cutting_ledger.py`, `cutting_operator_trust.py`, `cutting_preflight.py`, `material_link_contract.py` | approval-digest canonical ledger/operator boundary, общий offline/live preflight и строгий article/sheet/post-audit material-link contract |
| `webviewer.py` | `viewer_payload` + `SCENE_JS` (общий three.js-движок: панели, присадки, метизы, анимация открывания, ракурсы `setView`, снапшоты) |
| `studio.py` | Studio: HTTP-сервер, страница редактора, каталог изделий (+SVG-аксонометрия карточек `/thumb/`), версии, экспорт-центр (rules/studio.md) |
| `spec_chat.py` | ИИ-чат: провайдеры (mock/gigachat/glm/kimi/deepseek/openai/gemini), конвейер фото-ТЗ vision→сборка, анти-инъекция; обычные правки принимаются только как типизированные операции |
| `edit_operations.py` | Pydantic discriminated union AI-операций + атомарный reducer в ParamSpec v1; проверяет target/preconditions и не вычисляет геометрию |
| `studio_graph.py` | feature-flagged LangGraph-оркестрация AI-команд; состояние, checkpoints, отмена/возобновление и Protocol-адаптеры к детерминированному движку |
| `techview.py`, `sheet_layout.py` | чертёж SVG (фронт+бок+деталировка), аллокатор выносок |
| `nesting.py` | bin-packing раскрой (KERF=4) |
| `estimate.py` | смета материалов по ценам базы |
| `delivery.py` | лист согласования: версии, статусы, PDF/PNG (rules/delivery.md) |
| `orchestrator.py` | generate → проверки → авторемонт → само-ревью |
| `converter.py`, `providers.py`, `oldspec.py` | ТЗ-изображение → JSON (Vision), старый формат спеки |

Прочее: `schema/` — JSON-контракты; `prompts/` — системные промпты (чат, convert);
`paramspecs/` — входные спеки (+`.previews/` — кэш миниатюр каталога);
`projects/` — сгенерированные project.json; `materials/` — каталоги;
`qa/e2e_ai.py` — e2e-матрица ИИ (нужен живой провайдер); `landing/` — обложка демо.

## Проверки (бейджи Studio) и что они значат

| Бейдж | Модуль | Ловит |
|---|---|---|
| схема | paramspec.validate_paramspec | невалидный ParamSpec (лимиты: габариты 50..10000, ящиков ≤20, дверей ≤2) |
| встык | consistency_check | объёмные пересечения >0.5 мм, расхождение placement/dimensions |
| геометрия | geometry_check | нахлёсты/выходы за габарит |
| .cfrn | cfrn.check_cfrn_encoding | ошибки кодирования в родную конвенцию |
| присадки | cfrn.check_cfrn_holes | присадки в .cfrn ≠ compute_drilling |
| сверловка | drilling_check | отверстие вне тела панели, нарушение системы 32 |

Дополнительно обязательный production gate проверяет bounds, purpose registry,
completeness и materials отдельными шагами. `b3d_verify` остаётся паритетом после
платной сборки и в offline-матрицу не входит.

**Грабли, проверенные опытом:**

- placement-чек НЕ гарантирует корректный `.b3d`: кодирование в .cfrn может дать
  нахлёсты при правильном placement. Всегда смотреть `check_cfrn_encoding`;
  полная правда — round-trip `b3d→cfrn` из облака.
- Новый `purpose` присадки регистрируется минимум в 3 местах:
  `hardware.py` (создание), `fasteners3d.py::_PURPOSE_KIND` (тело в .cfrn/.b3d),
  `webviewer.py::fastenerGroup` (3D в Studio). Проверки drilling/completeness
  должны его понимать, иначе красный регресс.
- Правки Python не подхватываются работающим Studio — перезапустить сервер
  (страница и SCENE_JS сидят в памяти процесса).
- Система 32 (производство): присадка Ø8 (шкант+эксцентрик), шаг 32, пропил 4.
  Конфирматы Ø7 и «пары 64» — устаревший реверс, не использовать.

## Тесты и ворота качества

```bash
cd tools/basis
python -m qa.engine_checks           # обязательная matrix archetype/fixture/gate
python -m pytest tests/ -q          # юнит + goldens (итерирует ВСЕ paramspecs/)
python -m tests.regression          # ГЕЙТ перед merge: 37 спек valid + 8 golden EXACT
```

Manifest матрицы: `qa/fixtures/engine_checks/manifest.json`. Команда полностью
offline, не обновляет goldens и возвращает ненулевой exit code при красном gate
или неполном покрытии обязательных архетипов/вариантов. Features manifest
проверяются точным сравнением с признаками, выведенными из generated project.

- Goldens (`tests/goldens/`) — эталонные panels. Обновлять ЗАМЕНОЙ блока `panels`
  из свежей генерации (не руками), только когда изменение геометрии осознанное.
- `pytest tests/` может краснеть из-за чужой битой спеки в `paramspecs/`
  (тесты сканируют каталог) — смотреть, чья спека падает, прежде чем чинить код.
- e2e ИИ: `python qa/e2e_ai.py` против живого Studio (нужны ключи провайдера).

## Координатные конвенции (краткая шпаргалка)

- **Мир БАЗИС (project.json)**: X — ширина вправо, Y — высота вверх, Z — глубина
  назад (0 — перёд). Панель = бокс `{x1..z2}` (rules/core.md).
- **Показ (Studio/viewer)**: правосторонняя, Y-вверх, `Z' = Zmax − Z` (фронт на
  зрителя). Только визуализация.
- **.cfrn/.b3d**: родная ЛЕВОсторонняя конвенция БАЗИС + разворот 180° вокруг Y
  (фасады к камере БАЗИС-Просмотра). Файл станка НЕ равен сцене показа.
