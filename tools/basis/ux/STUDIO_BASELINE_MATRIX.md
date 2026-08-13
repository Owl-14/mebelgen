# Akeda Studio — baseline и parity-матрица

Связанная задача: `MEB-093`.

Статус: `REVIEW READY` — baseline обновлён 2026-08-14 для
`origin/master` `7543c85415503bb4e3651a1a954bca54c4991db8`.

- Полные сценарии и screenshots: [STUDIO_BASELINE_REVIEW.md](STUDIO_BASELINE_REVIEW.md).
- Каждое действие и безопасный способ проверки:
  [STUDIO_CURRENT_MASTER_PARITY.md](STUDIO_CURRENT_MASTER_PARITY.md).
- Раздельные visual / logical / product observations:
  [CURRENT_STUDIO_VISUAL_AUDIT.md](CURRENT_STUDIO_VISUAL_AUDIT.md).

## Два слоя evidence

`baseline/01`–`11` — историческая фиксация SHA `a074825` от 2026-08-11.
Она сохраняется для сравнения с уже слитыми визуальными срезами.

`baseline/12`–`25` сняты на SHA `288a02d` и повторно подтверждены на exact
`origin/master` `7543c85` от 2026-08-14. Между этими SHA PR #121 меняет только
CI/dev dependencies и добавляет browser-state test, не затрагивая Studio UI,
3D или baseline assets. Набор фиксирует:
catalog, empty project, те же ключевые 3D/режимные состояния, три desktop-размера,
changed, recalculating и validation error. Это актуальная точка parity для
следующих изменений.

## Неприкосновенный контракт 3D

MEB-093 меняет только документацию и PNG evidence. В этой ветке не меняются:

- `tools/basis/src/studio.py`;
- `tools/basis/src/webviewer.py` и `MebelScene`;
- payload сцены, геометрия, камеры, OrbitControls и raycast;
- анимации дверей/ящиков, присадки, фурнитура, размеры, x-ray и exploded;
- ParamSpec, генераторы, материалы и API.

Автоматический гейт:

```bash
git diff --exit-code origin/master -- \
  tools/basis/src/studio.py tools/basis/src/webviewer.py
```

## Зафиксированный эталон

- Изделие: `Тумба для модератора`.
- Файл: `paramspecs/tumba_moderatora.json`.
- Архетип: `cabinet`.
- Габариты: `1000×400×750`.
- Состав: 25 деталей, 241 присадка.
- Default: присадки, фурнитура, текстура и размеры включены; x-ray и exploded
  выключены; фасады закрыты.
- Страница: `src/studio.py::PAGE`.
- 3D: `src/webviewer.py::MebelScene`.

## Фактическая геометрия current master

Значения измерены через `getBoundingClientRect()` в локальном браузере на SHA
`7543c85` и соответствуют current screenshots; UI tree относительно capture
SHA `288a02d` не менялся.

| Viewport | Левая панель | Центр | Правая область | Tabs/HUD/composer |
|---|---:|---:|---:|---|
| 1280×720 | 288 px | 944 px | 48 px rail | не пересекаются |
| 1440×900 | 288 px | 1104 px | 48 px rail | не пересекаются |
| 1920×1080 | 340 px | 1220 px | 360 px inspector | не пересекаются |

Старые значения `300 / 650 / 330` и `300 / 810 / 330` описывали layout до
включения responsive rail, но находились рядом с post-rail PNG. Они больше не
выдаются за геометрию снятых кадров.

При `<1600 px` inspector можно открыть из rail; тогда центральная область
уменьшается на ширину открытой панели. При `≥1600 px` inspector открыт по
умолчанию. Ручной выбор сохраняется до reload.

## Карта зон и действующий DOM-контракт

