# Версионирование ParamSpec и управляемые миграции

Статус решения: **ParamSpec v1 остаётся единственной канонической версией**.
Документ фиксирует решение MEB-156, фактический формат хранения и обязательные
условия, при которых можно проектировать настоящий `paramspec-v2`.

## Решение: v2 сейчас не обоснован

Все найденные сохранённые изделия и 38 repository fixtures имеют
`schemaVersion: "paramspec-v1"`. Генераторы, production gate, Studio, history и
CFRN уже потребляют один и тот же v1 payload. Требуемое разделение catalog
metadata не меняет мебельную семантику и выражается совместимым v1 envelope и
adapter-границей. Поэтому создание `paramspec-v2`, dual-read или мигратора
v1→v2 сейчас было бы фиктивной сменой версии без несовместимого контракта.

**Pydantic v2 не является ParamSpec v2.** Pydantic — версия Python-библиотеки,
которая реализует валидацию. Версия данных определяется только полем
`schemaVersion`; сегодня допустимо только `paramspec-v1`.

## Канонический envelope v1

Физический JSON остаётся плоским, чтобы не ломать существующие генераторы и API.
Логически adapter `src/paramspec_versioning.py` делит документ на три слоя:

| Слой | Сохранённые поля | Кто владеет |
|---|---|---|
| discriminator | `schemaVersion="paramspec-v1"` | version adapter |
| domain payload | все типизированные поля изделия, кроме `catalog` | ParamSpec/Pydantic и детерминированный движок |
| catalog metadata | `catalog.creator_user_id`, `responsible_user_id`, `author`, `responsible` | Studio server; браузер не является источником истины |

`ParamSpecEnvelopeV1.payload_dict()` никогда не передаёт `catalog` в генератор.
`canonical_document()` собирает обратно совместимый плоский JSON и повторно
проверяет его строгой моделью. Таким образом metadata отделена на границе
домена без скрытой перестройки файлов и без смены `schemaVersion`.

## Inventory сохранённых полей

Источник истины типов и ограничений — `src/paramspec.py`; точная машинная форма —
`schema/paramspec.schema.json`. Ниже перечислены все группы, которые реально
могут сохраняться в ParamSpec v1.

| Группа | Поля |
|---|---|
| Идентификация и lifecycle | `schemaVersion`, `project_name`, `furniture_type`, `archetype`, `draft`, `created` |
| Габариты | `dimensions.{width,depth,height,depth_carcass,tolerance}` |
| Материалы | `materials.{board_thickness,back_thickness,board_material,back_material,facade_material,edge_band_thickness,color,color_code,facade_color,facade_color_code,board_article,facade_article,top_thickness,texture_direction}` |
| Опоры и зазоры | `legs.{type,height,adjustable,color,count,as_panel}`, `gaps.{facade,default}` |
| Секции | `sections[].{kind,id,width_share,shelves,shelf_levels,shelf_label,drawers,drawer_heights,door,door_name,door_names,door_below_shelf,door_swing,door_inset,door_z,rod,front_bottom,front_top,open_top,open_top_height,cover_top,prefix,niche_z_front,guide_gap,guide_type,box_z1,box_depth,box_y_offset,box_height,box_back_thickness,box_bottom_thickness,box_bottom_mode,box_back_mode,box_sides_on_bottom,boxes,back_limit}` |
| Фурнитура | `hardware.handles.{type,material,color,size,count,offset_from_top,furniture_encoded}`, `drawer_guides.{type,length_mm,soft_close,with_closer,furniture_encoded,cabinet_left_panel,cabinet_right_panel}`, `hinges.{type,color,adjustable,furniture_encoded}`, `locks[].{type,color,target,furniture_encoded}`, `selection` |
| Конструкция | `features`, `back_mount`, `sides_over_top`, `rod.{axis,height,diameter,length}`, `interior_z_front`, `carcass_z_front`, `top_overhang`, `socle_full`, `socle_recess`, `facade_reveal`, `apron`, `apron_height`, `frame`, `screen`, `screen_height`, `screen_thickness`, `screen_margin`, `screen_z`, `top_thickness`, `pedestal_diameter`, `base`, `base_thickness`, `base_diameter` |
| Ручные изменения | `overrides[].{panel,action,placement.{x1,x2,y1,y2,z1,z2},move,to,type,orientation,thickness,material}` |
| Composite | `blocks[].{name,origin.{x,y,z},spec}`; вложенный `spec` — non-composite ParamSpec v1 |
| Допущения | `warnings`, `estimated_values` |
| Catalog metadata | `catalog.{creator_user_id,responsible_user_id,author,responsible}` |

