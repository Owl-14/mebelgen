# basis — ТЗ → JSON → модель в БАЗИС-Мебельщик

Превращает мебельное ТЗ (фото/спецификацию) в строгий JSON, по которому скрипт
строит готовую модель в **БАЗИС-Мебельщик 2026** (панели + ручки).

Навигация по документации:

- **[AGENTS.md](AGENTS.md)** — входная точка для агентов: где что лежит, рабочий
  процесс, ворота качества, грабли;
- [rules/architecture.md](rules/architecture.md) — карта модулей, поток данных, проверки;
- [rules/paramspec.md](rules/paramspec.md) — справочник ParamSpec и правила чтения ТЗ;
- [RULES.md](RULES.md) — правила построения (координаты, корпус, фурнитура);
- [STUDIO.md](STUDIO.md) — концепция Studio.

## Установка

```bash
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env        # вписать OPENAI_API_KEY (для convert)
```

## Конвейер

```
ТЗ (фото/текст/чертёж)
  → [LLM]  извлечение высокоуровневого ParamSpec (без координат)
  → [КОД]  генератор архетипа → project.json (panels[] с placement)
  → [ВАЛИДАТОРЫ] схема + геометрия + согласованность
  → [STUDIO] показ и правки в нашем движке (живое 3D + чертёж + BOM, бесплатно)
  → (только по явному запросу) импортёр в Мебельщике ИЛИ облако .cfrn→.b3d (ПЛАТНО)
```
Координаты считает детерминированный код, не модель (см. [RULES.md](RULES.md)).

**Правило по умолчанию: любой запрос «сделай мебель» завершается показом в Studio —
нашем локальном движке. API/облако БАЗИС не используется, пока пользователь явно
не попросит собрать `.b3d` (платно) или импортировать в десктопный Мебельщик.**

## Studio — локальный редактор (основной способ показа и правок)

Концепция и дорожная карта Studio 2.0 — в [STUDIO.md](STUDIO.md).

```bash
python main.py studio paramspecs/<spec>.json [--port 8765] [--out ../../out]
```

Браузерный редактор на нашем Python-конвейере, без облака и без десктопа:
живое 3D (присадки/фурнитура/открывание), чертёж (фронт+бок), проверки
(схема/встык/геометрия/.cfrn/присадки), BOM, правка габаритов/материала/секций
и параметров **любого архетипа** (селектор архетипа + динамическая форма),
raw-JSON редактор. Кнопки: «Сохранить» (spec + project.json), «.cfrn»,
«Собрать .b3d (~10₽)» — платная сборка только по явному клику и при зелёных
проверках. Статичный `viewer` (.html) остаётся для отправки файлом.

**Чат с ИИ** (панель в Studio): правки словами — «сделай глубину 600», «замени
цвет на дуб вотан», «фасады белые», «добавь ножки 100», «дверь открывается вверх».
LLM меняет только ParamSpec (координаты считает генератор), новая спека
валидируется схемой ДО применения, к каждой правке — сводка изменений и
«⟲ Откатить». Кнопка «⚕ Починить всё» — автоцикл: ИИ правит спеку по реальным
текстам ошибок проверок до зелёных бейджей (до 3 итераций).

Провайдеры (`src/spec_chat.py`, селектор в UI): `mock` (rule-based, офлайн),
`gigachat` (текст+vision, работает из РФ), OpenAI-совместимые `glm` / `kimi` /
`deepseek` / `openai`, `gemini`. Выбор по умолчанию — `SPEC_CHAT_PROVIDER`,
активируются наличием ключа (см. таблицу env ниже). Конвейер фото ТЗ:
vision-провайдер (`VISION_EXTRACT_PROVIDER`, напр. gigachat) выписывает факты →
сборщик (напр. glm) строит ParamSpec. Фото — 📎 / Ctrl+V / drag&drop.

