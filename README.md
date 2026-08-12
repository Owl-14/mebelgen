# mebelgen / Akeda Studio

Единственный актуальный продукт репозитория — **Akeda Studio** в
[`tools/basis`](tools/basis). Текущий поток данных:

```text
ТЗ → ParamSpec → детерминированный генератор → проверки → Studio → .cfrn/.b3d
```

LLM извлекает намерение и параметры, но не рассчитывает координаты деталей.
Геометрию строит только код генераторов из `tools/basis/src/generators/`.

```text
tools/basis/    продукт, Studio, генераторы, схемы, тесты и production-интеграции
input_specs/    исходные ТЗ и эталонные входные материалы
docs/           командный процесс и технические исследования
```

Старый отдельный конвейер референс-листов с Blender удалён из актуального
дерева. Он не является частью Studio, 3D-движка или производственного контура;
при необходимости его исходники доступны только в истории Git.

Быстрый старт и команды: [tools/basis/README.md](tools/basis/README.md).
Актуальные предметные правила: [tools/basis/AGENTS.md](tools/basis/AGENTS.md) и
[tools/basis/rules/](tools/basis/rules/). Карта документации:
[docs/README.md](docs/README.md).

## Работа над проектом

Перед любой задачей прочитайте корневой [AGENTS.md](AGENTS.md). Он задаёт
обязательные правила для людей и агентов: отдельные ветки/worktree, защита
чужих изменений, проверки, PR и безопасный deploy.

- [Командный workflow и handoff](docs/TEAM_WORKFLOW.md)
- [Проверки по типам задач](docs/TASK_PLAYBOOKS.md)
- [Предметные правила tools/basis](tools/basis/AGENTS.md)
- [Production runbook](tools/basis/ops/runbook.md)
