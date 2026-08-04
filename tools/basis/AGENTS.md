# AGENTS.md — входная точка для агентов (людей и нейросетей)

Проект: **basis** — конвейер «мебельное ТЗ → ParamSpec → детерминированный
генератор → проверки → Studio → производственный .b3d». Ты меняешь ParamSpec и
Python-код; координаты деталей ВСЕГДА считает генератор, не LLM.

## С чего начать читать

| Вопрос | Документ |
|---|---|
| Как устроен проект, где какой модуль, поток данных | [rules/architecture.md](rules/architecture.md) |
| Как читать ТЗ, поля ParamSpec, создать изделие | [rules/paramspec.md](rules/paramspec.md) |
| Координаты, корпус, зазоры, фасады (законы геометрии) | [rules/core.md](rules/core.md) |
| Поля генераторов по архетипам | [rules/generators.md](rules/generators.md) |
| Присадки, система 32, фурнитура | [rules/hardware.md](rules/hardware.md) |
| Материалы: дефолты, база, резолвер | [rules/materials.md](rules/materials.md) |
| Studio: UI, API, каталог, чат | [rules/studio.md](rules/studio.md) |
| Визуальные правки Studio без потери функций | [rules/studio-ui.md](rules/studio-ui.md) |
| Продуктовая логика, UX-этапы и принятые решения | [ux/README.md](ux/README.md) |
| Лист согласования, чертёж | [rules/delivery.md](rules/delivery.md) |
| Команды CLI, установка, env | [README.md](README.md) |

## Правило по умолчанию («сделай мебель»)

1. Составить ParamSpec → `python main.py generate paramspecs/<x>.json` →
   `python main.py finish projects/<x>.json`.
2. Показать в **Studio**: `python main.py studio paramspecs/<x>.json --out D:/claude/bazis/out`.
3. **Облако/API БАЗИС НЕ трогать** (сборка .b3d платная ~10₽, Cutting тоже) —
   только по явной просьбе пользователя. Статичный viewer .html — только когда
   нужен автономный файл.
4. Артефакты (project.json, .cfrn, .b3d, .html) — в out-каталог вне репозитория.

## Ворота качества (перед merge)

```bash
cd tools/basis
python -m tests.regression        # ГЕЙТ: все спеки valid + goldens EXACT
python -m pytest tests/ -q        # юнит; сканирует ВСЕ paramspecs/ — падение
                                  # может быть от чужой битой спеки, смотри чьей
```

Процесс: задачи в Linear (команда AKD) → ветка → коммит с `AKD-<N>` в сообщении →
PR в GitHub `Owl-14/mebelgen` → merge после зелёного регресса.

## Грабли (стоили времени — не наступать)

- **placement-чек ≠ корректный .b3d**: кодирование .cfrn может дать нахлёсты при
  правильном placement. Смотреть `check_cfrn_encoding`; полная правда — round-trip.
- **Новый purpose присадки** — регистрировать в `hardware.py`,
  `fasteners3d.py::_PURPOSE_KIND`, `webviewer.py::fastenerGroup`; проверки
  drilling/completeness должны его знать, иначе красный регресс.
- **Goldens обновлять заменой блока `panels`** из свежей генерации, осознанно.
- **Studio не перечитывает Python** — после правок кода перезапустить сервер.
- **UI Studio не заменяет движок** — визуальные задачи выполняются поверх
  существующих `PAGE` и `MebelScene`; обязательный parity-чек описан в
  [rules/studio-ui.md](rules/studio-ui.md).
- **Система 32**: присадка Ø8 (шкант + эксцентрик), шаг 32, пропил 4.
  Конфирматы Ø7/«пары 64» — снятый реверс, не использовать.
- **ИИ и база материалов**: не выдумывать артикулы — резолвер/чат работают только
  с реальными кандидатами из `materials/baza_materiala.json`.
- **«Цвет из палитры/по согласованию»** в ТЗ = цвет не выбран (generic-маркер),
  а не название декора.

## Быстрые ориентиры по коду

- Генерация: `src/generators/registry.py::generate_from_paramspec`
- Присадки: `src/hardware.py::compute_drilling`
- Все проверки одним вызовом: `src/studio.py::build_payload`
- 3D-движок (общий Studio+viewer): `src/webviewer.py::SCENE_JS` (`MebelScene`:
  `setView` — ракурсы, `snapshot`, анимация открывания)
- Каталог изделий и SVG-аксонометрия карточек: `src/studio.py::_axon_svg`, роут `/thumb/`
- ИИ-чат и провайдеры: `src/spec_chat.py` (`_OAI_PRESETS`, `available_providers`)
