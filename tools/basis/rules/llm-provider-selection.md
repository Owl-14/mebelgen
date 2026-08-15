# MEB-153: выбор платной LLM для Akeda Studio

Статус на 2026-08-13: **live bake-off заблокирован, выбор production-модели
отложен**. Этот документ фиксирует проверенные возможности, стоимость,
реализованный безопасный контур и точный оставшийся эксперимент. Он не выдаёт
маркетинговые benchmark-данные вендоров за результат Akeda.

## Кандидаты и первичные источники

| Критерий | Kimi K3 | GLM-5.2 |
|---|---|---|
| API model / endpoint | `kimi-k3` / `https://api.moonshot.ai/v1` | `glm-5.2` / `https://api.z.ai/api/paas/v4/` |
| Output cap в официальном API | `max_completion_tokens` | `max_tokens` |
| Vision | native K3 | отдельный `glm-5v-turbo` |
| Контекст | 1M | 1M |
| Structured output | strict JSON Schema и JSON mode | JSON mode; схема остаётся в prompt и валидируется Akeda |
| Управление reasoning | всегда включён, `reasoning_effort=low/high/max` | thinking можно отключить для bounded Studio latency |
| Цена input, cache miss | $3.00 / 1M tokens | $1.40 / 1M tokens |
| Цена output | $15.00 / 1M tokens | $4.40 / 1M tokens |
| Vision pricing/accounting для safety gate | unknown — paid vision fail-closed | unknown для выбранной двухмодельной цепочки — paid vision fail-closed |

Источники, проверенные 2026-08-13:

- Kimi K3 overview и API contract:
  <https://platform.kimi.ai/docs/guide/kimi-k3-quickstart>
- Kimi K3 pricing ($0.30 cache hit / $3 input / $15 output):
  <https://platform.kimi.ai/>
- Kimi structured output:
  <https://platform.kimi.ai/docs/guide/response_format>
- GLM-5.2 model/API:
  <https://docs.z.ai/guides/llm/glm-5.2>
- GLM-5.2 OpenAI SDK contract (`api.z.ai`, `max_tokens`):
  <https://docs.z.ai/guides/develop/openai/python>
- GLM structured output:
  <https://docs.z.ai/guides/capabilities/struct-output>
- GLM text/vision pricing:
  <https://docs.z.ai/guides/overview/pricing>

Устаревшие production-пресеты `moonshot-v1-8k` и `glm-4.5-flash` сохранены как
отдельные legacy IDs. Они не были молча переназначены на платные модели.

## Что доказано offline

Оба новых кандидата идут через существующий `OpenAICompatProvider` и общий
контракт prompt nodes. Реализация проверяет:

- timeout задаётся явно, OpenAI SDK работает с `max_retries=0`;
- paid flag только разрешает вызов и никогда не выбирает provider; платный ID
  должен быть явно задан как `SPEC_CHAT_PROVIDER=<candidate>` либо в запросе;
- без явного выбора сохраняется прежний бесплатный/default fallback;
- выключение `SPEC_CHAT_PAID_ENABLED` блокирует явно выбранный paid ID;
- в CI платные вызовы запрещены, пока отдельно не задан
  `SPEC_CHAT_ALLOW_PAID_IN_CI=1`;
- text API-запрос ограничен 4096 output tokens через официальное поле кандидата
  (`max_completion_tokens` у K3, `max_tokens` у GLM) и worst-case оценкой $0.15;
  input upper bound считается по UTF-8 byte length полного сериализованного
  окончательного wire payload, включая history, messages, JSON Schema,
  provider-specific output cap и параметры из `extra_body`;
- paid vision заблокирован до фиксации отдельного доказанного тарифа и правила
  token/image accounting для точной модели; text rate не подставляется;
- K3 получает strict JSON Schema; GLM получает JSON mode; оба результата затем
  проходят тот же Pydantic/typed-operation/production gate;
- input/output/total tokens и вычисленная `cost_usd` попадают в privacy-safe
  OpenTelemetry; в двухшаговом photo flow usage/cost vision и text суммируются;
- prompts, фото, ParamSpec и ключи в trace не экспортируются.

Offline mocks не доказывают точность модели, latency или repair rate.

## Решение после live bake-off

До сравнительного прогона кандидаты не ранжируются и production-модель не
выбирается. Цена и различия API-контрактов входят в будущую оценку вместе с
evidence по русским ТЗ, фото, EditOperation/ParamSpec, injection, repair success
и latency; ни один из этих факторов отдельно не объявляет результат.

## Обязательный live bake-off

Источник dataset — версионируемый набор MEB-151. До его готовности нельзя
создавать второй конкурирующий корпус внутри MEB-153. Минимальный прогон:

1. Одинаковые prompt versions и неперсональные fixtures для обоих кандидатов.
2. Не меньше: 3 create по русскому тексту, 3 typed edit, 2 photo create,
   2 injection/несвязанная мутация, 2 malformed/repair сценария.
3. Temperature/reasoning: K3 `low`, GLM thinking disabled; по одному запросу на
   кейс, без скрытого retry. Repair учитывается отдельным явным запросом.
4. Предварительный ceiling: 12 основных + максимум 2 repair вызова на модель,
   `SPEC_CHAT_MAX_REQUEST_USD=0.15`; абсолютный бюджет эксперимента $4.20.
5. Сохранять только fixture id, provider/model, prompt version, schema/operation/
   gate outcomes, repair count, latency, tokens и cost. Текст/фото/ParamSpec не
   сохранять в traces.
6. Итоговый выбор начинается с safety gates: injection escape или мутация
   при красном gate дисквалифицирует. Затем: successful operations, schema
   success, repair success, median/P95 latency и стоимость успешной операции.

Платный прогон разрешено начать только когда одновременно доступны MEB-151 и
оба тестовых API-ключа, а для photo cases подтверждены отдельные vision pricing
и accounting rules точных моделей. Отсутствующий или fail-closed кандидат нельзя
считать проигравшим.

## Rollout и rollback

После утверждения отчёта:

1. Установить секрет выбранного по отчёту provider вне Git и оставить прежний
   provider секрет.
2. Canary: явно задать `SPEC_CHAT_PROVIDER=<selected-provider>`, затем разрешить его
   `SPEC_CHAT_PAID_ENABLED=1` при текущих cost/token ceilings. Один flag без
   provider selection ничего не переключает.
3. Проверить текстовое создание, photo create, typed edit, provider failure,
   trace usage/cost и отсутствие persistence при красном gate.
4. Rollback: вернуть `SPEC_CHAT_PROVIDER` к прежнему provider (или убрать явный
   выбор) и выставить `SPEC_CHAT_PAID_ENABLED=0`, затем перезапустить штатным
   runbook.
5. Не удалять legacy provider до отдельного периода наблюдения.

До live bake-off флаг обязан оставаться выключенным; deploy этой ветки не нужен
и запрещён до merge завершённой задачи.
