# Демо-компания и просмотр клиентов из платформы

Статус: `PROPOSED / NEXT TENANT MIGRATION PHASE`

Этот документ фиксирует следующую фазу после identity/admin vertical slice:

1. platform owner Akeda одним действием открывает любую компанию в том же
   интерфейсе, которым пользуется клиент;
2. он видит каталоги, изделия, сотрудников, действия и историю AI-диалогов, но
   остаётся самим собой — без подмены личности сотрудника;
3. публичная демо-учётка получает отдельную временную копию демо-компании на
   каждую сессию, поэтому посетители могут экспериментировать и не влияют ни на
   эталон, ни друг на друга;
4. существующий Studio переносится на tenant-aware repository без изменения
   `src/webviewer.py`, `MebelScene`, внешнего вида и физики 3D-модели.

Документ является продуктово-техническим контрактом. Он не объявляет текущие
общие файловые каталоги безопасными для нескольких компаний.

## 1. Решения этой фазы

### 1.1. «Открыть компанию», а не «сеанс поддержки»

Текущая форма с причиной, номером обращения, TTL и кнопкой `Начать read-only
сеанс` убирается из основного пути platform owner. В реестре компаний и в её
карточке остаётся одно понятное действие: **`Открыть компанию`**.

После клика platform owner попадает в обычное рабочее пространство выбранной
компании:

- тот же каталог изделий;
- тот же Studio и неизменённый 3D viewport;
- те же карточки сотрудников и активности;
- та же история AI-команд конкретного изделия.

Это не impersonation. В серверном контексте и журнале actor всегда остаётся
platform-пользователем. Постоянная полоса сверху говорит:

> Вы смотрите «Север Мебель» как администратор Akeda · Вернуться к платформе

Причина, тикет и ручной срок не требуются. Контекст заканчивается при переходе
обратно в платформу, выходе из учётной записи или закрытии auth-сессии.

Первая версия этого режима предназначена для диагностики и чтения. Она даёт
видеть все нужные клиентские данные, но не выдаёт скрытую роль клиента и не
позволяет случайным кликом изменить его изделие. Если позже понадобится чинить
данные клиента силами Akeda, это будет отдельное явно включаемое действие с
новым permission и отдельным продуктовым решением.

### 1.2. Демо — не общий изменяемый аккаунт

В платформе создаётся специальная организация **`Akeda Demo`** с
`organization.mode = demo`. У неё есть опубликованный неизменяемый эталон:
каталоги, изделия, превью и заранее подготовленная история AI-команд.

Каждый успешный вход по опубликованным demo credentials создаёт новую auth-сессию
и новый **ephemeral sandbox**. Все изменения посетителя живут только в sandbox
этой сессии. Новый вход всегда начинает с опубликованного эталона.

Точное поведение:

- обновление страницы сохраняет изменения текущей сессии;
- вкладки одного браузера с одной cookie видят один sandbox;
- другой браузер, incognito или новый login получает другой чистый sandbox;
- `Выйти`, expiry, принудительный reset или сборщик мусора уничтожают sandbox;
- публичные prompt, ParamSpec, превью и вложения из sandbox не пишутся в
  canonical tenant database и постоянное object storage;
- платная `.b3d`-сборка в demo запрещена на сервере;
- эталон можно изменить только из platform admin в отдельном режиме
  `Редактировать демо-эталон → Опубликовать`.

## 2. Что существует сейчас и почему это нельзя просто открыть клиентам

Аудит выполнен по `src/studio.py`, `src/paramspec.py`, `src/spec_chat.py` и
текущему файловому layout.

### 2.1. Runtime Studio

`_Studio` держит на весь процесс единственные mutable:

- `st.spec_path`;
- `st.spec`;
- `st.out_dir`;
- `_ChatGuard` и его общий token budget.

`/api/open`, `/api/new`, `/api/duplicate` и `/api/import-tz` меняют `st.spec` и
`st.spec_path` для всех вкладок и всех пользователей процесса. Поэтому два
человека могут открыть разные изделия и незаметно переключить состояние друг
друга. Это должно исчезнуть до подключения auth к Studio.

