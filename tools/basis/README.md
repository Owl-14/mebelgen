# basis — ТЗ → JSON → модель в БАЗИС-Мебельщик

Превращает мебельное ТЗ (фото/спецификацию) в строгий JSON, по которому скрипт
строит готовую модель в **БАЗИС-Мебельщик 2026** (панели + ручки).

Правила построения (координаты, корпус, тумбы, рабочий процесс) — в [RULES.md](RULES.md).

## Установка

```bash
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env        # вписать OPENAI_API_KEY (для convert)
```

## Команды

```bash
# ТЗ-изображение → JSON (Vision, нужен OPENAI_API_KEY)
python main.py convert spec.png -o projects/<project>.json

# Проверки
python main.py validate          projects/<project>.json [--geometry]
python main.py check-geometry     projects/<project>.json        # пересечения панелей
python main.py check-consistency  projects/<project>.json        # placement↔dimensions↔габарит
python main.py check-consistency  projects/<project>.json --fix-dimensions

# Полный этап проверки (схема + геометрия + авторазделение полок)
python main.py finish             projects/<project>.json
```

## Структура

```
src/         конвертер (Vision), валидатор схемы, geometry_check, consistency_check
schema/      furniture.schema.json — контракт JSON
prompts/     системный промпт для convert
projects/    готовые проекты-примеры (.json)
scripts/     ImportFurnitureFromJSON.js — импорт JSON в БАЗИС
```

## Импорт в БАЗИС

1. Скопировать `scripts/ImportFurnitureFromJSON.js` в `%USERPROFILE%\Документы\BazisN\Scripts`.
2. БАЗИС → **Скрипты** → `ImportFurnitureFromJSON.js` → выбрать `projects/<project>.json`.
3. Первый запуск попросит выбрать ручку/направляющую в каталоге — строка
   сохраняется в `hardware.*.furniture_encoded` для повторного импорта.

Скрипт создаёт панели из `panels[]` (по `placement`) и ручки из `hardware.handles`
(если `count > 0`). Направляющие/петли — когда подключён каталог поставщика, иначе
мастерами БАЗИС «Установка ящиков/дверей». Подробнее о фурнитуре — [../../docs/BASIS_AUTOMATION.md](../../docs/BASIS_AUTOMATION.md).

## Переменные окружения

| Переменная | Описание |
|---|---|
| `OPENAI_API_KEY` | ключ OpenAI (для `convert`) |
| `OPENAI_MODEL` | модель, по умолчанию `gpt-4o` |
