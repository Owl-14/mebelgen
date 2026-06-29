# BASIS Apilist research

Дата проверки: 2026-06-29.

Цель: понять, может ли БАЗИС-Облако/АпиЛист принимать наше ТЗ/JSON/скрипт и
возвращать готовый `.b3d`, либо АпиЛист закрывает только часть пайплайна после
создания `.b3d`.

## 1. Короткий вывод

На публичных официальных endpoint-ах сейчас **не подтверждено**, что мы можем
создать задачу выполнения клиентского скрипта через REST.

Что подтверждено официальными публичными источниками:

- страница АпиЛиста говорит, что услуга поддерживает задачи, справочники
  скриптов и выполнение клиентского скрипта;
- публичный Tasks OpenAPI показывает только:
  - список задач;
  - чтение/удаление задачи;
  - скачивание результата;
  - `drawing-convert`;
  - `model-convert`;
- `ExecuteClientScript` есть в enum типа задачи, но публичного `POST`, который
  создает такую задачу, в Swagger нет;
- Cutting Public API умеет принимать `.b3d`/`.cfrn`/другие модели в заказ,
  связывать материалы, запускать раскрой и генерировать производственные файлы.

Практический вывод:

```text
На сегодня надежный MVP-сценарий:
ТЗ -> наш JSON -> локальный/операторский БАЗИС-скрипт -> .b3d
   -> Cutting API / АпиЛист для загрузки, раскроя, конвертаций, производственных файлов
```

Облачный сценарий:

```text
ТЗ -> JSON -> Apilist ExecuteClientScript -> .b3d
```

пока остается гипотезой, которую надо подтвердить в личном кабинете или у
поддержки БАЗИС-Центра.

## 2. Проверенные официальные источники

### АпиЛист landing page

URL: https://baz-plus.ru/cloud/tasks

Страница описывает:

- отправку API-запроса для выполнения задачи;
- получение статуса задачи;
- скачивание результата задачи;
- управление справочниками со скриптами;
- хранение результатов;
- задачи:
  - конвертация чертежей;
  - конвертация моделей `.cfrn <-> .b3d`;
  - выполнение клиентского скрипта.

Важная формулировка: при передаче модели или чертежа можно выбрать необходимый
скрипт и выполнить его.

### Tasks OpenAPI

URL: https://cloud.bazissoft.ru/openapi-tasks/index.html

Swagger JSON:

```text
https://cloud.bazissoft.ru/openapi-tasks/swagger/Tasks/swagger.json
```

Проверенные публичные paths:

```text
GET    /api-cloud-tasks-public/tasks
GET    /api-cloud-tasks-public/tasks/{id}
DELETE /api-cloud-tasks-public/tasks/{id}
POST   /api-cloud-tasks-public/tasks/drawing-convert
POST   /api-cloud-tasks-public/tasks/model-convert
GET    /api-cloud-tasks-public/tasks/{id}/download-result
```

Enums:

```text
CloudTaskStateEnum: 0=Created, 1=Running, 2=Success, 3=Failed
CloudTaskTypeEnum: 0=ExecuteClientScript, 1=DrawingConvertation, 2=Model3DConvertation
DrawingConvertFormatEnum: 0=Pdf, 1=Jpeg, 2=Wmf, 3=Svg
Model3DConvertTypeEnum: 0=B3dToCfrn, 1=CfrnToB3d
```

Авторизация в Swagger UI делается через заголовок:

```text
apiKey: <ключ>
```

Это видно в `/openapi-tasks/swagger/api-key.js`.

### Cutting OpenAPI

URL: https://cloud.bazissoft.ru/openapi/index.html

Swagger JSON:

```text
https://cloud.bazissoft.ru/openapi/swagger/Cutting/swagger.json
```

Это не API запуска скриптов, а публичный API коммерческого раскроя/заказов.

Ключевые возможности:

- `POST /api-cutting-public/orders` — создать заказ;
- `POST /api-cutting-public/cad-models` — загрузить модели в заказ.
  Поддерживаемые расширения из Swagger:
  `.b3d`, `.fr3d`, `.shn`, `.obl`, `.oblx`, `.zbprj`, `.xml` bCad, `.k3bz`, `.cfrn`;
- `GET /api-cutting-public/cad-models/{id}/materials` — получить материалы модели;
- `POST /api-cutting-public/cad-models/{id}/set-link-materials` — связать материалы;
- `POST /api-cutting-public/orders/{id}/run-cutting` — запустить раскрой;
- `POST /api-cutting-public/orders/{id}/run-generation-production-files`;
- `GET /api-cutting-public/orders/{id}/production-files-url`;
- `POST /api-cutting-public/orders/{id}/run-generation-control-program-files`;
- `GET /api-cutting-public/orders/{id}/control-program-files-url`.

## 3. Что изменилось относительно старого repo

Файл `test_apilist/API_FINDINGS.md` из `zazabag/mebel-bazis` оказался актуальным:
текущий Swagger совпадает с сохраненным старым Swagger:

- те же 5 paths;
- те же 4 schemas;
- endpoint создания `ExecuteClientScript` по-прежнему не публичен.

Файл `Архитектура_агента.md`, где сказано, что облако подтвержденно запускает
JS-скрипты по REST, надо считать устаревшим/слишком оптимистичным.

## 4. Возможные сценарии стека

### Сценарий A: полный cloud-runner через АпиЛист

```text
ТЗ/таблица/фото
  -> LLM/parser
  -> BasisProductionModel JSON
  -> Apilist ExecuteClientScript
  -> БАЗИС-скрипт в облаке строит .b3d
  -> download-result
  -> Cutting API / production files
```

Статус: **не подтвержден публичным OpenAPI**.

Что нужно доказать:

- в личном кабинете есть загрузка скрипта в справочник;
- есть UI-запуск script-задачи;
- есть API/скрытый/партнерский endpoint для создания script-задачи;
- можно передать входной JSON;
- скрипт может создать и вернуть `.b3d`.

Плюсы:

- Linux backend без Windows/GUI;
- проще масштабировать;
- меньше боли с лицензиями.

Риск:

- если endpoint только UI/ручной, для SaaS не подходит без отдельного договора.

### Сценарий B: локальный БАЗИС строит `.b3d`, облако дальше обрабатывает

```text
ТЗ
  -> LLM/parser
  -> BasisProductionModel JSON
  -> локальный БАЗИС-Мебельщик / оператор запускает импортёр
  -> .b3d
  -> Cutting API: заказ, материалы, раскрой, производственные файлы
```

Статус: **самый реалистичный MVP**.

Плюсы:

- не зависит от скрытого cloud-script API;
- можно тестировать сразу на триальной версии БАЗИСа;
- технолог видит модель в привычной среде.

Минусы:

- на старте есть человек/оператор;
- для полной автоматизации потом нужен локальный runner, CLI, RPA или договор с БАЗИСом.

### Сценарий C: Windows runner

```text
ТЗ
  -> backend
  -> JSON
  -> Windows worker with BAZIS
  -> автозапуск скрипта / RPA
  -> .b3d
  -> Cutting API
```

Статус: **fallback**.

Из старого VPS-теста: БАЗИС 2026 стартовал на Windows Server 2022 без GPU, но
CLI автозапуск и headless save не были доведены до конца.

### Сценарий D: Cutting API `products/from-configurator`

```text
ТЗ -> JSON конфигуратора -> Cutting API products/from-configurator -> product/model
```

Статус: **гипотеза**.

Swagger показывает endpoint `POST /api-cutting-public/products/from-configurator`,
но схема тела пустая и нет публичной документации формата. Возможно, это работает
только с заранее настроенным БАЗИС-конфигуратором/товаром, а не с произвольной
корпусной мебелью.

Это надо спрашивать у поддержки отдельно.

## 5. Что тестировать в твоей триальной версии

### Test 1: Tasks API доступ и авторизация