`project_name`, `furniture_type`, warnings и estimated values остаются в domain
payload: они доходят в производный `project.json` и используются материал-политикой,
выводом и документами. Ownership-поля `catalog` мебельную модель не меняют.

### Соседние сохранённые артефакты — не поля ParamSpec

| Артефакт | Форма и назначение |
|---|---|
| `<stem>.versions.json` | список `{ts, spec}`; `spec` обязан читаться тем же adapter, новая запись — только канонический strict-write v1 |
| `.history/<stem>.ai.json` | append-only AI audit: id/time/tenant/actor/revisions/message/reply/changes/provider/usage/context/image_count/trace_id/changed; не domain payload |
| `.previews/<stem>.png` | производное каталоговое изображение |
| `<stem>.project.json`, `.cfrn`, `.b3d`, deliveries, `builds.json` | производные артефакты; никогда не конвертируются обратно в ParamSpec как источник истины |
| archive `manifest.json` | server-owned archive/rollback metadata и список companions; не часть ParamSpec |

## Tolerant-read / strict-write

| Сценарий | Политика |
|---|---|
| Известный валидный v1 | читать типизированно; generator получает только payload |
| Неизвестное дополнительное поле | чтение допускается, поле не попадает в geometry; путь поля и счётчик попадают в privacy-safe metrics |
| Невалидное известное поле | чтение отклоняется; неизвестные поля не маскируют ошибку значения |
| Неизвестная `schemaVersion` | отклонить; не угадывать и не помечать как v2 |
| Запись | только строгая Pydantic-модель, `extra="forbid"`; при ошибке исходный файл не перезаписывается |
| Canonical rewrite | возможен только отдельной явно разрешённой операцией после dry-run и backup; обычное открытие ничего массово не переписывает |

Неизвестные поля сознательно не переносятся в каноническую запись: автоматическое
сохранение непонятных данных противоречило бы strict-write. Studio сначала отдаёт
клиенту канонический известный v1, а серверная запись повторно проверяет его.

## Dry-run, метрики и доказательство эквивалентности

Команда только читает legacy/tenant каталоги и history snapshots:

```bash
python scripts/audit_paramspec_versions.py \
  --legacy-root paramspecs \
  --tenant-root /path/to/copied/tenants
```

Отчёт содержит: распределение `schema_versions`, valid/draft/invalid,
unknown-field documents/count, число потенциальных canonical changes и для
каждого генерируемого документа результаты `geometry`, `drilling`, `cfrn`.
Значения ParamSpec в отчёт не выводятся; используются имя файла, хэш, версии,
счётчики и пути неизвестных полей. Инварианты dry-run:

- `dry_run=true`;
- `writes_performed=0`;
- `production_migration_allowed=false`;
- входные файлы, versions и tenant-like copies не меняются;
- geometry сравнивается по panels, drilling — по результату `compute_drilling`,
  CFRN — по структурной модели encoder до бинарной сериализации.

## History, backup и rollback

Пока v2 отсутствует, миграции нет и rollback не требуется: adapter применяется
на чтении, массовая запись запрещена. Если когда-либо будет разрешён canonical
rewrite v1, обязательны: отдельная копия tenant root, manifest с SHA-256 каждого
файла, атомарная запись, сохранение всех `<stem>.versions.json` и `.history`,
повторный dry-run после записи и rollback восстановлением точных байтов backup.

Версии и AI history мигрируются вместе с текущим документом, а не независимо:
каждый `versions[].spec` проходит тот же converter; revision hashes считаются от
канонического документа выбранной версии. Нельзя переписывать историю при
обычном открытии изделия.

## Когда нужен настоящий ParamSpec v2

RFC v2 создаётся только если новый контракт нельзя выразить optional/defaulted
полем v1 без изменения смысла существующих данных. RFC обязан определить:

1. несовместимый semantic diff и JSON Schema `paramspec-v2`;
2. явный version adapter и dual-read/single-write период;
3. идемпотентный v1→v2 migrator и поведение повторного запуска;
4. fixtures и tenant-copy evidence для geometry/drilling/CFRN;
5. обработку current document, всех revisions/history и public snapshots;
6. метрики ошибок, backup manifest, проверяемый rollback и stop conditions;
7. отдельное согласование production batch. Ни deploy, ни массовая migration не
   следуют автоматически из merge кода.