### 2.2. Изделие и ParamSpec

Сейчас identity изделия — имя файла `paramspecs/<stem>.json`. В JSON есть
`project_name`, `archetype`, `dimensions`, `materials` и прочные доменные поля,
но нет стабильного `project_id`, `organization_id` или ревизии.

`ParamSpec` остаётся чистым доменным payload. Tenant metadata нельзя добавлять в
него как единственный механизм изоляции: оно хранится во внешнем server-side
envelope (`projects` и `project_revisions`). Генератор продолжает получать тот же
словарь ParamSpec и не узнаёт о пользователях или компаниях.

### 2.3. Каталог

`_list_projects()` сканирует все `*.json` одного общего каталога. Отдельной
сущности каталога нет. `/api/projects` возвращает весь каталог, а `/api/open`
принимает имя файла.

В новой модели каталог — server-side сущность компании, а проект выбирается по
opaque `project_id`. Имя файла больше не является ни identity, ни permission
boundary.

### 2.4. Версии

Версии записываются рядом с файлом в `<stem>.versions.json` как массив до 30
полных копий ParamSpec. `restore` меняет только process-global `st.spec`, пока
пользователь отдельно не сохранит его.

В новой модели ревизии immutable. Restore создаёт новую ревизию со ссылкой на
восстановленный источник; существующая история никогда не переписывается.

### 2.5. Превью

Сейчас есть два вида:

- `.previews/<stem>.png` — snapshot 3D, присланный из браузера при сохранении;
- `.previews/<stem>.axon<V>.svg` — производный кэш `_axon_svg`, инвалидируемый
  mtime спеки и номером renderer version.

Оба должны стать revision-scoped. PNG является артефактом ревизии, SVG можно
всегда перестроить и кэшировать по `(revision_id, renderer_version)`.

### 2.6. AI-чат и журнал изменений

Постоянного chat storage сейчас нет:

- `CHAT_HISTORY` существует только в JavaScript текущей страницы;
- при открытии другого изделия он очищается;
- хранится максимум 16 сообщений, а провайдеру обычно передаются последние 8;
- `OPERATIONS` и визуальный блок `Изменения` также живут только в DOM/памяти
  вкладки и ограничены 30 записями;
- сервер получает историю из body и не может доказать, к какому tenant или
  проекту она относится.

Значит, старые диалоги восстановить невозможно. После миграции каждый запрос,
ответ, технический diff, статус, revision и snapshot проверок сохраняются в
project-scoped thread. Ограничение контекста LLM не ограничивает историю для
человека: модель может получить последние N сообщений, а интерфейс показывает
всю сохранённую историю с пагинацией.

### 2.7. Производственные файлы и расходы

Сейчас `out/<stem>.project.json`, `.cfrn`, `.b3d`, delivery-файлы и общий
`out/builds.json` не имеют tenant boundary. `/api/open-file` работает с файловым
путём. `out/chat_tokens.json` хранит общий суточный расход AI-инстанса.

В новой модели любой artifact/job содержит `organization_id`, `project_id` и
`revision_id`. Клиент получает artifact ID или серверный download URL, но не
передаёт произвольный filesystem path.

### 2.8. Нынешний public demo

`STUDIO_PUBLIC=1` защищает от перезаписи только JSON-файлы, существовавшие при
старте процесса. Дубликаты и новые изделия всё равно попадают в общую папку;
runtime, версии, preview, outputs и история сборок остаются общими. Rate limit
по IP и глобальный token budget полезны как anti-abuse, но не создают sandbox.

Этот режим нельзя считать требуемой демо-компанией. После cutover он либо
удаляется, либо становится только compatibility-флагом, включающим новый
`demo` access mode.

## 3. Контексты доступа

Каждый workspace/API request получает серверный `AccessContext`:

