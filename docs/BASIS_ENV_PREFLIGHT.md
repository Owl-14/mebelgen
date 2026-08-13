# Preflight лицензированной Windows-среды БАЗИС

Этот runbook относится к MEB-137 (ранее AKD-12). Он позволяет собрать
воспроизводимое evidence для локального БАЗИС-Мебельщика, не устанавливая
программу, не активируя лицензию, не вызывая Basis Cloud/Cutting API и не меняя
production.

Репозиторий не является источником точных имён MatBase. Значения `basisName`,
артикулы и кромки считаются подтверждёнными только после проверки оператором в
лицензированной или официальной trial-среде БАЗИС и сохранения manifest по
шаблону ниже.

## Автоматический read-only preflight

Запускать из корня репозитория в обычном PowerShell, без повышения прав:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\basis\scripts\basis_env_preflight.ps1 -Json
```

Скрипт только читает HKLM uninstall registry, ограниченный набор стандартных
каталогов, Authenticode/file-version metadata обнаруженных executable,
репозиторную базу материалов и переданный verification manifest. Он не запускает
executable БАЗИС, не пишет в registry и каталоги БАЗИС, не проверяет лицензию
обходными способами и не обращается в сеть.

Executable считается evidence только одновременно при валидной Authenticode-
подписи поставщика БАЗИС и согласованных `ProductName`, `CompanyName`,
`ProductVersion`. Имя файла (`mebel.exe`) само по себе ничего не доказывает.
Поиск выполняется без `-Recurse`: только canonical install root и фиксированные
одноуровневые `Bin`/`Program`. Reparse points и пути вне `Program Files`,
`LocalAppData\Programs` либо доверенного HKLM `InstallLocation` отклоняются.

Явный путь — только уточняющая подсказка, а не расширение доверенной области:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\basis\scripts\basis_env_preflight.ps1 `
  -BasisInstallPath 'C:\Program Files\Bazis' `
  -ScriptsPath "$HOME\Documents\BazisN\Scripts" `
  -VerificationManifestPath 'D:\MEB-137\basis-verification.json' `
  -Json
```

Код выхода `0` означает, что все обнаруженные evidence и manifest взаимно
согласованы; `2` — перечислены blocker-коды. Отчёт сам по себе ничего не
устанавливает и не лицензирует.

## Что уже можно подтвердить без БАЗИС

- импортёр: `tools/basis/scripts/ImportFurnitureFromJSON.js`;
- кандидат пользовательского каталога скриптов из существующей инструкции:
  `%USERPROFILE%\Documents\BazisN\Scripts` (фактический путь подтвердить в
  установленной версии);
- источник импорта базы: `tools/basis/materials/source/baza_materiala.xlsx`;
- нормализатор: `tools/basis/scripts/import_materials_base.py`;
- нормализованная база: `tools/basis/materials/baza_materiala.json`, контракт
  `material-base-v1`;
- импортёр рассчитан на API БАЗИС 2026.x; исторически исследовалась версия
  `2026.5.6.0 (64-bit)`, но это не доказательство версии конкретной машины.

Исходный XLSX и JSON не перезаписывать в рамках preflight. Повторный импорт базы
— отдельное осознанное изменение с review diff и полными тестами.

## Операторский чеклист снятия blocker

1. Предоставить Windows-машину с легальной лицензией или официальной trial-версией.
2. Запустить preflight и записать точные `ProductVersion`, canonical install
   path, SHA-256 executable и Authenticode subject. Не копировать license-файлы,
   ключи или machine identifiers. Если подпись невалидна или поставщик не
   совпадает, остановиться и запросить официальный дистрибутив.
3. В UI БАЗИС подтвердить фактический каталог пользовательских JS-скриптов.
   Скопировать туда `ImportFurnitureFromJSON.js` вручную; исходник в репозитории
   не менять на машине оператора.
4. Импортировать производственную базу штатным способом БАЗИС. Зафиксировать
   SHA-256 использованного `baza_materiala.json`, итоговый статус, число входных,
   импортированных/отклонённых позиций и количества плит/кромок. Сохранить
   непустой локальный отчёт/скриншот и его SHA-256, не выгружая секретные
   настройки лицензии. Одного совпадения исходных файлов недостаточно.
5. В MatBase найти минимум одну плиту корпуса и одну кромку. Перенести точные
   отображаемые имена, артикулы и толщину кромки в manifest. Не угадывать и не
   нормализовать названия вручную.
6. Открыть БАЗИС-Мебельщик, запустить JS через штатное меню пользовательских
   скриптов и выбрать локальный fixture `tools/basis/projects/moderator_cabinet.json`
   либо другой заранее согласованный project JSON.