**Декоры и материалы из производственной базы**: поле «Из базы» в Studio ищет
по ≈960 листовым позициям (`materials/baza_materiala.json`) со свотчами цвета;
клик — декор корпуса (при совпадении толщины пишется и артикул `board_article`),
кнопка «Ф» — отдельный декор фасадов (`facade_color`/`facade_article`). Вьювер
красит корпус и фасады раздельно (`src/decor_colors.py`); цвета показа условные
(реальные текстуры — MatBase БАЗИС). ТЗ с разными материалами корпуса/фасадов
попадает в модель сразу: промпт извлекает `facade_color`, генератор пробрасывает
в project.json, резолвер подбирает позиции базы.

## Команды

```bash
# ТЗ-изображение → JSON (Vision, нужен OPENAI_API_KEY)
python main.py convert spec.png -o projects/<project>.json

# ParamSpec → project.json (детерминированный генератор архетипа)
python main.py generate          paramspecs/<spec>.json -o projects/<project>.json

# Проверки
python main.py validate          projects/<project>.json [--geometry]
python main.py check-geometry     projects/<project>.json        # пересечения панелей
python main.py check-consistency  projects/<project>.json        # placement↔dimensions↔габарит
python main.py finish             projects/<project>.json        # схема + геометрия + авторазделение полок
python main.py orchestrate        paramspecs/<spec>.json         # generate → валидаторы → авторемонт → само-ревью

# Материалы (курируемый каталог + производственная база ≈5000 позиций)
python main.py materials                                          # статистика
python main.py materials --search "Дуб Вотан" --category "Листовой материал"
python main.py materials --resolve projects/<project>.json [--write]   # подбор реальных позиций → material_refs
python main.py materials --check projects/<project>.json          # сверка материалов проекта

# Показ и документы (бесплатно, локально)
python main.py studio    paramspecs/<spec>.json --out D:/claude/bazis/out   # редактор
python main.py viewer    projects/<project>.json -o model.html   # автономный 3D-файл
python main.py techview  projects/<project>.json                 # чертёж SVG фронт+бок
python main.py hardware  projects/<project>.json [--full]        # присадки/фурнитура
python main.py deliver   paramspecs/<spec>.json [--status draft] # лист согласования

# Сборка нативной модели .b3d через облако БАЗИС (device-independent, ПЛАТНО ~10₽/операция)
python main.py build-b3d         projects/<project>.json -o out.b3d
python main.py cloud info | list | model-convert … | drawing-convert …
python main.py cutting …                                          # раскрой (облако, платно)
```

## Тесты

```bash
python -m tests.regression   # ГЕЙТ перед merge: все спеки valid + goldens EXACT
python -m pytest tests/ -q   # юнит-тесты (сканируют все paramspecs/)
python qa/e2e_ai.py          # e2e ИИ-помощника (нужен живой провайдер с ключом)
```

## Структура

```
src/         весь конвейер: генераторы, валидаторы, Studio, чат, cfrn/b3d, смета,
             раскрой, чертёж, доставка — карта модулей в rules/architecture.md
schema/      furniture.schema.json, paramspec.schema.json — контракты JSON
prompts/     системные промпты (convert, узлы чата Studio в spec_chat/)
paramspecs/  входные ParamSpec (в т.ч. tz_*); .previews/ — кэш миниатюр каталога
projects/    сгенерированные проекты-примеры (.json)
materials/   catalog.json (курируемый) + baza_materiala.json (база ≈5000) + source/
scripts/     ImportFurnitureFromJSON.js (импорт в БАЗИС) + import_materials_base.py
rules/       источник истины: architecture, paramspec, core, generators, hardware,
             materials, studio, delivery, tumby
tests/       pytest + tests/regression.py (гейт) + goldens/
qa/          e2e_ai.py — e2e-матрица ИИ-помощника
landing/     обложка демо (planovo.pro/bazis)
```

## Импорт в БАЗИС