```text
actor_user_id          реальный пользователь из auth-сессии
auth_session_id        серверная сессия; raw token остаётся только в cookie
organization_id        компания, вычисленная сервером
access_mode            member | platform_inspect | demo
membership_id          только для member
platform_role          только для platform_inspect
demo_sandbox_id        только для demo; клиент его не выбирает
permissions            итоговый набор server-side permissions
request_id             correlation id для audit/jobs
```

Правила:

1. `organization_id` из query/body/header не является доказательством доступа.
2. Для project route сначала загружается project, затем его `organization_id`
   сравнивается с `AccessContext`.
3. Platform owner входит через разрешённый platform route и получает
   `access_mode=platform_inspect`; employee selector не используется.
4. Demo route получает sandbox исключительно из auth-сессии.
5. Внутри generator/viewer передаётся только ParamSpec; access context остаётся
   на уровне repository/service/HTTP.

### Permissions этой фазы

| Permission | Назначение |
|---|---|
| `platform.organization.inspect` | Открыть клиентскую компанию в режиме Akeda |
| `catalog.read/create/manage` | Каталоги компании |
| `project.read/create/write` | Изделия и новые immutable revisions |
| `chat.read` | История сообщений и AI-операций |
| `ai.run` | Новая AI-команда |
| `artifact.read` | Превью, чертежи и разрешённые результаты |
| `production.export/build` | Бесплатный export / платная job |
| `demo.play` | Изменения только в ephemeral sandbox |
| `demo.template.manage/publish` | Черновик и публикация демо-эталона |

`platform_inspect` получает чтение организации, сотрудников, каталогов,
проектов, chat и artifact metadata. Платные и изменяющие действия не наследуются
от роли конкретного сотрудника.

## 4. Целевая модель данных

Все tenant-сущности содержат `organization_id` явно, даже если его можно вывести
через parent. Это позволяет строить простые negative checks, индексы и аудит.

### 4.1. Изменения identity

#### `organizations`

- `mode`: `standard | demo`;
- `status`: существующий lifecycle;
- `default_catalog_id` после создания первого каталога;
- для production допускается только одна active public demo organization.

#### `users`

- `account_kind`: `person | shared_demo`;
- shared demo account нельзя пригласить в customer organization;
- shared demo account не получает admin/member-management permissions.

#### `sessions`

- `access_mode`: `member | platform | demo`;
- `demo_sandbox_id` nullable;
- platform organization context не обязан храниться в cookie: целевая org
  берётся из проверенного workspace route;
- новый успешный demo login всегда создаёт новую session, не переиспользуя
  прежний sandbox.

Существующая таблица `support_sessions` не используется в новом основном UX.
Её данные сохраняются для старого audit, затем таблица удаляется отдельной
миграцией после compatibility window.

### 4.2. Каталоги и изделия

#### `catalogs`

```text
id, organization_id, name, slug, kind, position, status,
created_by_user_id, created_at, updated_at
```

`kind`: `working | showcase | archive`. При создании customer organization
автоматически создаётся `Изделия`; у Akeda Demo — `Демо-каталог`.

#### `projects`

```text
id, organization_id, catalog_id, name, archetype, furniture_type,
status, current_revision_id, created_by_user_id, created_at, updated_at
```

Имя изменяемо и не уникально. Все ссылки используют UUID `project_id`.

#### `project_revisions`

```text
id, organization_id, project_id, revision_no, parent_revision_id,
paramspec_json, paramspec_sha256, source, summary,
created_by_user_id, created_at
```

`source`: `import | manual | ai | restore | duplicate | migration`. Ревизия
immutable. `current_revision_id` обновляется транзакционно с optimistic check
`base_revision_id`; конфликт возвращает `409 revision_conflict`.

ParamSpec JSON не содержит credentials, organization metadata или filesystem
paths и продолжает проходить существующую `validate_paramspec()`.

### 4.3. AI-история

#### `chat_threads`

```text
id, organization_id, project_id, title, status,
created_by_user_id, created_at, updated_at
```

