# `prompts/` — промпты для конвертера (Vision → JSON)

## Файлы

- **`system_prompt.txt`** — системный промпт, который заставляет конвертер:
  - использовать координаты `basis_mebelshik`,
  - заполнять `placement` для каждой панели,
  - не использовать `rotation`,
  - соблюдать структуру `schema/furniture.schema.json`,
  - отмечать оценочные значения через `estimated` / `estimated_values`.

## Как использовать

Конвертация изображения (пример):

```bash
python main.py convert spec.png -o examples/<project>.json
python main.py validate examples/<project>.json
```

## Важно про “инструкции агента”

Этот каталог (`prompts/`) относится к **Vision-конвертеру** (`main.py convert`).  
Плейбук поведения **агента в чате** лежит в `.cursor/rules/agent-playbook.mdc` и применяется всегда.

Важно: при изменении схемы (`schema/`) нужно синхронизировать промпт.

