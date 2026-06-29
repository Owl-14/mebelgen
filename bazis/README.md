# БАЗИС JSON Converter

Конвертер мебельной документации (фото + схема + спецификация) в структурированный JSON для последующей генерации модели в **БАЗИС-Мебельщик**.

**Главная инструкция (общая):** [INSTRUCT.md](INSTRUCT.md) — БАЗИС-координаты, `placement`, ориентации, общий расчёт, импорт.  
**Плейбук агента (всегда):** `.cursor/rules/agent-playbook.mdc` — что делать по шагам при любом ТЗ.  
**Этапы пайплайна:** [stages/README.md](stages/README.md) — проверка геометрии выполняется **агентом** (`python main.py finish …`, см. [AGENTS.md](AGENTS.md)).  
**База для тумб/тумбочек:** [TUMBA_RULES.md](TUMBA_RULES.md) — ящики/двери/фасады/зазоры и паттерны тумб.  
`moderator_cabinet.json` — только пример; размеры для других заказов **не копировать**.

## Возможности

- Анализ изображения через Vision API (OpenAI)
- Единый системный промпт (`prompts/system_prompt.txt`)
- Валидация по JSON Schema (`schema/furniture.schema.json`)
- CLI: конвертация и проверка
- Пример проекта: `examples/moderator_cabinet.json` (тумба для модератора)
- Скрипт БАЗИС: `basis_scripts/ImportFurnitureFromJSON.js` — создаёт панели по координатам из JSON

## Быстрый старт

```bash
cd "d:\cursor project vpn\bazis"
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
# Укажите OPENAI_API_KEY в .env
```

### Конвертация изображения

```bash
python main.py convert "path\to\spec.png" -o output\project.json
```

Дополнительный текст к промпту:

```bash
python main.py convert spec.png -i "Уточни: задняя стенка ЛДСП 16 мм" -o out.json --print
```

### Проверка JSON

```bash
python main.py validate examples/moderator_cabinet.json
```

### Проверка (выполняет агент, не пользователь)

После расчёта JSON агент Cursor сам запускает `python main.py finish examples/….json`.  
Подробнее: [AGENTS.md](AGENTS.md).

### Типовые ошибки (коротко)

- **Двери перекрыли верхнюю открытую полку**: если по картинке/ТЗ верхний ярус открыт, фасады должны закрывать **только нижний проём** (высота двери ограничена до уровня полки с зазором).
- **Ручки “не появились”**: скрипт ставит ручки только при `hardware.handles.count > 0` и когда фасады реально присутствуют как панели `type: door_front` / `drawer_front` (первый импорт попросит выбрать фурнитуру и сохранит `furniture_encoded`).

## Структура JSON

| Блок | Назначение |
|------|------------|
| `overall_dimensions` | Габариты изделия |
| `materials` | ЛДСП, толщина, кромка, цвет |
| `sections` | Секции (ящики, открытая, дверь) |
| `panels` | Все детали с размерами и координатами |
| `drawers` / `doors` | Фурнитура и зоны |
| `hardware` | Ручки, опоры, замки |
| `constraints` | Технологические зазоры |
| `warnings` | Противоречия на чертеже |
| `estimated_values` | Поля с оценочными размерами |

Система координат: **basis_mebelshik** — начало в левом нижнем переднем углу; X — ширина, Y — высота, Z — глубина.

У каждой панели поле **`basis_orientation`** (без `Rotate` в скрипте):

| Ориентация | Детали | `dimensions` |
|------------|--------|----------------|
| `horizont` | Дно, крышка | ширина по X × глубина по Z |
| `placement` | Все панели | бокс `{x1,x2,y1,y2,z1,z2}` — точные стыки без зазоров |
| `horizont` | Дно, крышка | `NewPanel(x2-x1, z2-z1)` — ширина по X |
| `horizont` | Полки, перемычки | `NewPanel(z2-z1, x2-x1)` — глубина по Z, длина по X |
| `vertical` | Боковины, перегородки | глубина по Z × высота по Y |
| `front` | Задняя стенка, фасады | ширина по X × высота по Y |

## БАЗИС-Мебельщик

Нативного импорта произвольного JSON нет — используется скрипт:

1. Скопируйте `basis_scripts/ImportFurnitureFromJSON.js` в `%USERPROFILE%\Документы\BazisN\Scripts`
2. В БАЗИС: **Скрипты** → **ImportFurnitureFromJSON.js**
3. Укажите сгенерированный `.json`

Скрипт создаёт:
- **панели** из `panels[]` (`NewPanel` + `placement`);
- **ручки** из `hardware.handles` (4 шт., сверху фасадов);
- **направляющие** из `hardware.drawer_guides` (3 ящика × 2 стороны).

Первый запуск: выбор ручки и направляющей в каталоге БАЗИС → строки в `furniture_encoded`.  
Подробно: [docs/BASIS_AUTOMATION.md](docs/BASIS_AUTOMATION.md).

## Примеры проектов

| Проект | JSON | Расчёт |
|--------|------|--------|
| Тумба модератора | `examples/moderator_cabinet.json` | `docs/CABINET_DIMENSIONS.md` |
| Кашпо | `examples/kashpo.json` | `docs/kashpo_DIMENSIONS.md` |
| Шкаф 700×400×500 | `examples/cabinet_700x400x500.json` | `docs/cabinet_700x400x500_DIMENSIONS.md` |
| Стойка охраны | `examples/security_desk.json` | `docs/security_desk_DIMENSIONS.md` |

Каждое новое ТЗ — **свой** JSON и `docs/<имя>_DIMENSIONS.md` (см. [INSTRUCT.md](INSTRUCT.md) §0).

- 1000×400×750 мм, ЛДСП 16 мм, U 740 ST 9  
- 3 ящика с доводчиком, центральный замок  
- Открытая средняя секция с полкой  
- Правая дверь с замком и полкой  
- Ручки 128 мм, регулируемые опоры  

## Переменные окружения

| Переменная | Описание |
|------------|----------|
| `OPENAI_API_KEY` | Ключ API OpenAI |
| `OPENAI_MODEL` | Модель (по умолчанию `gpt-4o`) |

## Расширение

- Добавьте поля в `schema/furniture.schema.json` и обновите промпт
- Подключите другой Vision-провайдер в `src/converter.py`
- Расширьте `ImportFurnitureFromJSON.js` для ящиков, пазов, отверстий под фурнитуру
