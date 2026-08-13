# MEB-094 — component-state matrix

Статус: `REVIEW READY`; применяется к выбранному направлению после user review.

Общие правила: focus — `2px` outline с offset `1px`; status никогда не передаётся
только цветом; busy не скрывает последний валидный 3D; disabled сохраняет читаемую
причину; hover/motion не меняют геометрию.

| Компонент | Default | Hover | Focus | Selected | Disabled | Busy | Warning | Error |
|---|---|---|---|---|---|---|---|---|
| Input | panel + neutral border, label снаружи | border emphasis | accent outline | текущее значение без отдельной заливки | muted fill, значение читаемо, причина рядом | readonly + этап «Пересчёт…» | warn border + текст причины | bad border + inline correction text |
| Button | neutral surface; редкая команда с текстом | лёгкий tint, без lift | accent outline | `aria-pressed=true`, accent line/tint + текст | без hover, `not-allowed`, причина доступна | блокирует повтор, глагол заменён этапом, spinner не обязателен | warn icon + verb | bad только для опасного/неуспешного действия + объяснение |
| Tab | текст + спокойная hit-area | text/tint | accent outline внутри hit-area | underline + accent text + `aria-selected=true` | muted text, не фокусируется без причины | выбранная вкладка остаётся, рядом короткий этап | marker + текст в содержимом | marker + текст в содержимом |
| Select | как input + локальный chevron SVG | border emphasis | accent outline | выбран option текстом; цвет не единственный сигнал | muted fill, значение остаётся читаемо | readonly surrogate + этап | warn border + helper | bad border + helper |
| Toggle/checkbox | neutral track/box + явная подпись | лёгкий tint | accent outline | check/knob + `checked` и текст состояния | muted, подпись объясняет недоступность | остаётся в последнем подтверждённом состоянии | warn marker рядом, не внутри knob | bad marker + сообщение |
| Menu/popover | panel, hairline, elevation только для слоя | строка получает tint | focus row outline/background | checkmark + текст текущего пункта | muted row, `aria-disabled=true` | меню закрывается; источник показывает этап | предупреждающий пункт с icon+text | опасный пункт отделён и подписан |
| Tooltip | появляется только для пояснения/shortcut | n/a | показывается при keyboard focus | n/a | объясняет причину disabled | может объяснить этап, не заменяет status | не единственный носитель warning | не единственный носитель error |

## Проверяемые состояния Studio

- Input: W/D/H, точные XYZ, AI composer.
- Button: Undo disabled, Execute busy, production actions warning/error-gated.
- Tab: `3D / Чертёж / Раскрой`, правые modes.
- Select: проект, material, archetype, AI provider.
- Toggle: holes/hardware/texture/dimensions/x-ray.
- Menu/popover: AI settings и контекстные действия.
- Tooltip: rail/icon-only commands и disabled production controls.
