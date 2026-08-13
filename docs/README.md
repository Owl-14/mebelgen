# Карта документации

## Источники правды

Актуальные инструкции и контракты продукта читаются в таком порядке:

1. [`../AGENTS.md`](../AGENTS.md) — командная работа, Git, проверки и deploy.
2. [`../tools/basis/AGENTS.md`](../tools/basis/AGENTS.md) — предметная карта Akeda Studio.
3. [`../tools/basis/rules/`](../tools/basis/rules/) — геометрия, ParamSpec,
   фурнитура, материалы, Studio, доступы и выдача.
4. [`TEAM_WORKFLOW.md`](TEAM_WORKFLOW.md) и [`TASK_PLAYBOOKS.md`](TASK_PLAYBOOKS.md)
   — процесс работы и проверки по типам задач.
5. [`../tools/basis/ops/runbook.md`](../tools/basis/ops/runbook.md) — production.

Для подготовки лицензированной Windows-среды БАЗИС без установки и платных
вызовов используется [`BASIS_ENV_PREFLIGHT.md`](BASIS_ENV_PREFLIGHT.md).

Если исследование ниже противоречит `tools/basis/rules/`, действуют правила.

## Технические исследования

Эти документы сохраняют результаты экспериментов и снимки внешних API. Они не
являются текущим продуктовым контрактом и не дают разрешения на платные вызовы:

- `BASIS_APILIST_RESEARCH.md`, `BASIS_AUTOMATION.md` — доступные API БАЗИС;
- `BASIS_FASTENERS_REVERSE.md`, `CFRN_HOLES_FORMAT.md` — реверс производственных
  файлов и присадок;
- `bazis_cloud_*_swagger.json` — исторические снимки Swagger для сверки клиента.

## Удалённое наследие

Ранний Blender-конвейер референс-листов, обзор предыдущего репозитория, черновой
production workflow и старые отчёты выполненных QA-задач удалены из рабочего
дерева. Они доступны в истории Git для археологии, но не должны использоваться
агентами как архитектура Akeda Studio.
