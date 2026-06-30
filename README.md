# mebelgen

Два инструмента для перевода мебельного ТЗ в готовые артефакты.

| Инструмент | Что делает | Запуск |
|---|---|---|
| **[tools/basis](tools/basis)** | ТЗ → строгий JSON → модель в **БАЗИС-Мебельщик** (панели + фурнитура) | `cd tools/basis && python main.py …` |
| **[tools/refsheets](tools/refsheets)** | Word-ТЗ → референс-листы A4 (2D-чертежи, 3D-рендер) | `cd tools && python -m refsheets …` |

```
tools/
  basis/        ТЗ → JSON → БАЗИС     (src/, generators/, schema/, projects/, paramspecs/,
                                       materials/ ≈5000 позиций, scripts/, RULES.md)
  refsheets/    Word → листы 2D/3D    (Python-пакет, output/)
input_specs/    общие исходные ТЗ  (+ komi_rf/ — заказ Россельхозбанк: текст ТЗ и 23 чертежа)
docs/           аналитика и заметки по БАЗИС-API
```

Сборка модели — двумя путями: импортёр в БАЗИС-Мебельщике (надёжно, нужна лицензия)
или облако `.cfrn→.b3d` без десктопа (device-independent). База материалов производства —
`tools/basis/materials/baza_materiala.json`, поиск `python main.py materials --search …`.

Подробности — в README каждого инструмента. Правила построения модели для БАЗИС —
в [tools/basis/RULES.md](tools/basis/RULES.md).