Первая версия создаёт один default thread на изделие, но модель допускает
несколько тредов позже.

#### `chat_messages`

```text
id, organization_id, project_id, thread_id, sequence_no,
role, text, attachment_manifest_json, actor_user_id,
ai_operation_id, created_at
```

`role`: `user | assistant | system_event`. Секреты провайдера и base64
вложения в сообщении не хранятся.

#### `ai_operations`

```text
id, organization_id, project_id, thread_id,
actor_user_id, access_mode, context_scope, selected_part_name,
base_revision_id, result_revision_id, provider, status,
diff_json, check_snapshot_json, usage_json, error_code,
started_at, completed_at
```

Статусы совместимы с нынешним UI: `pending | applied | warning | answer |
error | undone | cancelled`. Точный prompt и reply лежат в `chat_messages`,
технический результат — в `ai_operations`.

Нажатие undo не удаляет операцию: создаётся новая revision, а operation получает
ссылку на compensating revision и состояние `undone`.

### 4.4. Превью, exports и jobs

#### `artifacts`

```text
id, organization_id, project_id, revision_id, job_id,
kind, storage_key, content_type, byte_size, sha256,
renderer_version, created_by_user_id, created_at, deleted_at
```

`kind`: `preview_png | axon_svg | project_json | cfrn | b3d |
delivery_html | delivery_pdf | attachment`.

#### `jobs`

```text
id, organization_id, project_id, revision_id, kind, status,
requested_by_user_id, access_mode, cost_rub, provider_ref,
result_artifact_id, error_code, created_at, completed_at
```

Платность проверяется до enqueue. Demo не может создавать `build_b3d` job.

### 4.5. Platform view

Для простого просмотра отдельная support-сессия не нужна. Достаточно platform
auth-сессии, проверенного route и событий audit:

- `platform.organization_view.entered`;
- `platform.organization_view.exited`;
- `platform.chat_viewed` — при первом открытии конкретного thread в сессии;
- `platform.artifact_downloaded` — на download чувствительного файла.

Audit содержит platform actor, organization/project/thread/artifact IDs и
request ID, но не копию prompt, ParamSpec или файла.

### 4.6. Демо-эталон и sandbox

#### Persistent metadata

`demo_publications`:

```text
id, organization_id, version_no, status, manifest_json,
published_by_user_id, published_at, created_at
```

`manifest_json` перечисляет опубликованные catalog/project/revision/thread IDs и
их checksum. Publication immutable; новая публикация создаёт новую версию.

`demo_config`:

```text
organization_id, shared_user_id, active_publication_id,
status, session_idle_seconds, session_absolute_seconds,
ai_requests_per_session, ai_tokens_per_session, updated_at
```

#### Ephemeral runtime

`DemoSandboxRepository` хранит copy-on-write overlay по `demo_sandbox_id`:

- ссылку на pinned `publication_id`;
- изменённые/новые project envelopes и revisions;
- новые chat messages и AI operations;
- временные previews/attachments;
- usage counters и timestamps.

Для первой single-instance версии допустим process-local memory + отдельный
`runtime/demo/<sandbox_id>/` на временном диске. Для нескольких инстансов нужен
Redis/совместимое TTL-store. Ни один вариант не использует customer database или
canonical demo object storage для sandbox writes.

## 5. Repository boundary

HTTP handler больше не читает и не пишет `Path` напрямую. Он обращается к
интерфейсам:

```text
CatalogRepository.list/create/update(context, ...)
ProjectRepository.get/create/append_revision/restore(context, ...)
ChatRepository.list_threads/list_messages/append_operation(context, ...)
ArtifactRepository.put/get_authorized(context, ...)
JobRepository.enqueue/list(context, ...)
```

Диспетчер выбирает реализацию по `access_mode`:

- `member` → persistent tenant repository;
- `platform_inspect` → тот же persistent repository через read-only adapter;
- `demo` → immutable publication + session overlay.

Так один и тот же Studio UI работает во всех режимах, а demo/reset и platform
view не требуют копии интерфейса.