7. Убедиться, что создана новая модель, число панелей совпадает с JSON и alert
   импорта завершился без ошибки. Сохранить нативную модель `.b3d` в отдельный
   временный каталог и проверить её штатным offline parser: `BZ85`, узлы Model и
   Document, 64-байтный trailer. JSON с расширением `.b3d` evidence не является.
8. В самой модели проверить назначение подтверждённой плиты и кромки. Не запускать
   раскрой, облачную конвертацию или другие платные операции.
9. Заполнить manifest, повторить preflight с `-VerificationManifestPath` и
   приложить JSON-отчёт, manifest и обезличенный скриншот результата к MEB-137.

## Verification manifest

Manifest хранится вне репозитория, если содержит внутренние пути производства.
Минимальный формат:

```json
{
  "schemaVersion": "basis-verification-v2",
  "basis_version": "2026.5.6.0",
  "basis_install_path": "C:\\Program Files\\Bazis",
  "basis_executable_sha256": "<SHA-256 подписанного executable БАЗИС>",
  "scripts_path": "C:\\Users\\operator\\Documents\\BazisN\\Scripts",
  "material_base_sha256": "<SHA-256 tools/basis/materials/baza_materiala.json>",
  "material_import": {
    "method": "basis-material-import",
    "result": "pass",
    "outcome": "completed",
    "basis_version": "2026.5.6.0",
    "basis_install_path": "C:\\Program Files\\Bazis",
    "input_path": "C:\\MEB-137\\baza_materiala.json",
    "input_sha256": "<тот же SHA-256 базы>",
    "input_count": 5047,
    "imported_count": 5047,
    "rejected_count": 0,
    "board_count": 963,
    "edge_count": 325,
    "evidence_type": "basis-material-import-report",
    "evidence_path": "C:\\MEB-137\\material-import-report.png",
    "evidence_sha256": "<SHA-256 отчёта/скриншота>"
  },
  "materials": [
    {
      "slot": "board",
      "basisName": "<точное имя из MatBase>",
      "article": "<точный артикул>",
      "thickness_mm": 16
    }
  ],
  "edges": [
    {
      "basisName": "<точное имя кромки из MatBase>",
      "article": "<точный артикул>",
      "thickness_mm": 2.0
    }
  ],
  "js_smoke": {
    "script_sha256": "<SHA-256 ImportFurnitureFromJSON.js>",
    "fixture": "C:\\MEB-137\\moderator_cabinet.json",
    "fixture_sha256": "<SHA-256 fixture>",
    "result": "pass",
    "output_model": "C:\\MEB-137\\smoke-result.b3d",
    "output_model_sha256": "<SHA-256 сохранённой модели>",
    "model_type": "BZ85",
    "magic_hex": "425A3835",
    "section_marker_hex": "010000FF",
    "file_size_bytes": 123456,
    "header_root_name": "Header",
    "document_root_name": "Document",
    "parser_result": "pass",
    "model_node_found": true,
    "document_section_found": true,
    "document_compressed": true,
    "trailer_bytes": 0
  },
  "checked_at": "<RFC3339>"
}
```

Preflight принимает только `schemaVersion=basis-verification-v2` и требует,
чтобы версия, install/scripts paths и SHA-256 совпадали с evidence, найденными в
этом же запуске. `materials[].slot` допускает только точное `board`, а все
`thickness_mm` должны быть конечными положительными JSON-числами. `checked_at`
должен быть не старше семи суток и не более чем на пять минут в будущем.
Fixture и output model должны существовать локально и совпадать по хэшам;
output обязан иметь расширение `.b3d`, magic `BZ85`, section marker, валидный
Adler-32, полностью разобранное дерево `Document` с реальным узлом `Model` и
полный parser evidence. `trailer_bytes` — вычисленное значение (`0` или `64`),
а не константа оператора. Произвольный непустой JSON не снимает blocker.

Manifest не должен содержать license keys, токены, содержимое license-файлов,
пароли, machine identifiers или пользовательские ТЗ.

## Критерий blocker

Задача остаётся внешне заблокированной, если отсутствует хотя бы одно:

- легальная запускаемая Windows-среда БАЗИС с подтверждённой версией;
- подтверждённый каталог пользовательских скриптов;
- успешный штатный импорт производственной базы;
- точные `basisName` и артикулы плиты/кромки из MatBase;
- успешный локальный JS smoke с сохранённой моделью.

Локальные unit/regression-тесты доказывают безопасность кода репозитория, но не
заменяют это внешнее evidence.
