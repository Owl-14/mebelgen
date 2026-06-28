# Training Data Audit

Date: 2026-06-29

## Local Inputs

- `/Users/macintosheesh/Downloads/ТЗ мебель Коми РФ (1).doc`
  - Legacy Word `.doc`, 27 MB, 39 pages, CP1251/Word binary.
  - `textutil` conversion produces mojibake, but direct UTF-16LE extraction gives usable Russian text.
  - Contains mixed scope: manufactured modular furniture plus procurement items such as chairs, armchairs, sofas.
- `/Users/macintosheesh/Downloads/Сыктывкар 3 часть .Чертежи .zip`
  - Extract with `ZipFile(metadata_encoding="cp866")`.
  - Contains 23 one-page PDF drawings and `Заказ покупателя № НФ-447 от 30.12.2025.xlsx`.
  - PDF files have almost no text layer; treat as visual references requiring render/OCR/vision, not text parsing.
- `/Users/macintosheesh/Downloads/База материала.xlsx`
  - 5,047 material rows, 19 columns.
  - Key columns: material article, material name, group id/name, unit, price, length, width, thickness, designation, overhang.

## Manufactured vs Procurement Filter

Manufactured modular furniture should be kept:

- Tables/desks: `стол`, `столешница`, `брифинг-приставка`, переговорные столы, рабочие места.
- Cabinets and wardrobes: `шкаф`, `гардероб`, `стеллаж`, `шкаф-купе`, `шкаф навесной`, `шкаф для одежды`.
- Pedestals/drawers: `тумба`, `тумба подкатная`, `тумба приставная`, printer pedestals.
- Kitchens: `кухня`, `мини кухня`, cabinet runs, wall/base modules.
- Built-ins and systems: `система встроенных шкафов`, `доборы`, niche wardrobes.
- Special modular items: `трибуна`, reception/guard desks, service tables, countertops with framing.

Procurement items should be ignored for generation:

- `стул`, `кресло`, `диван`, `пуф`, `банкетка`, similar upholstered/seating items.

The filter must run at item/row level, not at document level, because one TZ mixes both scopes.

## Extracted TZ Headings

Readable headings detected from `ТЗ мебель Коми РФ (1).doc` include:

- Procurement: `Стулья в переговорную`, `Стулья для посетителей`, `Диван в комнату отдыха директора`, `Кресло сотрудника`, `Кресло руководителя`, `Стулья приставные`, `Стулья для столовой`, `Диван на входную зону`, `Кресло офисное работника тип 1`.
- Manufactured: `Стол письменный тип 1/2/3`, `Тумба приставная`, `Тумба подкатная`, `Шкаф для документов тип 1/2`, `Шкаф комбинированный`, `Шкаф гардеробный тип 1/2`, `Тумба под принтер`, `Шкаф гардеробный для ниши`, `Стол директора`, `Брифинг-приставка`, `Шкаф под минихолодильник`, `Система встроенных гардеробных шкафов`, `Кухня`, `Мини кухня`, `Стол для корреспонденции`, `Стол для охранника`, `Столешница с обрамлением для лотка`, `Шкаф-купе для документов средний`, `Стол подкатной для обслуживания маломобильных групп населения`.

## Archive Order Items

The archive order spreadsheet contains 24 manufactured order rows:

- 38 `Стол директора 2000х800х750 Дуб Денвер Трюфель/Металл 9005`
- 39 `Тумба приставная к столу директора 1000х500х750 Дуб Денвер Трюфель`
- 44 `Брифинг-приставка 1000х800x750 Дуб Денвер Трюфель/Опоры 9005`
- 41 `Стол для переговоров в кабинете директора 1200х1200х750 Дуб Денвер Трюфель`
- 43 `Стол заместителя директора 1800х800х750 Дуб Денвер Трюфель/Металл 9005`
- 45 `Тумба приставная к столу зам. директора 1000х500х750 Дуб Денвер Трюфель`
- 46 `Шкаф для документов зам. директора 1000х400х1800 Дуб Денвер Трюфель`
- 47 `Шкаф гардеробный зам.директора 1000х400х1800 Дуб Денвер Трюфель`
- 48 `Шкаф комбинированный - директор 1000х400х1817 Дуб Денвер Трюфель`
- `Шкаф под минихолодильник 750х750х950 Дуб Денвер Трюфель`
- 50 `Система встроенных гардеробных шкафов 4000х600х1800 Бело-серый`
- 52 `Стол для большой переговорной 2000х900х750 Дуб Денвер Трюфель/Опоры 9005`
- 53 `Тумба для большой переговорной 800x400x900 Дуб Денвер Трюфель`
- `Зона референтов 3200x2400x1200 Бело-серая`
- 55 `Система встроенных шкафов в зоне референтов 3118х600х1800 Бело-серая`
- 59 `Тумбы в президиум 900х300х800 Светло-серые`
- 60 `Трибуна 500х400х1200 Оникс Серый`
- 61 `Кухня (2 этаж) 5650х1579х1800 Серый уголь`
- 62 `Мини кухня (3 этаж) первый блок 1700х700х1800, второй блок 2250х400х1800 Серый Дымчатый`
- 69 `Стол для корреспонденции 800х500х550 Светло-серый`
- 71 `Стол для охранника 1400х700х750 Светло-серый`
- 72 `Тумба подкатная 400x450х580 Светло-серая`
- 73 `Столешница с обрамлением для лотка 500-1500х300х750/1100 Дуб Кендал`

