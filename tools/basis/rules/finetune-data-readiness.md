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
- `provenance.source_id`, `usage_rights=training-approved` и
  `rights_record_id`, подтверждающий право использовать данные для обучения.

Legacy-записи `ParamSpec→project` из `feedback.record_pair` без этих полей не
считаются чистыми парами. Тестовые fixtures также не входят в реальный отчёт.

## Измеримые условия допуска

1. Не менее 200 clean unique пар после production quality checks и dedup.
2. Нет повторов `pair_id`, нормализованного ТЗ, канонического ParamSpec или
   всей пары.
3. Разбиение находится в диапазонах: train 70–90%, validation 5–20%, test
   5–20%.
4. Один source/ParamSpec/pair/leakage group не встречается в разных split.
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
train/RAG. Минимум 100 кейсов на arm. Fine-tune можно принять только когда
одновременно:

- нижняя граница 95% CI прироста `paramspec_field_accuracy` не меньше 2 п.п.;
- нижняя граница 95% CI изменения `production_gate_pass_rate` не ниже нуля;
- верхняя граница 95% CI изменения `invalid_output_rate` не выше нуля.

Иначе решение `reject`: остаётся prompt+RAG, а модель не выкатывается.
