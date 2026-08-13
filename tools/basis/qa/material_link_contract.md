# Offline evidence: Cutting `set-link-materials`

Задача: MEB-139. Этот файл отделяет локально доказуемый контракт от проверки,
которая возможна только в реальной лицензированной базе БАЗИС/MatBase.

## Что подтверждает сохранённый Swagger

Источник: `docs/bazis_cloud_cutting_swagger.json`.

- `GET /cad-models/{id}/materials` возвращает только массив строк — имена
  материалов, распознанные в загруженной CAD-модели.
- `POST /cad-models/{id}/set-link-materials` принимает массив
  `CuttingMaterialLinkDTO`: `originalMaterialFullName`, `materialType`,
  `linkedMaterialFullName`. Полей артикула, MatBase id или размера листа нет.
- `GET /cutting-materials?orderId=...` возвращает `name`, `article`,
  `inMaterialBaseId` и листы (`height`, `width`, `count`, `production`,
  `materialType`).
- `GET /orders/{id}/cutted-materials` возвращает итоговый материал и статистику
  раскроя, включая `boardsCount`/`countPlate`.

Публичный Swagger не содержит endpoint поиска MatBase по имени/артикулу. Поэтому
целевое `linkedMaterialFullName` нельзя честно вывести из локального
`baza_materiala.json`, `catalog.json::basisName` или артикула.

## Что закрыто локально

`src/material_link_contract.py` и synthetic fixture
`tests/fixtures/material_link_contract.json` проверяют:

1. name/article/геометрия листов остаются связаны через реально используемые
   `materialIndex` панелей экспортированного CFRN;
2. exact name имеет приоритет, fallback допускает только единственное совпадение
   после нормализации регистра/пробелов;
3. без явно подтверждённого `linkedMaterialFullName`, при неоднозначности или
   несовпадении артикула payload не строится;
4. клиент не отправляет поля вне `CuttingMaterialLinkDTO`;
5. offline-аудит требует полностью ready plan, точный артикул и точное множество
   ожидаемых листов со строгими numeric/bool/enum типами, MatBase id и
   статистику раскроя;
6. одинаковые exact CFRN names считаются неоднозначными, даже если отличаются
   артикулом; словарное схлопывание запрещено.

Значения вида `__FIXTURE_*__` и `inMaterialBaseId: 1` — только синтетические
sentinel-данные теста. Они не являются и не объявляются реальными именами,
артикулами или id БАЗИС.

## Exact blocker реальной базы

Реальная проверка требует всех следующих внешних действий:

1. лицензированная среда возвращает точное MatBase-имя, которое оператор
   визуально сверяет с ожидаемым декором;
2. создаётся Cutting order, загружается модель и читается её список материалов;
3. выполняется `set-link-materials` с подтверждённым именем;
4. повторно читаются cutting materials и визуально проверяется MatBase;
5. запускается раскрой/production generation и проверяются реальные выходные
   файлы и статистика.

На текущем прогоне эти действия запрещены постановкой задачи: нельзя вызывать
платные Basis/Cutting API и нельзя выполнять внешний прогон. Поэтому локально
подтверждены форма, mapping и fail-closed поведение, но **не подтверждены**
реальное имя/артикул MatBase, визуальный декор и производственный результат.

Удалённый POST `set-link-materials` также **не считается идемпотентным**:
сохранённый Swagger не даёт такой гарантии, а live evidence запрещён. Клиент не
делает автоматических retries; после timeout с неизвестным исходом сначала
нужно отдельно разрешённое чтение/reconciliation состояния модели.

Для будущего разрешённого прогона fixture следует получить из неперсонального
тестового заказа, заменить реальные значения на стабильные redacted aliases и
сохранить отдельно исходный evidence manifest (order/model ids, timestamp,
версия Swagger и hashes выходных файлов). Секреты/API key в fixture не входят.