1. Скопировать `scripts/ImportFurnitureFromJSON.js` в `%USERPROFILE%\Документы\BazisN\Scripts`.
2. БАЗИС → **Скрипты** → `ImportFurnitureFromJSON.js` → выбрать `projects/<project>.json`.
3. Первый запуск попросит выбрать ручку/направляющую в каталоге — строка
   сохраняется в `hardware.*.furniture_encoded` для повторного импорта.

Скрипт создаёт панели из `panels[]` (по `placement`) и ручки из `hardware.handles`
(если `count > 0`). Направляющие/петли — когда подключён каталог поставщика, иначе
мастерами БАЗИС «Установка ящиков/дверей». Подробнее о фурнитуре — [../../docs/BASIS_AUTOMATION.md](../../docs/BASIS_AUTOMATION.md).

## Сборка .b3d через облако (device-independent)

Без десктопа: `project.json → src/cfrn.py собирает .cfrn → облако `model-convert
CfrnToB3d` → нативный `.b3d`. Команда `build-b3d`. Каждая конвертация платная (~10₽,
нужен `BAZIS_API_KEY`). Раскладка деталей в `.cfrn` сверена с эталоном родного шкафа
БАЗИС (контур horizont `{x:ширина, y:глубина}`, привязка по задней грани `z2`) —
модель собирается корректно. Просмотр результата — `D:\bazis\viewer.exe` (БАЗИС-Просмотр 3D).

## Материалы

Два слоя: курируемый `materials/catalog.json` (типовые позиции по умолчанию) и
производственная база `materials/baza_materiala.json` (≈5000 реальных позиций с
артикулами/ценами/размерами, импорт из xlsx через `scripts/import_materials_base.py`).
Поиск реальных позиций — `python main.py materials --search "<запрос>"`. API в
`src/materials.py`: `load_base / search_base / find_board`.

**Подбор для проекта** — `materials --resolve project.json [--write]`: заполняет
`material_refs` реальными позициями. Плита/задник/кромка — одна позиция (по толщине,
коду декора или цвету); фурнитура (ручки/направляющие/петли/опоры/замки) — шорт-лист
кандидатов из нужной группы (конкретный артикул выбирает технолог). Слабое совпадение
не подменяется вслепую — помечается `resolved:false` с кандидатами. Сверка точных имён
БАЗИС (`basisName`) — при доступной лицензии (AKD-12).

## Переменные окружения

| Переменная | Описание |
|---|---|
| `BAZIS_API_KEY` | ключ БАЗИС-Облака (для `build-b3d`, `cloud`, раскрой) — ПЛАТНЫЕ операции |
| `SPEC_CHAT_PROVIDER` | провайдер чата Studio по умолчанию: `mock`\|`gigachat`\|`glm`\|`kimi`\|`deepseek`\|`openai`\|`gemini` |
| `VISION_EXTRACT_PROVIDER` | кто читает фото ТЗ в конвейере (напр. `gigachat`), сборку делает SPEC_CHAT_PROVIDER |
| `GIGACHAT_AUTH_KEY` | GigaChat (Сбер): текст+vision, работает из РФ (+`GIGACHAT_SCOPE/MODEL/VISION_MODEL/VERIFY/CA`) |
| `GLM_API_KEY` | GLM/Zhipu `glm-4.5-flash` (бесплатный, thinking отключён) |
| `KIMI_API_KEY` | Kimi/Moonshot (платный) |
| `DEEPSEEK_API_KEY` | DeepSeek |
| `OPENAI_API_KEY` | OpenAI `gpt-4o` (чат и `convert`) |
| `GEMINI_API_KEY` | Gemini (из РФ заблокирован) |
| `LLM_API_KEY/BASE_URL/MODEL/VISION_MODEL/JSON_MODE` | переопределение любого OpenAI-совместимого провайдера |
| `PARAMSPEC_PROVIDER` | провайдер извлечения ParamSpec для `convert`/`ingest` |
| `BAZIS_VIEWER` | путь к БАЗИС-Просмотру (по умолчанию `D:\bazis\viewer.exe`) |