## PDF Drawing Observations

The PDF set is closer to the future production/Basis stage than the current client visualization stage.

Reusable layout rules:

- Desks: isometric view plus front/top or side views, dimensions on main extents, notes block with material, cable-channel, metal frame, screens, supports.
- Cabinets: isometric/front/side, internal shelves visible, repeated shelf spacing, door/handle/lock notes.
- Built-in systems: treat as a run of repeated modules, not a single cabinet object.
- Kitchens: require cabinet-run schema with per-module widths, appliance placeholders, wall/base modules, handles, backsplash/countertop, plinth/supports.
- Pedestals: show drawer count, handle offsets, wheel/support details, front and side views.

Important: these sheets are wireframe/production drawings. For client-facing ME-RA-like output, keep the clean page discipline and dimensions, but render furniture as smoother shaded material instead of transparent wireframe.

## Material Base Findings

Top-level group counts:

- `01 Листовой материал`: 963 rows.
- `02 Кромочные материалы`: 325 rows.
- `03 Погонные материалы`: 119 rows.
- `04 Крепеж`: 313 rows.
- `05 Фурнитура`: 3,148 rows.

High-value material mappings found:

- `H3430 ST22` -> `ЛДСП, 16/18 мм, Сосна Аланд белая, Egger`, with matching PVC edge.
- `H1399 ST10` -> `ЛДСП, 16 мм, Дуб Денвер трюфель, Egger`, with matching PVC edge.
- `H1387 ST10` -> `ЛДСП, 16 мм, Дуб Денвер графит, Egger`, with matching PVC edge.
- `U708 ST9` -> `ЛДСП, 16 мм, Светло-серый, Egger`, with matching edge options.
- `U968 ST9` -> `ЛДСП, 16/25 мм, Серый уголь, Egger`.
- `U767 ST9` -> `ЛДСП, 16 мм, Кубанит серый, Egger`.

Implication: renderer specs should reference canonical material IDs from this workbook, then derive display labels, approximate colors/textures, thicknesses, and edge descriptions from the material database.

## External Sources Reviewed

Useful candidates:

- OpenCutList for SketchUp: parts list, cutting diagrams, labels, cost/weight reports for woodworking projects. Useful for BOM/cutlist ideas, not for client rendering.
- dprojects Woodworking FreeCAD workbench: MIT-licensed FreeCAD workbench for simple cabinets, parametric furniture operations, dowels, drilling, measurements, cut-list export, transparent previews, open/close fronts.
- AIGenFurniture FreeCAD Workbench (`github.com/yelloish6/AIGenFurniture-freecad-workbench`): early MVP for parametric cabinet boxes in FreeCAD. Worth inspecting, but beta.
- FreeCAD-library: community FreeCAD parts library; useful for generic hardware/parts references, license and quality vary per part.
- Amazon Berkeley Objects: CC BY 4.0 product dataset with metadata, catalog images, 360 images, and 3D models. Useful for purchased furniture visual classifiers, but less relevant to produced cabinet furniture.
- ShapeNet / 3D-FUTURE / ABC: useful as general 3D shape or CAD research datasets, but not enough for exact custom cabinet geometry and production materials.

## Engineering Implications

Next schema expansion:

- Add `classification.scope`: `manufactured` or `procurement`.
- Add `archetype`: `desk`, `pedestal`, `cabinet`, `wardrobe`, `built_in_run`, `kitchen_run`, `countertop`, `lectern`.
- Add `modules[]` for kitchens and built-in runs.
- Add `sections[]`, `shelves[]`, `doors[]`, `drawers[]`, `hardware[]`.
- Add `materialRefs[]` pointing to material workbook rows.
- Add `viewPolicy`: which annotations belong to front/top/side/isometric views.

Implemented after this audit:

- `orchestrator/import-syktyvkar.py` imports the archive order XLSX from the ZIP
  and reads material rows from `База материала.xlsx`.
- `FurnitureSpec JSON` now supports `classification`, `materialRefs`, `modules`,
  `built_in_run`, `kitchen_run`, `countertop`, and `lectern`.
- The Three.js demo can load both `kabinety` and `syktyvkar` manifests.

Recommended next implementation step:

1. Use vision/OCR on the PDF drawings to enrich `modules[]`, doors, shelves,
   appliance placeholders, handles, and exact annotation policy.
2. Add a reviewer loop that flags unmatched materials and suspicious dimensions.
3. Continue using AI only as controller/parser/reviewer, not as the renderer.