## 6. Целевые маршруты

Названия могут быть реализованы под `/api/v2`; смысл и boundary обязательны.

### 6.1. Workspace и платформа

| Method/route | Назначение | Permission |
|---|---|---|
| `POST /api/platform/organizations/{org_id}/open` | Audit enter, вернуть workspace URL | `platform.organization.inspect` |
| `POST /api/platform/organizations/{org_id}/exit` | Audit exit | текущий platform actor |
| `GET /workspace/o/{org_id}` | Company workspace с banner | member этой org или platform inspect |
| `GET /api/workspace/context` | Actor, org, mode, permissions, demo flags | authenticated |
| `GET /api/organizations/{org_id}/members` | Сотрудники | `member.read`/platform inspect |
| `GET /api/organizations/{org_id}/activity` | Активность | `audit.read`/platform inspect |

`open` — POST с CSRF, потому что создаёт audit event. Workspace URL не даёт
доступ без действующей platform session и проверки permission.

### 6.2. Каталоги и проекты

| Method/route | Назначение |
|---|---|
| `GET /api/catalogs` | Каталоги текущего server-derived context |
| `POST /api/catalogs` | Новый каталог |
| `GET /api/catalogs/{catalog_id}/projects` | Карточки изделий |
| `POST /api/projects` | Новое изделие/черновик |
| `GET /api/projects/{project_id}` | Project envelope + current ParamSpec |
| `POST /api/projects/{project_id}/duplicate` | Копия в текущем repository |
| `POST /api/projects/{project_id}/revisions` | Save с `base_revision_id` |
| `GET /api/projects/{project_id}/revisions` | История ревизий |
| `POST /api/projects/{project_id}/restore` | Новая revision из старой |
| `POST /api/projects/{project_id}/generate` | Существующий `build_payload` без записи |
| `GET /api/projects/{project_id}/previews/{kind}` | Tenant-checked preview |

Body больше не может подменить project identity. На save server берёт проект из
route, проверяет current/base revision и только затем принимает новый ParamSpec.

### 6.3. AI и производство

| Method/route | Назначение |
|---|---|
| `GET /api/projects/{project_id}/chat/threads` | Треды изделия |
| `GET /api/chat/threads/{thread_id}/messages?cursor=` | История с пагинацией |
| `POST /api/chat/threads/{thread_id}/operations` | AI-команда на `base_revision_id` |
| `POST /api/ai-operations/{operation_id}/cancel` | Отмена выполняющейся операции |
| `POST /api/projects/{project_id}/exports/cfrn` | Бесплатный artifact job/result |
| `POST /api/projects/{project_id}/builds/b3d` | Платная job, запрещена demo |
| `GET /api/projects/{project_id}/jobs` | История сборок только проекта |
| `GET /api/artifacts/{artifact_id}/download` | Проверка org + stream файла |

История AI не приходит целиком из клиента. Server загружает thread, выбирает
ограниченное контекстное окно и добавляет новую пару message/operation
транзакционно. Результат AI применим только если `base_revision_id` всё ещё
актуален; иначе operation завершается `revision_conflict` без перезаписи работы
другого пользователя.

### 6.4. Демо

| Method/route | Назначение |
|---|---|
| `POST /api/auth/login` | При shared demo user создать новую session + sandbox |
| `GET /api/demo/session` | Publication version, limits, reset capability |
| `POST /api/demo/reset` | Уничтожить overlay, создать чистый sandbox в этой auth-сессии |
| `POST /api/platform/demo/publish` | Опубликовать новый immutable baseline |

Остальные routes те же, что у normal Studio. Resolver направляет их в
`DemoSandboxRepository`; клиент не передаёт `demo_sandbox_id`.

## 7. UX-путь platform owner

### 7.1. Реестр

Строка компании показывает название, тип (`Клиент`/`Демо`), статус, сотрудников,
изделия и последнюю активность. Действия:

