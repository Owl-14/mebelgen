# `src/` — Python-логика конвертера и валидации

Эта папка — реализация CLI-конвертера.

## Что где

- `validate.py` — валидация JSON по `schema/furniture.schema.json` (используется в `main.py validate`).
- `finish_project.py` — этап 3: схема + геометрия + автофикс (`main.py finish`).
- `geometry_check.py` — пересечения AABB.
- `converter.py` — конвертер (Vision/OpenAI) → JSON по схеме (используется в `main.py convert`).

## Команды (для агента)

```bash
python main.py finish examples/<project>.json
```

Пользователю CLI проверок не нужен. См. [AGENTS.md](../AGENTS.md).

