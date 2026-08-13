# AI rollout: shadow, canary, SLO и rollback

Этот runbook управляет переходом с синхронного Studio AI path на новый
LangGraph path. Он не разрешает deploy, платные provider-вызовы или ослабление
production gate. Любой kill switch, SLO stop или отказ rollout/checkpoint
storage возвращает запросы на существующий синхронный путь.

## Компоненты и режимы

Для каждого компонента есть независимый `AKEDA_ROLLOUT_<COMPONENT>` со
значением `off`, `shadow`, `canary` или `on` и мгновенный
`AKEDA_KILL_SWITCH_<COMPONENT>=1`:

| Component | Переменные |
|---|---|
| typed operations | `AKEDA_ROLLOUT_TYPED_OPS`, `AKEDA_KILL_SWITCH_TYPED_OPS` |
| EditEngine | `AKEDA_ROLLOUT_EDIT_ENGINE`, `AKEDA_KILL_SWITCH_EDIT_ENGINE` |
| full production gate | `AKEDA_ROLLOUT_FULL_GATE`, `AKEDA_KILL_SWITCH_FULL_GATE` |
| split prompts | `AKEDA_ROLLOUT_SPLIT_PROMPTS`, `AKEDA_KILL_SWITCH_SPLIT_PROMPTS` |
| tracing/exporters | `AKEDA_ROLLOUT_TRACING_EXPORTERS`, `AKEDA_KILL_SWITCH_TRACING_EXPORTERS` |
| LangGraph | `AKEDA_ROLLOUT_LANGGRAPH`, `AKEDA_KILL_SWITCH_LANGGRAPH` |

`STUDIO_LANGGRAPH_ORCHESTRATION=1` остаётся совместимым alias для LangGraph
`on`, если новая переменная не задана. Новый path становится primary только
когда все пять execution-компонентов (`typed_ops`, `edit_engine`, `full_gate`,
`split_prompts`, `langgraph`) разрешены. Отключение любого из них атомарно
возвращает весь запрос на безопасный legacy path — частично применённого
pipeline нет. Exporter отключается независимо и не отключает локальный
`trace_id`.

## Shadow и canary

- `shadow`: legacy остаётся ответом пользователю; кандидат получает тот же
  запрос, но использует in-memory checkpoints и `NullRevisionStore`. Его spec,
  geometry и drilling только сравниваются; кандидат не попадает в каталог,
  revisions, AI history или audit.
- `canary`: сервер выбирает участника только по проверенной session identity.
  Разрешены точные списки `AKEDA_CANARY_TENANTS`/`AKEDA_CANARY_USERS` и
  стабильный `AKEDA_CANARY_PERCENT`. Tenant/user из request body не участвуют.
- В rollout state сохраняются только 20-символьные SHA-256 prefixes, числа,
  outcomes и коды. Текст, фото, ParamSpec, prompt и секреты запрещены.

## Утверждённые бюджеты по умолчанию

| SLO | Budget / stop condition | Env override |
|---|---:|---|
| latency p50 | ≤ 5 s | `AKEDA_SLO_LATENCY_P50_MS` |
| latency p95 | ≤ 15 s | `AKEDA_SLO_LATENCY_P95_MS` |
| tokens p95 | ≤ 12,000 | `AKEDA_SLO_TOKENS_P95` |
| reported cost p95 | ≤ $0.25 | `AKEDA_SLO_COST_USD_P95` |
| invalid operation rate | ≤ 5% | `AKEDA_SLO_INVALID_OP_RATE` |
| false rejection rate | ≤ 3% | `AKEDA_SLO_FALSE_REJECTION_RATE` |
| edit success rate | ≥ 90% | `AKEDA_SLO_EDIT_SUCCESS_RATE` |
| checkpoint SQLite | ≤ 64 MiB | `AKEDA_SLO_CHECKPOINT_BYTES` |
| checkpoint retention | 7 days | `AKEDA_CHECKPOINT_RETENTION_DAYS` |

Оценка начинается после `AKEDA_SLO_MINIMUM_SAMPLES=20`, окно ограничено
`AKEDA_SLO_WINDOW_SAMPLES=200`. Превышение любого бюджета записывает stop для
всех execution-компонентов. Следующий запрос автоматически идёт legacy path.
State ограничен 2,000 events и пишется атомарно в
`<out>/.rollout/state.json`; dashboard — `<out>/.rollout/dashboard.json`.

