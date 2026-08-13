# Локальный путь `.b3d` без Basis APIList

## Статус доказательств

Есть три разных утверждения, их нельзя смешивать:

1. **Доказано offline:** Akeda детерминированно строит `project.json`/`.cfrn`,
   production gate проверяет геометрию и присадки, BZ85-парсер читает оба
   репозиторных реальных `.b3d`, а parse → serialize → parse сохраняет дерево.
2. **Доказано только на известных артефактах:** облачный fixture содержит
   64-байтный непрозрачный хвост; desktop/production fixture не содержит хвоста.
   Поэтому хвост нельзя считать обязательной подписью всех нативных B3D.
3. **Не доказано без реальной среды:** что самостоятельно сериализованный BZ85
   откроется БАЗИС и сохранит материалы, кромку, присадки и производственные
   связи. `write_b3d()` — структурный кодек, не нативный builder.

## Воспроизводимый безопасный pipeline

Подготовка не создаёт `.b3d` и не вызывает сеть:

```bash
python main.py local-b3d prepare paramspecs/<model>.json --out <empty-package-dir>
```

Команда выполняет полный production gate (для ParamSpec) либо проектный
preflight, записывает канонический `project.json`, текущий
`ImportFurnitureFromJSON.js`, инструкции, SHA-256 manifest и детерминированный
ZIP. Manifest имеет обязательные `schema`/`version`, отдельный hash/counts
`project.json` и точный непустой набор artifacts; неполный manifest отклоняется
до чтения B3D. Одинаковый вход и версия репозитория дают одинаковый ZIP.

Далее оператор в лицензированном/триальном desktop БАЗИС запускает импортёр,
выбирает `project.json`, проверяет модель и сохраняет её штатной командой как
`.b3d`. Это локальный in-app scripting API БАЗИС, а не Cloud Tasks/APIList.

Результат проверяется бесплатно:

```bash
python main.py local-b3d verify <package-dir> <saved.b3d> \
  --expected-package-sha256 <SHA-256 из prepare> --report <report.json>
```

До доверия `manifest.json` проверяется внешний trust anchor: SHA-256 исходного
deterministic ZIP, напечатанный `prepare`. Распакованные файлы обязаны побайтно
совпасть с anchored ZIP; затем фактический `project.json` повторно проходит
project preflight. Поэтому согласованная подмена project+manifest/ZIP не проходит
с исходным внешним SHA-256.

Handoff разрешён только в **новый пустой документ БАЗИС**; import-script требует
явного подтверждения, а manifest/project несут обязательный marker. Проверка
фиксирует целостность пакета, секции `Header`/`Document`, структурный
round-trip и fail-closed semantic evidence: непустые typed panel nodes, точное
число/имена панелей, `Mat` каждой панели против ожидаемого CFRN mapping и
толщину, а также точное число и multiset сигнатур `(diameter, depth)` присадок
из `FurnList` × instances. Любой неизвестный/лишний `Model/Obj Type` вне
fail-closed whitelist отклоняется (`Type=9999` — красный). Пустой synthetic
BZ85 с корректными секциями отклоняется. Даже зелёный offline-отчёт оставляет
`native_basis.status=unverified` и `confirmed=false`: финальное доказательство —
фактический open/save/reopen либо `b3d→cfrn` round-trip в лицензированном БАЗИС.

## Ограничения текущего импортёра

`ImportFurnitureFromJSON.js` создаёт панели, назначает имя/толщину и пытается
назначить материал; отдельно поддерживает ручки и направляющие. Он не доказывает
полноту кромки, всех присадок, крепежа и связей производственной модели. Поэтому
пакет — воспроизводимый hand-off для эксперимента MEB-138, а не замена
`CfrnToB3d` и не готовый производственный контракт.
