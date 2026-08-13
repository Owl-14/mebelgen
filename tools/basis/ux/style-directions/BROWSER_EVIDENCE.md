# MEB-094 — live Studio browser evidence

Дата: 2026-08-14

Exact base / merge-base: `bbe3b4bb43003b17dc45d6ec8967da0946963dca`

Viewport: `1440×900`

Source: `paramspecs/komi_72_tumba_podkatnaya.json`

Все A/B/C assets ниже — полные screenshots настоящей локальной страницы Studio после достижения `viewportModelState === 'ready'`. Для каждого кадра Playwright временно добавил соответствующий review-only CSS через `page.add_style_tag()` и `data-meb094-direction`; production HTML/CSS не импортирует overrides и не изменялся.

| Asset | Размер | SHA-256 | Фактически применено |
|---|---:|---|---|
| `origin-master-1440x900.jpg` | 1440×900 | `4b8394b972da300f2733c09a096771b6fabf1e69b5d39a29a3af1a9676010660` | Studio без review override |
| `direction-a-1440x900.jpg` | 1440×900 | `b530ca87e0e51124bca46f0eda4de81e5a38ff45c1282573b222305b1a374a10` | `data-meb094-direction=a`, accent `#1f5faf`, workspace `rgb(232, 237, 241)` |
| `direction-b-1440x900.jpg` | 1440×900 | `7994ec6f7b67bc00f1765da58e0298ac79426fe051335ba779e0cfed78e08072` | `data-meb094-direction=b`, accent `#8a4b16`, workspace `rgb(235, 230, 220)` |
| `direction-c-1440x900.jpg` | 1440×900 | `f9ea864857969d3fd48c436f66de5276f8bc5ffaa03067a2ae8d7fa0aa8eb560` | `data-meb094-direction=c`, accent `#0b5d78`, workspace `rgb(221, 232, 239)` |

`browser-evidence.json` хранит CSS/screenshot SHA-256, вычисленные token/background значения, пустые списки console errors и результаты reduced-motion browser probe для каждого направления. `capture_live_studio.py` полностью воспроизводит эти артефакты и отказывает, если `git merge-base HEAD origin/master` не равен переданному exact base.

Удалён прежний `review-board.html`: составные boards больше не используются и не выдаются за live Studio. Эти screenshots подтверждают только три равноправных review-направления; пользовательский выбор и разрешение production implementation отсутствуют.
