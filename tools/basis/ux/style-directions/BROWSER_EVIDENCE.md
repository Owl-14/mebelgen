# MEB-094 — browser screenshot evidence

Дата: 2026-08-14
Browser viewport: `1440×900`
Source: локальный Studio из worktree MEB-094, base `7543c85`, без production diff.
Изделие: `Тумба для модератора · cabinet 1000×400×750`.

## Фиксации

| Asset | Размер | SHA-256 | Что подтверждает |
|---|---:|---|---|
| `origin-master-1440x900.jpg` | 1440×900 | `45a48fb6ff476b3d099eff5eab289ae178ad8ddea5d5b68de19e1a42b5eab003` | Свежий текущий Studio и настоящая MebelScene |
| `direction-a-1440x900.jpg` | 1440×900 | `018c7836f7253d85e949cfadaeb9460125b73672257d64fb7b840e09e3bfe777` | A + palette/component specimen |
| `direction-b-1440x900.jpg` | 1440×900 | `56c202d2f14a842dd799597926a9bfcca5a94526cd19de050be1841fd8230402` | B + palette/component specimen |
| `direction-c-1440x900.jpg` | 1440×900 | `b6e8b9fe188a6f501c141219749f4a9d37228e5fefb35ec81b7939cc58762ee9` | C + palette/component specimen |

## Проверено в Browser

- локальный Studio загрузился, нужное реальное изделие выбрано штатным select;
- baseline screenshot снят с exact-base worktree, не с production;
- каждый `review-board.html?direction=a|b|c` показал правильный title и видимую
  baseline image;
- все четыре JPEG имеют фактический размер `1440×900`;
- console current Studio и review boards: новых `error`/`warn` не обнаружено;
- review HTML содержит `prefers-reduced-motion: reduce`, а direction CSS не
  содержит animation/transition declarations.

Скриншоты доказывают визуальный review asset, но не являются пользовательским
выбором направления и не разрешают production implementation.
