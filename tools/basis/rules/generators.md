# Параметры генераторов по архетипам

Общие правила координат/корпуса/зазоров — в [../RULES.md](../RULES.md).
Здесь — какие поля `ParamSpec.sections[]` понимает каждый генератор
(`src/generators/`). Координаты считает код, ParamSpec задаёт параметры.

## Общие поля ParamSpec
`archetype`, `dimensions{width,depth,height,[depth_carcass,tolerance]}`,
`materials{board_thickness,[back_thickness],board_material,[color,color_code]}`,
`legs{type,height,[as_panel]}`, `gaps{facade,default}`,
`[interior_z_front]` (фронт полок/перегородок, по умолчанию T),
`[carcass_z_front]` (фронт-инсет корпуса), `[top_overhang]=[z1,z2]` (свес столешницы),
`[socle_full]` (цоколь на всю ширину).

## corpus
Пустой короб. Секции не нужны.

## shelving
Одна колонка. `sections[0]`: `shelves N` или `shelf_levels[...]`.

## drawer_unit
Одна колонка ящиков. `sections[0]`: `drawers N`, `drawer_heights[...]`,
`front_bottom`, `open_top` (верхняя ниша с полкой), плюс параметры короба (ниже).

## door_unit
Одна колонка. `shelves`/`shelf_levels` + `door 1|2`.

## cabinet / wardrobe
Несколько колонок (слева направо). У каждой `sections[i]`:
- `kind`: shelves | drawers | door | open;
- `width_share` — доля/абсолютная ширина (иначе поровну);
- `shelves N` | `shelf_levels[...]`, `shelf_label`;
- `drawers N` + параметры короба;
- `door 1|2`, `door_name(s)`, `door_z` (overlay=0..T | front=-T..0), `door_below_shelf`.

## desk / table
`top_overhang` (свес столешницы), `apron` (задняя царга), `apron_height`.

## round_table
Круглый стол на пьедестале. `dimensions.width = depth = диаметр`. Поля:
`top_thickness`, `pedestal_diameter`, `base` (диск-основание), `base_thickness`,
`base_diameter`. Панели несут `shape` (circle|cylinder) + `radius`; `placement` —
габаритный бокс (квадрат×высота). Физическая сборка в БАЗИС требует поддержки
контура в импортёре (AKD-42).

## composite
`blocks[]`: `{name, origin:{x,z}, spec: <вложенный ParamSpec>}`. Генератор строит
каждый блок и сдвигает на origin. Вложенность — один уровень.

## Параметры короба ящика (drawers)
| поле | смысл | по умолч. |
|---|---|---|
| `guide_gap` | зазор короба от стойки по X (под направляющие) | 14.5 |
| `box_z1` | фронт короба по Z | T |
| `box_depth` | длина боковин короба | 350 |
| `box_y_offset` | низ короба над низом фасада | T |
| `box_height` | высота боковин короба | ~0.52·min(h) |
| `box_back_thickness` | толщина задней | T |
| `box_bottom_thickness` | толщина дна | T |
| `box_bottom_mode` | `between` (между боковин) \| `under` (под боковинами) | between |
| `box_back_mode` | `beyond` (за боковинами) \| `inset` (в пределах) | beyond |
| `box_sides_on_bottom` | боковины стоят на дне (иначе от низа короба) | false |
| `boxes` | строить короба (`false` — только фасады) | true |
