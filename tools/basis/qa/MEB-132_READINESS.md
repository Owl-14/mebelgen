# MEB-132 — readiness производственного контура

Статус этого документа: **offline acceptance report**, не доказательство
реального E2E в БАЗИС.

## Закрытый локальный контур

`ParamSpec → production_gate → project.panels/material_refs/hardware → CFRN →`
`B3D preflight` теперь имеет единый запретительный барьер перед платным
`model_convert`. Прямой CLI с `project.json` больше не обходит проверки Studio.
Красная schema/mapping, geometry, holes parity, drilling или materials означает
ноль вызовов cloud-клиента.

Воспроизводимый сводный отчёт:

```bash
cd tools/basis
python qa/meb132_readiness.py
```

Отчёт сканирует все репозиторные ParamSpec через канонический production gate,
возвращает error codes по каждому изделию и никогда не вызывает Basis, Cutting
или LLM API.

## Внешняя acceptance-граница

Следующие пункты остаются `blocked` до фактического evidence из лицензированной
среды и не могут быть выведены из локальных fixtures:

| Gate | Owner | Требуемое evidence |
|---|---|---|
| Лицензированная версия БАЗИС и производственная база | MEB-137 | версия, путь скриптов, запуск API probe, manifest базы |
| BasisProductionModel JSON → нативные панели → сохранённый `.b3d` | MEB-138 | importer report, файл `.b3d`, габариты/ориентации/кромка/присадки |
| name/article/sheet → MatBase | MEB-139 | фактическое сопоставление и визуальная/производственная проверка |
| `.b3d` → Cutting → production files | MEB-140 | разрешённый test order, ответы endpoints и файлы результата |

До закрытия этих четырёх gate итоговый `ready` обязан оставаться `false`.
Репозиторный `.b3d` fixture доказывает только работу парсера и зафиксированные
производственные шаблоны; он не заменяет новый native import E2E.
