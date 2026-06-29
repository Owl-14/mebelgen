# `basis_scripts/` — скрипт и файлы для импорта в БАЗИС

Эта папка содержит **то, что нужно для запуска в БАЗИС-Мебельщик**:

- **`ImportFurnitureFromJSON.js`** — главный скрипт импорта.
  - Запускается в БАЗИС (меню **Скрипты**).
  - Просит выбрать `.json` файла проекта.
  - Строит панели по `panels[]` (через `placement` + `basis_orientation`).
  - Ставит ручки, если в JSON задано `hardware.handles.count > 0` и выбран/задан `furniture_encoded`.
  - Направляющие/петли/прочая фурнитура зависят от наличия каталога и настроек (см. `docs/BASIS_AUTOMATION.md`).

## JSON-файлы в этой папке

Файлы вида `*.json` здесь — это **готовые проекты для импорта** (копии из `examples/`).

Например:
- `moderator_cabinet.json`
- `kashpo.json`
- `cabinet_700x400x500.json`
- `security_desk.json`

### Зачем копии

`examples/*.json` — это исходники в репозитории.  
`basis_scripts/*.json` — удобные копии “в одном месте” для выбора в диалоге БАЗИС.

Рекомендуемый workflow:

1. Правки в `examples/<project>.json`
2. Проверка (делает агент автоматически): `python main.py finish examples/<project>.json`
3. Запуск `ImportFurnitureFromJSON.js` в БАЗИС → выбрать `basis_scripts/<project>.json`

### Если ручек нет

Проверьте в JSON:

- `hardware.handles.count > 0`
- фасады присутствуют в `panels[]` как `type: door_front` / `drawer_front` (с `placement`)