- основной `Открыть компанию`;
- вторичный `Настроить` для сотрудников, статуса и доступа;
- у Akeda Demo — `Редактировать эталон` и `Открыть публичное демо`.

Раздел `Доступы` и техническая форма support в этой фазе не нужны в основной
навигации. Старые support events остаются доступными в audit.

### 7.2. Внутри компании

Используется настоящий company workspace, а не отдельный диагностический
dashboard. Постоянный banner нельзя закрыть. Он содержит компанию, реальную
личность platform actor и `Вернуться к платформе`.

Навигация:

1. `Каталоги` — каталоги и карточки изделий;
2. `Сотрудники` — роли, статус, последняя активность;
3. `Активность` — действия по сотруднику/изделию/времени;
4. клик по изделию — обычный Studio;
5. `История AI` внутри изделия — команды и ответы этого изделия.

Никакого выбора «зайти как Иван Иванов» нет. В activity можно отфильтровать
Ивана, но platform actor не превращается в него.

### 7.3. История AI изделия

При открытии Studio существующий левый блок `Изменения` загружается с сервера,
а не начинается пустым. Запись показывает:

- кто и когда дал команду;
- точный пользовательский текст;
- контекст: всё изделие или выбранная деталь;
- ответ системы;
- человеческий diff и технические подробности;
- исходную и итоговую revisions;
- snapshot проверок и outcome;
- provider/usage без ключей и внутренних секретов.

Platform inspect видит те же записи. Изменяющие controls в Studio disabled с
короткой подписью `Просмотр компании из Akeda`; вращение, zoom, открывание
фасадов, выбор детали, 3D/чертёж/раскрой и просмотр истории остаются доступны.

## 8. UX-путь публичного demo visitor

1. Посетитель вводит опубликованные demo login/password.
2. Server создаёт auth session и чистый sandbox из active publication.
3. Он видит обычный каталог Akeda Demo с 5–10 тщательно подготовленными
   изделиями разных типов и понятными preview.
4. В каждом showcase-изделии уже есть 2–4 правдоподобные AI-команды: исходная
   постановка, изменение размера/секции/материала и результат проверки.
5. Посетитель может вращать 3D, открывать фасады, менять параметры, делать
   ephemeral duplicate и отправлять разрешённые AI-команды.
6. Верхняя спокойная полоса сообщает: `Демо · изменения видны только вам и
   сбросятся при новом входе`, рядом `Вернуть исходное`.
7. Save и AI визуально работают как в продукте, но пишут только в sandbox.
8. Новый login показывает исходную publication без следов предыдущего
   посетителя.

В demo доступны: 3D, выбор деталей, параметры, чертёж, раскрой-preview, версии
внутри sandbox, AI в пределах quota, создание/дублирование временного изделия.

В demo запрещены: сотрудники/права, company settings, audit, постоянные upload,
произвольный artifact download, `.cfrn`, платная `.b3d`, email/invites и доступ к
customer organization.

## 9. Точная семантика reset и конкуренции demo

### Начало

- каждый successful demo login выдаёт новый `auth_session_id` и
  `demo_sandbox_id`;
- sandbox pin-ится к active `demo_publication_id` на момент входа;
- публикация нового эталона не меняет уже открытые сессии.

### Во время работы

- baseline читается immutable;
- первое изменение создаёт copy-on-write overlay;
- все tabs одной auth session используют один overlay;
- каждое изменение проверяет `base_revision_id`, поэтому два tab не затирают
  друг друга молча;
- reload не является новым входом и не сбрасывает работу.

### Сброс

- `Вернуть исходное` удаляет overlay и создаёт новый sandbox, не меняя login;
- logout немедленно помечает sandbox на удаление;
- idle expiry и absolute auth expiry делают то же;
- cleanup idempotent: повторное удаление безопасно;
- недоступный orphan sandbox удаляется фоновым GC;
- server restart допустимо сбрасывает все in-memory sandbox первой версии.

### Что можно хранить постоянно

