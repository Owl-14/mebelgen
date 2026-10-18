# MEB-094 — contrast audit

Метод: WCAG 2.2 relative luminance, normal text threshold `4.5:1`, large text
`3:1`, non-text active/focus boundary `3:1`. Значения ниже рассчитаны без alpha.

| Direction | Token | Hex | На panel | На canvas | Результат normal text |
|---|---|---:|---:|---:|---|
| A | ink | `#15191E` | 17.65 | 15.57 | PASS |
| A | muted | `#515B66` | 6.91 | 6.10 | PASS |
| A | accent | `#1F5FAF` | 6.34 | 5.59 | PASS |
| A | success | `#167142` | 6.04 | 5.33 | PASS |
| A | warning | `#8A5A00` | 5.93 | 5.23 | PASS |
| A | danger | `#A62F36` | 6.82 | 6.01 | PASS |
| B | ink | `#211F1A` | 16.31 | 14.34 | PASS |
| B | muted | `#5F5A50` | 6.79 | 5.97 | PASS |
| B | accent | `#8A4B16` | 6.72 | 5.90 | PASS |
| B | success | `#356A3E` | 6.34 | 5.57 | PASS |
| B | warning | `#7A5200` | 6.86 | 6.03 | PASS |
| B | danger | `#9A3038` | 7.29 | 6.41 | PASS |
| C | ink | `#16212B` | 16.00 | 14.20 | PASS |
| C | muted | `#52616F` | 6.24 | 5.54 | PASS |
| C | accent | `#0B5D78` | 7.22 | 6.41 | PASS |
| C | success | `#166B55` | 6.30 | 5.59 | PASS |
| C | warning | `#7A5600` | 6.52 | 5.78 | PASS |
| C | danger | `#9E3240` | 6.90 | 6.12 | PASS |

Panel/canvas pairs: A `#FFFFFF/#EEF1F4`, B `#FFFEFA/#F2EFE8`,
C `#FBFDFE/#EAF0F4`.

Ограничения: таблица подтверждает токены, а не каждый rendered pixel. После выбора
направления обязателен browser audit реальных размеров, alpha, disabled text,
focus ring на соседних цветах и SVG strokes. Цвет всегда дублируется текстом,
иконкой или DOM-state.