| Зона | Реальные функции | Ключевые DOM-контракты | Evidence |
|---|---|---|---|
| Проект | выбор, new, rename, duplicate, каталог, review link | `projSel`, `projNew`, `projRen`, `projDup`, `projCat`, `projShare` | parity rows + catalog/empty PNG |
| Проверки | статусы, ошибки, сводка, auto-fix | `badges`, `errors`, `stats`, `btnFixAll` | browser + contract tests |
| Выбранная деталь | данные, координаты, apply/reset/delete, drawing | `partCard`, `partExact`, `ovApply`, `ovReset`, `ovDelete`, `ovDetail` | selected-part PNG + parity |
| AI | provider, prompt, attachment, send, Undo | `aiProvider`, `chatMsg`, `chatAttach`, `chatSend`, `btnUndo` | contract/gate; внешние providers не вызваны |
| Viewport | настоящий `MebelScene` | `view3d`, `stage` | current 3D PNG + browser smoke |
| Режимы | 3D, чертёж, раскрой, печать | `tab3d`, `tabDraw`, `tabNest`, `btnPrint` | current mode PNG + round-trip |
| HUD | 5 камер, 5 слоёв, open/close, exploded | `views`, `cbHoles`, `cbHw`, `cbTex`, `cbDims`, `cbXray`, `btnToggleOpenAll`, `explode` | browser round-trip + PNG |
| Параметры | габариты, материалы, архетип, секции, raw ParamSpec | `f_w`, `f_d`, `f_h`, `f_color`, `f_facade_color`, `f_code`, `archSel`, `sections`, `rawspec`, `applyRaw` | contract + state PNG |
| Комплектация | hardware, estimate, BOM | `rightViewComponents`, `hwSlots`, `estTable`, `bom` | browser tab + contract |
| Производство | save, CFRN, B3D, delivery, versions | `btnSave`, `btnCfrn`, `btnB3d`, `btnDeliver`, `verSel`, `verRestore` | safe gates only |
| Согласования | links и inbox | `rightViewReviews`, `reviewInbox`, `reviewInboxRefresh` | browser tab + contract |
| Каталог | search, scopes, filters, inspector, archive/restore | `catalog`, `catQ`, `catScopes`, `catCats`, `catGrid`, `catInspector` | catalog PNG + parity |
| Empty project | draft, drop zone, upload CTA, composer | `emptyState`, `esFile`, `chatComposer` | empty PNG + parity |
| Нижнее состояние | model/save/selection/help | `viewportModelStatus`, `viewportSaveStatus`, `viewportSelection`, `viewportHint` | changed/recalc/error PNG |

## Обязательные состояния

| Состояние | Current master evidence | Итог |
|---|---|---|
| Default 3D | `14-current-default-1440x900.png` | PASS |
| Выбрана деталь | `15-current-selected-part-1440x900.png` | PASS |
| Открыты фасады/ящики | `16-current-opened-facades-1440x900.png`; individual + mass round-trip | PASS |
| Присадки/фурнитура/текстура/размеры | browser toggle off/on для каждого слоя | PASS |
| X-ray | `17-current-xray-1440x900.png` | PASS |
| Exploded | `18-current-exploded-1440x900.png`; 100 → 0 round-trip | PASS |
| Чертёж | `19-current-drawing-1440x900.png` | PASS |
| Раскрой | `20-current-cutting-1440x900.png` | PASS |
| Каталог | `12-catalog-1440x900.png` | PASS |
| Empty project | `13-empty-project-1440x900.png` | PASS |
| Validation error | `24-current-validation-error-1920x1080.png` | PASS |
| Changed | `23-current-changed-1920x1080.png` | PASS |
| Loading/busy | `25-current-recalculating-1920x1080.png` | PASS |
| 1280 / 1440 / 1920 | `21`, `14`, `22` current default PNG | PASS |

## Итог MEB-093

Baseline, screenshots, reproduction, per-action parity и issue classification
синхронизированы с current master. Платные/внешние действия не запускались:
их существование и блокирующие условия подтверждены контрактами, а не
production side effects.