Для anti-abuse/продуктовой аналитики допустимы только агрегаты без content:
время, hash session/IP bucket, endpoint kind, status, token count и latency.
Тексты prompt, ответы, ParamSpec, attachments, previews и email visitor в
постоянную аналитику demo не попадают.

### Лимиты

Минимум три независимых ограничения:

- per sandbox requests/tokens;
- per IP rate limit;
- global demo daily token budget/concurrency.

При исчерпании лимита 3D и локальные ручные действия продолжают работать, а UI
понятно сообщает, когда AI снова доступен.

## 10. Файловое/object storage правило

Canonical storage key строится server-side, например:

```text
org/{organization_id}/projects/{project_id}/revisions/{revision_id}/preview.png
org/{organization_id}/projects/{project_id}/artifacts/{artifact_id}.b3d
demo/publications/{publication_id}/...
```

Sandbox:

```text
runtime/demo/{demo_sandbox_id}/...
```

Один только prefix не является authorization. Перед каждым read/write metadata
row проверяется против AccessContext. Пользователь никогда не присылает
абсолютный путь; `open-file` заменяется artifact download по ID.

## 11. Поэтапная миграция существующего Studio

### Phase A — schema и repositories без изменения UI

1. Добавить migrations для catalogs/projects/revisions/chat/artifacts/jobs и
   identity-полей demo.
2. Реализовать persistent repositories и permission-aware service layer.
3. Добавить `AccessContext` middleware и deny-by-default helpers.
4. Не подключать customer traffic; существующий локальный Studio работает как
   раньше за feature flag.

Гейт: repository tests с двумя организациями; ни один get/list/update по ID
другой организации не проходит.

### Phase B — импорт текущих файлов в одну выбранную компанию

Мигратор работает только с явным `--organization-id`, сначала в `--dry-run`:

1. делает backup/manifest с SHA-256;
2. создаёт default catalog;
3. каждый валидный `paramspecs/*.json` импортирует как project + initial
   revision;
4. `<stem>.versions.json` импортирует по времени как последовательные immutable
   revisions, дедуплицируя одинаковые hashes;
5. `.previews/<stem>.png` привязывает к соответствующей current revision;
6. axon SVG не переносит — он производный и перестраивается;
7. `project.json`, `.cfrn`, `.b3d` и delivery files импортирует как artifacts
   только при однозначном соответствии stem/revision/checksum;
8. строки общего `builds.json`, которые нельзя однозначно сопоставить, помещает
   в migration report, но не угадывает tenant;
9. chat не импортирует: постоянной истории сейчас нет;
10. проверяет количество проектов/revisions/artifacts и может полностью
    откатить импорт по migration batch ID.

Файлы-источники до отдельного подтверждения не удаляются.

### Phase C — tenant-aware catalog и read-only Studio

1. Переключить каталог с filesystem scan на repositories.
2. Убрать `st.spec/st.spec_path` из request path; каждый request получает project
   и revision по ID.
3. Подключить existing `build_payload`, techview, nesting и неизменённый
   `MebelScene` к repository ParamSpec.
4. Запустить employee и platform inspect в режиме чтения.

Гейт: два пользователя параллельно открывают разные изделия без смены состояния
друг друга; 3D parity со старым Studio exact.

### Phase D — revisions и persisted AI history

1. Save/new/duplicate/import создают tenant-scoped records.
2. Save требует `base_revision_id`; restore/undo создают новые revisions.
3. Chat messages/operations сохраняются транзакционно.
4. Левый журнал Studio загружается с API и пагинируется.
5. Cancel/error/conflict не оставляют current revision в промежуточном состоянии.

Гейт: refresh и повторный вход сохраняют customer chat; другой tenant не может
получить thread/message/operation даже по известному UUID.

### Phase E — artifacts и производство

1. Перевести previews, project.json, cfrn, b3d, delivery и builds history на
   artifact/job repositories.
2. Удалить arbitrary path из API.
3. Добавить server-side permission/cost/idempotency checks.
4. Подключить tenant quotas и audit.

