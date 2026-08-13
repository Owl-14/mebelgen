# Akeda Studio — эталонный локальный review

Связанная задача: `MEB-093`.

- Current verification SHA: `7543c85415503bb4e3651a1a954bca54c4991db8`.
- Screenshot capture SHA: `288a02d6f8517745a47f53570104d2d1dda10807`.
- Исторический baseline SHA: `a0748256d78f9e910c1cfa7225c12d8ff4ddfa96`.
- Дата current фиксации: 2026-08-14.

Это screenshots настоящего локального Studio, не макет. Current набор снят из
отдельного worktree exact `origin/master`. Product code, `MebelScene`,
генераторы, ParamSpec, материалы и API для съёмки не менялись.

## Как воспроизвести

```bash
cd tools/basis
python main.py studio paramspecs/tumba_moderatora.json \
  --port 44194 --out /tmp/akeda-meb-093-review --no-open
```

Открыть `http://127.0.0.1:44194/`, выбрать «Тумба для модератора». Внешние AI,
B3D/Cutting cloud, публикацию review link и production не вызывать. Тестовые
правки не сохранять.

## Current master screenshots

Все кадры `12`–`25` сняты на SHA `288a02d` и повторно подтверждены на exact
`origin/master` `7543c85`. PR #121 между этими SHA добавляет только CI/dev
dependencies и browser-state test; Studio UI, 3D и baseline assets не меняет.

| Кадр | Что доказывает | Файл |
|---|---|---|
| Каталог | отдельный catalog workspace, поиск, scopes, категории и карточки | [1440×900](baseline/12-catalog-1440x900.png) |
| Empty project | настоящий draft workspace с upload CTA и composer | [1440×900](baseline/13-empty-project-1440x900.png) |
| Default 3D | текущая оболочка, реальный payload, HUD, status и composer | [1440×900](baseline/14-current-default-1440x900.png) |
| Выбрана деталь | raycast highlight, карточка и контекст composer | [1440×900](baseline/15-current-selected-part-1440x900.png) |
| Открытые фасады | массовая анимация настоящей сцены | [1440×900](baseline/16-current-opened-facades-1440x900.png) |
| X-ray | текущая прозрачность и технические слои | [1440×900](baseline/17-current-xray-1440x900.png) |
| Exploded | текущий разнесённый вид при 100% | [1440×900](baseline/18-current-exploded-1440x900.png) |
| Чертёж | current drawing workspace | [1440×900](baseline/19-current-drawing-1440x900.png) |
| Раскрой | current nesting workspace без cloud Cutting | [1440×900](baseline/20-current-cutting-1440x900.png) |
| Узкий desktop | collapsed rail и свободный центр | [1280×720](baseline/21-current-default-1280x720.png) |
| Wide desktop | открытый inspector | [1920×1080](baseline/22-current-default-1920x1080.png) |
| Changed | несохранённая правка до debounce | [1920×1080](baseline/23-current-changed-1920x1080.png) |
| Validation error | прежняя модель явно помечена как предыдущая | [1920×1080](baseline/24-current-validation-error-1920x1080.png) |
| Recalculating | previous model остаётся видима с честным busy status | [1920×1080](baseline/25-current-recalculating-1920x1080.png) |

Кадр `25` снят через локальный loopback proxy с двухсекундной задержкой только
для `/api/generate`. Это не внешний API и не изменение product code.

## Исторические screenshots

Кадры `01`–`11` сохраняют состояние SHA `a074825` перед следующими слитыми
визуальными срезами. Они не используются для current размеров, но остаются
точкой сравнения поведения и 3D.

| Состояние | Файл |
|---|---|
| Default 1440 | [01](baseline/01-default-1440x900.png) |
| Selected part | [02](baseline/02-selected-part-1440x900.png) |
| Opened facades | [03](baseline/03-opened-facades-1440x900.png) |
| X-ray | [04](baseline/04-xray-1440x900.png) |
| Exploded | [05](baseline/05-exploded-1440x900.png) |
| Drawing | [06](baseline/06-drawing-1440x900.png) |
| Cutting | [07](baseline/07-cutting-1440x900.png) |
| Default 1280 | [08](baseline/08-default-1280x720.png) |
| Default 1920 | [09](baseline/09-default-1920x1080.png) |
| Validation error | [10](baseline/10-validation-error-1920x1080.png) |
| Changed / recalculating | [11 changed](baseline/11-changed-1920x1080.png), [11 recalculating](baseline/11-recalculating-1920x1080.png) |

## Фактические current размеры

Browser measurement на SHA `288a02d`:

- `1280×720`: side 288 px, center 944 px, rail 48 px.
- `1440×900`: side 288 px, center 1104 px, rail 48 px.
- `1920×1080`: side 340 px, center 1220 px, inspector 360 px.

На всех трёх размерах tabs/HUD не пересекаются. Composer остаётся внутри
центральной области. Значения `300/650/330` и `300/810/330` относятся к
pre-rail layout и больше не описывают current PNG.

## Выполненный browser smoke

Локально, без внешних API:

1. Выбран `tumba_moderatora.json`.
2. Выполнен round-trip `3D → чертёж → раскрой → 3D`.
3. Выбрана и снята внутренняя деталь; отдельно открыт фасад.
4. Выполнены `Открыть всё → Закрыть всё`.
5. Все пять ракурсов активировались по одному и вернулись в перспективу.
6. Все пять слоёв переключились и вернулись в default.
7. Exploded прошёл `0 → 100 → 0`.
8. Проверены четыре режима правого inspector на wide desktop.
9. Открыты catalog и empty draft; временный draft удалён.
10. Сняты `changed`, `recalculating` и `validation error`; ширина возвращена к
    исходному значению без сохранения.
11. App console/page errors: 0. Headless Chromium сообщил только собственные
    WebGL `ReadPixels` performance warnings; функциональных ошибок Studio нет.

Полный результат по каждому действию:
[STUDIO_CURRENT_MASTER_PARITY.md](STUDIO_CURRENT_MASTER_PARITY.md).

## Проверки current baseline

- Studio-targeted на exact `7543c85`: `129 passed`.
- Полный pytest на exact `7543c85`: `606 passed, 4 skipped`.
- Генераторная регрессия: `valid 38/38`, `exact vs golden 15/15`.
- PNG: 14/14 файлов `12`–`25` соответствуют размеру в имени.
- Все Markdown-ссылки на current/historical PNG существуют.
- `git diff --check` чист; diff против `origin/master` не содержит
  `tools/basis/src/studio.py` или `tools/basis/src/webviewer.py`.

## Что намеренно не выполнялось

- внешние AI providers;
- B3D и Cutting cloud API;
- deploy и любые production действия;
- публикация review link;
- сохранение тестовой редакции или изменение пользовательского изделия.

Эти пути присутствуют в per-action matrix как `G`: проверены DOM/handler/API
контракты и safe gates без side effects.

## Issue classification

Визуальные дефекты, логические дефекты и вопросы продукта разделены в
[CURRENT_STUDIO_VISUAL_AUDIT.md](CURRENT_STUDIO_VISUAL_AUDIT.md). Это
наблюдения, а не разрешение менять product workflow в MEB-093.
