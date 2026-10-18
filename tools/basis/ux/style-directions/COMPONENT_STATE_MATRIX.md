# MEB-094 — component-state matrix

Статус: `REVIEW READY / USER SELECTION REQUIRED`. Матрица применяется только после пользовательского выбора направления.

Общие правила: focus — `2px` outline с offset `1px`; статус никогда не передаётся только цветом; busy не скрывает последний валидный 3D; disabled сохраняет читаемую причину; hover/motion не меняют геометрию. `Empty` означает отсутствие значения/результата, а `Open/expanded` — раскрытый контрол или слой, не выбранное направление.

| Компонент | Default | Empty | Hover | Focus | Selected | Open/expanded | Disabled | Busy | Warning | Error |
|---|---|---|---|---|---|---|---|---|---|---|
| Input | panel + neutral border, label снаружи | пустое значение + постоянный label/placeholder, без ложной ошибки | border emphasis | accent outline | текущее значение без отдельной заливки | n/a; multiline может увеличиваться только по контенту | muted fill, значение читаемо, причина рядом | readonly + этап «Пересчёт…» | warn border + текст причины | bad border + inline correction text |
| Button | neutral surface; редкая команда с текстом | n/a; отсутствие target объясняется disabled-причиной | лёгкий tint, без lift | accent outline | `aria-pressed=true`, accent line/tint + текст | trigger получает `aria-expanded=true`; раскрытый слой остаётся связан через `aria-controls` | без hover, `not-allowed`, причина доступна | блокирует повтор, глагол заменён этапом, spinner необязателен | warn icon + verb | bad только для опасного/неуспешного действия + объяснение |
| Tab | текст + спокойная hit-area | панель показывает самостоятельный empty-state с дальнейшим действием | text/tint | accent outline внутри hit-area | underline + accent text + `aria-selected=true` | активная панель видима; вложенные секции управляют своим `aria-expanded` | muted text, не фокусируется без причины | выбранная вкладка остаётся, рядом короткий этап | marker + текст в содержимом | marker + текст в содержимом |
| Select | как input + локальный chevron SVG | явный placeholder «Не выбрано», не выглядит выбранной option | border emphasis | accent outline | выбран option текстом; цвет не единственный сигнал | native/listbox открыт, trigger `aria-expanded=true`, текущая option отмечена | muted fill, значение остаётся читаемо | readonly surrogate + этап | warn border + helper | bad border + helper |
| Toggle/checkbox | neutral track/box + явная подпись | n/a; boolean всегда имеет `true/false` | лёгкий tint | accent outline | check/knob + `checked` и текст состояния | n/a; disclosure-toggle использует правило Button | muted, подпись объясняет недоступность | остаётся в последнем подтверждённом состоянии | warn marker рядом, не внутри knob | bad marker + сообщение |
| Menu/popover | закрыт; trigger остаётся в потоке | открытая коллекция показывает «Нет доступных действий» и не исчезает молча | строка получает tint | focus row outline/background | checkmark + текст текущего пункта | panel видим, trigger `aria-expanded=true`, Escape/наружный click закрывают с возвратом focus | muted row, `aria-disabled=true` | меню закрывается; источник показывает этап | предупреждающий пункт с icon+text | опасный пункт отделён и подписан |
| Tooltip | скрыт; появляется только для пояснения/shortcut | пустой tooltip не рендерится | показывается после короткой устойчивой задержки | показывается при keyboard focus | n/a | видимая подсказка имеет роль tooltip и связь с trigger | объясняет причину disabled | может объяснить этап, не заменяет status | не единственный носитель warning | не единственный носитель error |

## Проверяемые состояния Studio

- Input: W/D/H, точные XYZ, AI composer; empty — очищенный composer с доступным label.
- Button: Undo disabled, Execute busy, production actions warning/error-gated; expanded — AI settings trigger.
- Tab: `3D / Чертёж / Раскрой`, правые modes; empty — самостоятельное содержимое пустого проекта/каталога.
- Select: проект, material, archetype, AI provider; empty — «Не выбрано» до выбора.
- Toggle: holes/hardware/texture/dimensions/x-ray.
- Menu/popover: раскрытые AI settings и контекстные действия, включая пустой список.
- Tooltip: rail/icon-only commands и disabled production controls.