Гейт: build одного tenant не отображается и не скачивается другим; повтор
одного idempotency key не списывает деньги дважды.

### Phase F — новый platform view

1. Заменить support form основной кнопкой `Открыть компанию`.
2. Добавить platform inspect route, banner и возврат к платформе.
3. Подключить каталоги, employees, activity и AI history.
4. Оставить actor identity и audit на всех чувствительных чтениях.

Гейт: в customer audit виден platform actor; ни одна запись не выглядит как
действие сотрудника; banner остаётся на всех company routes.

### Phase G — demo publication и ephemeral sandbox

1. Создать organization mode `demo`, shared account и curated baseline.
2. Реализовать publication draft/publish.
3. Реализовать DemoSandboxRepository и reset/GC.
4. Подключить те же Studio routes через repository dispatcher.
5. Добавить AI quotas и server-side запрет production actions.
6. Провести parallel browser test минимум на 20 sandbox.

Гейт: посетитель A меняет изделие и пишет в chat; посетитель B видит baseline;
новый login A снова видит baseline; canonical publication checksum не меняется.

### Phase H — cutover

1. Запретить старые routes без authenticated AccessContext.
2. Удалить process-global project state и legacy file writes.
3. Удалить/перенаправить старый `STUDIO_PUBLIC` protection.
4. Сохранить CLI/local adapter только как явно single-user repository.
5. После compatibility window убрать support-session UX/schema отдельной
   миграцией, не удаляя audit events.

## 12. Матрица обязательных тестов

### Tenant isolation

- list/get/save/restore/chat/preview/artifact/build между org A и org B → 404 или
  403 без раскрытия существования объекта;
- переданный чужой `organization_id` в body/header игнорируется;
- disabled membership/org/session закрывает все routes;
- background job повторно проверяет org и permission, а не доверяет enqueue body.

### Platform inspect

- только `platform.organization.inspect` открывает company workspace;
- actor в audit — platform user, не выбранный employee;
- banner присутствует в catalog, Studio, employees, activity и chat history;
- mutating и paid endpoints не становятся доступны из-за inspect context;
- enter/exit и просмотр chat фиксируются без сохранения prompt в audit.

### Revisions и chat

- одновременный save с одним base revision даёт один success и один 409;
- AI result на устаревшей revision не применяется;
- refresh/relogin возвращает полный customer thread;
- undo/restore добавляют revision, не переписывают историю;
- attachments доступны только внутри того же org/project/thread.

### Demo

- две demo auth-сессии не видят overlays друг друга;
- новый login всегда создаёт новый sandbox;
- reload той же session сохраняет overlay;
- reset, logout, expiry и GC удаляют overlay;
- публикация baseline не меняется после save/chat/import/duplicate visitor;
- b3d/cfrn/member/admin/audit endpoints запрещены server-side;
- AI limits работают per sandbox + IP + global;
- template publication atomic: новые sessions видят либо старую, либо новую
  полную version, но не смесь.

### 3D parity

- `src/webviewer.py` без diff;
- одинаковый ParamSpec даёт одинаковый `viewer_payload` до и после migration;
- вращение, выбор деталей, прозрачность, присадки, фурнитура, разбор и анимации
  фасадов/ящиков проходят существующие tests и визуальный parity review.

## 13. Definition of done

Фаза считается законченной только когда:

1. platform owner нажимает `Открыть компанию` и сразу видит её настоящий
   каталог, сотрудников и AI-историю без причины, тикета и TTL;
2. везде видно, что это platform actor, а не impersonated employee;
3. customer projects, revisions, chat, previews, jobs и artifacts имеют
   server-enforced `organization_id`;
4. два реальных tenant не могут прочитать или изменить данные друг друга;
5. demo login создаёт индивидуальный sandbox и новый login полностью чист;
6. demo visitor может реалистично попробовать Studio и AI, но не меняет
   publication и не запускает платное производство;
7. существующая 3D-модель выглядит и ведёт себя точно как до миграции;
8. старые filesystem globals больше не обслуживают многопользовательские routes.