Цель: понять, работает ли ключ и видим ли задачи.

```bash
export BAZIS_API_KEY="..."
node basis/apilist/check_tasks_api.mjs --list
```

Ожидаем:

- HTTP 200;
- список задач или пустой список;
- если 401, ключ/тариф/заголовок не подходит.

### Test 2: model-convert B3D -> CFRN

Нужен любой маленький `.b3d`.

```bash
export BAZIS_API_KEY="..."
node basis/apilist/check_tasks_api.mjs --model-convert path/to/model.b3d --convert-type 0
```

Проверяем:

- создается задача;
- polling до `Success`;
- скачивается результат;
- что именно лежит в download-result.

### Test 3: есть ли script-задачи в списке после ручного запуска

В личном кабинете:

1. загрузить тестовый клиентский скрипт в справочник, если UI это позволяет;
2. запустить его вручную на модели/чертеже, если UI это позволяет;
3. выполнить:

```bash
node basis/apilist/check_tasks_api.mjs --list
```

Если в списке появится задача `type=0`, значит API хотя бы видит script-task.
Дальше надо выяснить, можно ли ее создать программно.

### Test 4: поиск script endpoint в личном кабинете

В DevTools браузера при ручном запуске скрипта:

- открыть Network;
- запустить задачу выполнения скрипта;
- найти запрос, который создает задачу;
- сохранить URL, method, headers, request body;
- сравнить, является ли это публичным `/api-cloud-tasks-public/...` или внутренним API.

Это самый быстрый способ понять, есть ли скрытый endpoint.

### Test 5: локальный БАЗИС импортирует JSON

В БАЗИС-Мебельщике:

1. запустить `basis/scripts/detect_basis_api.js`;
2. сохранить `basis-api-report.json`;
3. запустить `basis/scripts/import_basis_model.js`;
4. выбрать `basis/samples/wardrobe-basic.json`;
5. проверить `.b3d`.

Это докажет MVP-путь независимо от АпиЛиста.

### Test 6: Cutting API принимает наш `.b3d`

После успешного `.b3d`:

1. создать заказ через Cutting API;
2. загрузить `.b3d` в `/cad-models`;
3. получить материалы модели;
4. связать материалы с базой производства;
5. запустить раскрой/production files.

## 6. Вопросы в БАЗИС-Центр

1. Как программно создать задачу `ExecuteClientScript`?
2. Есть ли endpoint, не показанный в публичном Tasks OpenAPI?
3. Если запуск скрипта возможен через API:
   - как передается входной JSON;
   - как выбирается script_id;
   - можно ли запускать скрипт без входной модели;
   - может ли скрипт вернуть `.b3d` и дополнительные файлы;
   - какие runtime limits;
   - какие модули JS API доступны в облаке;
   - можно ли использовать `require('fs')`;
   - можно ли использовать `modelIOOperations.SaveModelToFile`.
4. Что такое `products/from-configurator` в Cutting API?
5. Можно ли через него передать конфигурацию произвольной корпусной мебели и получить модель?
6. Какой тариф нужен для API, справочников скриптов, хранилища и раскроя?
7. Можно ли получить тестовый API key и sandbox без списания платных единиц?

## 7. Рекомендуемый путь сейчас

Не ждать идеального облака.

Параллельно идти двумя дорожками:

1. **MVP production path**
   - триальная локальная версия БАЗИСа;
   - загрузка вашей базы материалов;
   - наш JSON importer;
   - ручной запуск скрипта;
   - `.b3d` на проверку технологу.

2. **Cloud discovery path**
   - получить API key;
   - проверить Tasks API;
   - проверить model-convert;
   - проверить Cutting API;
   - через DevTools/поддержку выяснить `ExecuteClientScript`.

Решение по архитектуре принимаем после этих тестов:

- если script endpoint найден и работает, уходим в cloud-runner;
- если нет, оставляем cloud для post-processing, а построение `.b3d` делаем локальным
  БАЗИСом или Windows-runner.

