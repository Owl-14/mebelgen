# `schema/` — JSON Schema и правила валидации

Здесь лежит схема, по которой валидируются все проекты.

## Файлы

- **`furniture.schema.json`** — главный JSON Schema.
  - определяет обязательные поля проекта: `project_name`, `materials`, `sections`, `panels`, `hardware` и т.д.
  - ограничивает структуру (`additionalProperties: false`) — лишние поля не пройдут валидацию.

## Как использовать

Проверка любого проекта:

```bash
python main.py validate examples/<project>.json
```

Если нужен новый тип фурнитуры/поле:

1. добавить в `furniture.schema.json`,
2. обновить `prompts/system_prompt.txt` (чтобы конвертер писал это поле),
3. при необходимости расширить `basis_scripts/ImportFurnitureFromJSON.js`.