False rejection нельзя честно вывести из обычного production запроса. Этот
label поступает из MEB-151 trace-replay/eval dataset. Корректный security refusal
имеет label `false`, а live candidate/legacy disagreement публикуется отдельно
как `live_divergence_rate`. Стоимость учитывается только если provider её
сообщил; отсутствие цены остаётся `null`, не входит в percentile и не увеличивает
`cost_samples`. Cost/false-rejection budgets включаются только после собственного
`AKEDA_SLO_MINIMUM_SAMPLES`, а не по общему числу live запросов.

False-rejection denominator принимает только полный `trace-eval-case-v1` с
`ok=true`, пустым списком mismatches, совпавшими decision/output/verdict digests
и candidate status из строгого enum `accepted|replied|rejected`. Missing,
timeout, unknown, failed и inconclusive не считаются успешным отказом и не
снижают rate: они увеличивают `false_rejection_inconclusive_samples`.

## Checkpoints и деградация хранилища

До `graph.invoke` SQLite атомарно регистрирует active lease. Prune не удаляет
leased thread, а незарегистрированные checkpoint/write threads сначала ждут
`AKEDA_CHECKPOINT_ORPHAN_GRACE_SECONDS=300`, чтобы concurrent request не был
принят за crash orphan. SQLite хранит максимум
`AKEDA_CHECKPOINT_MAX_PER_THREAD=64` checkpoints на thread и
`AKEDA_CHECKPOINT_MAX_THREADS=100` threads. Завершённые threads старше
retention удаляются вместе с pending writes. После очистки контролируется
фактический размер файла. Ошибка открытия, записи, integrity/budget failure
помечает storage degraded и возвращает запрос на legacy path; новая ревизия из
degraded graph не сохраняется.

## Dashboard и связанные evidence gates

Dashboard-as-code: `ops/dashboards/ai-rollout.json`. Его source — компактный
локальный `dashboard.json`. Перед расширением canary приложить три независимых
evidence:

1. MEB-149 engine matrix: `python -m qa.engine_checks` (когда ветка принята).
2. MEB-151 replay/evals: `python main.py trace-eval` (когда ветка принята),
   включая false rejection и shadow parity.
3. MEB-153 paid-provider bake-off: только ручной запуск с отдельным cost budget;
   CI и этот drill платные API не вызывают.

Отсутствующий/непринятый evidence не блокирует offline разработку, но блокирует
production rollout и перевод задачи в «Готово».

## Rollback drill без deploy

```bash
cd tools/basis
python -m qa.rollout_drill
```

The CI drill uses real offline HTTP routing, login/session/CSRF and SQLite/JSON
storage adapters. It independently executes the legacy primary and LangGraph
shadow, then forces a checkpoint budget failure. The gate requires measured
`provider_calls=0`, `production_writes=0`, a persistent scoped stop and an
observed `rollout.primary=legacy` fallback; it never deploys.

Reviewer invariants: SLO events/stops are scoped by the complete
`mode + tenant_hash + cohort` key, and dashboard red state is sourced from
persistent `state.stops`, not only the rolling window. Shadow usage, result
code and success are candidate metrics; live divergence is separate from
MEB-151-labelled false rejection. ParamSpec, geometry and drilling parity use
separate samples. Retention enumerates checkpoint/write tables and applies
active lease plus orphan grace before deletion. Retention, budget, checkpoint write and atomic
revision-flush failures latch degraded plus the scoped stop.

Drill использует только rule-based mock path: доказывает отсутствие shadow
revision writes, создаёт намеренное нарушение latency SLO, проверяет переход
`graph → legacy`, затем имитирует недоступный checkpoint root и проверяет
degraded fallback. В отчёте должны быть `provider_calls: 0` и
`production_writes: 0`.

## Операционный rollback

1. Установить kill switch затронутого компонента (при неясной причине — всех
   execution-компонентов).
2. Перезапустить только штатным release process после одобрения deploy; ручных
   правок production файлов не делать.
3. Проверить, что новые ответы имеют `rollout.primary=legacy` и stop reason.
4. Сохранить privacy-safe dashboard и trace/eval IDs как evidence.
5. Исправлять причину в отдельной ветке; очистить автоматические stops только
   осознанным локальным/операторским действием после зелёных matrix/evals.

Этот PR не выполняет production drill: он доказывает механизм локально. Реальная
canary-проверка требует принятого SHA, deploy authorization, provider budget и
живого tenant traffic и проводится отдельным этапом.
