# mebelgen

Два инструмента для перевода мебельного ТЗ в готовые артефакты.

| Инструмент | Что делает | Запуск |
|---|---|---|
| **[tools/basis](tools/basis)** | ТЗ → строгий JSON → модель в **БАЗИС-Мебельщик** (панели + фурнитура) | `cd tools/basis && python main.py …` |
| **[tools/refsheets](tools/refsheets)** | Word-ТЗ → референс-листы A4 (2D-чертежи, 3D-рендер) | `cd tools && python -m refsheets …` |

```
tools/
  basis/        ТЗ → JSON → БАЗИС     (src/, schema/, projects/, scripts/, RULES.md)
  refsheets/    Word → листы 2D/3D    (Python-пакет, output/)
input_specs/    общие исходные ТЗ
docs/           аналитика и заметки по БАЗИС-API
```

Подробности — в README каждого инструмента. Правила построения модели для БАЗИС —
в [tools/basis/RULES.md](tools/basis/RULES.md).
