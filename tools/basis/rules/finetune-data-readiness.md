# Gate готовности данных для fine-tune ParamSpec

Fine-tune извлечения ParamSpec — условный эксперимент, а не текущий этап
разработки. До зелёного отчёта `finetune-readiness` запуск обучения запрещён.
Gate полностью offline: он не вызывает LLM, БАЗИС или Cutting API.

## Команда dry-run

```bash
python main.py finetune-readiness \
  --dataset dataset \
  --evidence qa/finetune-evidence.json \
  --output qa/finetune-readiness-report.json
```

Код возврата `0` означает только допуск к ограниченному offline-эксперименту,
а не разрешение на обучение, закупку API, rollout или deploy. Код `2` означает
`stay_in_backlog`. JSON-отчёт содержит только счётчики, коды и SHA-256, без
текста ТЗ и ParamSpec.

## Контракт одной реальной пары

Каждый `dataset/*.json` должен содержать:

- уникальный `pair_id`;
- непустой `source_tz` и полный `paramspec`, проходящий `production_gate`;
- явный `split`: `train`, `validation` или `test`;
- `leakage_group` для одного заказа/семейства вариантов;
- `review.status=approved`, `reviewer_id`, `reviewed_at`;
- `provenance.source_id` и allowlisted `source_type`: `customer_tz`,
  `internal_production_tz` или `licensed_external_tz`;
- структурированный `provenance.rights_record`: `record_id`, allowlisted
  `basis` (`customer_contract`, `explicit_consent`, `internal_ownership` или
  `dataset_license`), точный `scope=paramspec-model-development`,
  совпадающие `source_id`/`source_type`, `verified_by`, `verified_at` и SHA-256
  внешнего документа. Rights record, не привязанный к источнику пары, отклоняется.

`source_type` со значением `synthetic`, `fixture`, `generated`, `test`, `mock`
или `demo` всегда запрещён. Pair-local boolean/string вроде
`training_approved=true` или `usage_rights=training-approved` не заменяет
структурированный rights record.

Граница доверия: offline gate проверяет полноту и внутреннюю согласованность
provenance metadata и неизменяемую ссылку на документ, но не может сам доказать
юридическую подлинность документа или полномочия `verified_by`. Перед любым
экспериментом эти записи должен независимо подтвердить governance/legal reviewer.

Legacy-записи `ParamSpec→project` из `feedback.record_pair` без этих полей не
считаются чистыми парами. Тестовые fixtures также не входят в реальный отчёт.

## Измеримые условия допуска

1. Не менее 200 clean unique пар после production quality checks и dedup.
2. Нет повторов `pair_id`, нормализованного ТЗ, канонического ParamSpec или
   всей пары.
3. Разбиение находится в диапазонах: train 70–90%, validation 5–20%, test
   5–20%.
4. Один `provenance.source_id`, source text, ParamSpec, pair или leakage group
   не встречается в разных split. Несколько изделий одного заказа должны
   целиком оставаться в одном split.
5. Плато prompt+RAG доказано минимум тремя последовательными offline-прогонами
   одного frozen benchmark (не менее 50 примеров каждый): абсолютное изменение
   `paramspec_field_accuracy` между соседними прогонами не больше 1 п.п. В
   evidence явно фиксируются `benchmark_frozen=true` и
   `benchmark_excluded_from_training_and_rag=true`, а каждый прогон имеет
   уникальный id и версии prompt/RAG.

Любое отсутствие данных или evidence закрывается fail-closed точным blocker
code. Недостающие записи нельзя заменять синтетическими или дублированными.

## Предварительно зарегистрированное A/B-решение

Сравнение проводится на paired frozen holdout, который не использовался в
train/RAG. Result обязан указать точные `rule_version=paramspec-ab-v1`,
`design=paired_frozen_holdout`, `paired=true`, `holdout_frozen=true`,
`holdout_excluded_from_training_and_rag=true`, SHA-256 holdout и
`confidence_level=0.95`. Минимум 100 кейсов на arm. Fine-tune можно принять
только когда одновременно:

- все границы интервалов — конечные числа и `lower <= upper`;
- нижняя граница 95% CI прироста `paramspec_field_accuracy` не меньше 2 п.п.;
- нижняя граница 95% CI изменения `production_gate_pass_rate` не ниже нуля;
- верхняя граница 95% CI изменения `invalid_output_rate` не выше нуля.

`NaN`, infinity, обратный интервал, unpaired/non-frozen дизайн или иной уровень
доверия всегда дают `reject`. Иначе остаётся prompt+RAG, а модель не выкатывается.
