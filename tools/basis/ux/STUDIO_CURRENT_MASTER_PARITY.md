# Akeda Studio — current master per-action parity

Связанная задача: `MEB-093`.

- Проверяемый SHA: `7543c85415503bb4e3651a1a954bca54c4991db8` (`origin/master`, 2026-08-14).
- Проект: `paramspecs/tumba_moderatora.json`, «Тумба для модератора».
- Среда: локальный Studio, без production и внешних API.
- Результат: `PASS` для доступных read-only/local действий; платные, внешние и
  сохраняющие действия перечислены отдельно и подтверждены контрактами и
  безопасными гейтами, но не запускались.

## Обозначения evidence

- `B` — действие выполнено вручную в локальном браузере на указанном SHA.
- `S` — состояние зафиксировано PNG в `ux/baseline/`.
- `C` — DOM/handler/API контракт подтверждён локальными pytest.
- `G` — действие намеренно не выполнялось: оно пишет данные, публикует,
  вызывает внешний сервис или платную сборку; проверен только безопасный гейт.

## Проект и каталог

| Действие | Место / DOM | Состояние и ожидаемый результат | Evidence | Итог |
|---|---|---|---|---|
| Выбрать изделие | `projSel` | 3D, сводка и формы переключаются на выбранный файл | B, C | PASS |
| Создать новое | `projNew` → `/api/new` | Открывается draft workspace без 3D | B, S `13-empty-project-1440x900.png`, C | PASS |
| Переименовать | `projRen` → `/api/save` | Имя меняется только после подтверждения | C, G | PASS (contract) |
| Создать копию | `projDup` → `/api/duplicate` | Новый файл создаётся отдельно от источника | C, G | PASS (contract) |
| Открыть каталог | `projCat`, `catalog` | Отдельный каталог, 3D-параметры не подменяют workspace | B, S `12-catalog-1440x900.png`, C | PASS |
| Закрыть каталог | `catClose` | Возврат в выбранное изделие | B, C | PASS |
| Поиск по названию | `catQ` | Список фильтруется без открытия карточки | B, C | PASS |
| Фильтр области | `catScopes` | Все / мои / без ответственного / архив | B, C | PASS |
| Фильтр ответственного | `catResponsible` | Фильтр не изменяет изделие | C | PASS |
| Фильтр статуса | `catStatus` | Все / рабочие / черновики | C | PASS |
| Фильтр категории | `catCats` | Все типы и категории каталога | B, C | PASS |
| Выбрать карточку | `catGrid`, `catInspector` | Открывается inspector выбранного изделия | B, C | PASS |
| Открыть изделие из каталога | `catOpen` | Выбранный файл становится текущим | C | PASS |
| Дублировать / переименовать / архивировать | `catDuplicate`, `catRename`, `catArchive` | Подтверждение и обратимый архив | C, G | PASS (contract) |
| Восстановить из архива | `catRestore` | Карточка возвращается в рабочую область | C, G | PASS (contract) |

## Viewport, выбор и режимы

| Действие | Место / DOM | Состояние и ожидаемый результат | Evidence | Итог |
|---|---|---|---|---|
| Orbit | `view3d` / canvas drag | Камера вращается, модель не меняется | B | PASS |
| Zoom | `view3d` / wheel | Масштаб ограничен bounds модели | B, C | PASS |
| Pan | `view3d` / Shift+drag | Target перемещается в допустимых границах | B, C | PASS |
| Выбрать деталь | raycast → `partCard` | Highlight и карточка одной детали | B, S `15-current-selected-part-1440x900.png`, C | PASS |
| Снять выбор | `Esc`, `partClearSelection` | Highlight и карточка исчезают | B, C | PASS |
| Изменить координаты детали | `partExact`, `ovApply` | Применение только валидного локального draft | C, G | PASS (contract) |
| Сбросить координаты | `ovReset` | Возврат к исходным координатам | C, G | PASS (contract) |
| Удалить деталь | `ovDelete` | Явное действие, блокируется при busy | C, G | PASS (contract) |
| Открыть чертёж детали | `ovDetail` | Чертёж адресован выбранной детали | C | PASS |
| Режим 3D | `tab3d` | Видна интерактивная сцена | B, S `14-current-default-1440x900.png` | PASS |
| Режим чертежа | `tabDraw`, `draw` | Общий чертёж без потери проекта | B, S `19-current-drawing-1440x900.png`, C | PASS |
| Режим раскроя | `tabNest`, `draw` | Листы и предупреждения без внешнего Cutting API | B, S `20-current-cutting-1440x900.png`, C | PASS |
| Возврат 3D → чертёж → раскрой → 3D | `tabs` | Контекст проекта сохраняется | B | PASS |
| Печать | `btnPrint` | Disabled в 3D; доступна только в печатном режиме | B, C, G | PASS (gate) |

## HUD и 3D-состояния

| Действие | Место / DOM | Состояние и ожидаемый результат | Evidence | Итог |
|---|---|---|---|---|
| Ракурс Аксон | `views [data-view=axon]` | Активен один preset | B | PASS |
| Ракурс Перспектива | `views [data-view=persp]` | Активен один preset | B | PASS |
| Ракурс Сверху | `views [data-view=top]` | Активен один preset | B | PASS |
| Ракурс Спереди | `views [data-view=front]` | Активен один preset | B | PASS |
| Ракурс Слева | `views [data-view=left]` | Активен один preset | B | PASS |
| Присадки | `cbHoles` | Toggle меняет слой и возвращается | B | PASS |
| Фурнитура | `cbHw` | Toggle меняет слой и возвращается | B | PASS |
| Текстура | `cbTex` | Toggle меняет слой и возвращается | B | PASS |
| Размеры | `cbDims` | Toggle меняет слой и возвращается | B | PASS |
| X-ray | `cbXray` | Прозрачность включается и выключается | B, S `17-current-xray-1440x900.png` | PASS |
| Открыть всё | `btnToggleOpenAll` | Все фасады/ящики открываются | B, S `16-current-opened-facades-1440x900.png` | PASS |
| Закрыть всё | `btnToggleOpenAll` | Та же кнопка возвращает закрытое состояние | B | PASS |
| Открыть отдельный фасад | raycast по openable | Состояние синхронизируется с общей кнопкой | B, C | PASS |
| Exploded 0 → 100 | `explode` | Детали раздвигаются | B, S `18-current-exploded-1440x900.png` | PASS |
| Exploded 100 → 0 | `explode` | Геометрия возвращается без изменения ParamSpec | B | PASS |

## Панели, редактирование и статусы

| Действие | Место / DOM | Состояние и ожидаемый результат | Evidence | Итог |
|---|---|---|---|---|
| Открыть параметры | `rightRailProperties` / `rightTabProperties` | Видна только область параметров | B, C | PASS |
| Открыть комплектацию | `rightRailComponents` / `rightTabComponents` | Видны hardware, estimate и BOM | B, C | PASS |
| Открыть производство | `rightRailProduction` / `rightTabProduction` | Видны save/export/versions | B, C | PASS |
| Открыть согласования | `rightRailReviews` / `rightTabReviews` | Видны ссылки и inbox | B, C | PASS |
| Свернуть inspector | `rightPanelClose` | На `<1600` остаётся rail 48 px | B, S `21-current-default-1280x720.png` | PASS |
| Изменить габарит | `f_w`, `f_d`, `f_h` | `changed` → `recalculating` → terminal | B, S `23-current-changed-1920x1080.png`, `25-current-recalculating-1920x1080.png`, C | PASS |
| Невалидный габарит | `f_w=10` | Предыдущая 3D явно помечена как устаревшая | B, S `24-current-validation-error-1920x1080.png`, C | PASS |
| Материал / декор | `f_color`, `f_facade_color`, `f_code` | Пересчёт с видимым состоянием | C | PASS |
| Архетип | `archSel` | Динамические поля соответствуют типу | C | PASS |
| Добавить / удалить секцию | `addSec`, section remove | Изменение проходит через тот же draft/recalc | C, G | PASS (contract) |
| Raw ParamSpec | `rawspec`, `applyRaw` | Невалидный JSON не применяется | C, G | PASS (contract) |
| Undo | `btnUndo` | Возврат последней локальной правки | C, G | PASS (contract) |
| Auto-fix | `btnFixAll` | Запуск только явной кнопкой при fixable issue | C, G | PASS (gate) |

## AI, производство и согласование

| Действие | Место / DOM | Состояние и ожидаемый результат | Evidence | Итог |
|---|---|---|---|---|
| Выбрать provider | `aiProvider` | Basic остаётся локальным; внешние providers не вызывались | B, C, G | PASS (gate) |
| Ввести команду | `chatMsg` | Контекст явно «всё изделие» или выбранная деталь | B, C | PASS |
| Приложить ТЗ | `chatAttach`, `chatFile` | Локальный chooser; файл не отправлялся | C, G | PASS (contract) |
| Выполнить команду | `chatSend` | Busy/error/history не теряют контекст | C, G | PASS (contract) |
| Сохранить | `btnSave` | Сохраняется текущая валидная редакция | C, G | PASS (contract) |
| CFRN | `btnCfrn` | Доступен только при пройденных гейтах | C, G | PASS (gate) |
| B3D | `btnB3d` | Платная/внешняя сборка не запускалась | C, G | PASS (gate) |
| Передача | `btnDeliver` | Не публикует blocked/stale редакцию | C, G | PASS (gate) |
| Версии / восстановление | `verSel`, `verRestore` | Restore требует выбранной версии и явного действия | C, G | PASS (contract) |
| Создать review link | `projShare`, `shareDialog` | Диалог не публикует ссылку до подтверждения | C, G | PASS (gate) |
| Обновить inbox | `reviewInboxRefresh` | Локальный список обновляется отдельно от модели | C, G | PASS (contract) |

## Responsive parity

| Viewport | Левая панель | Центр | Правая область | Проверка | Итог |
|---|---:|---:|---:|---|---|
| 1280×720 | 288 px | 944 px | 48 px rail | tabs/HUD/composer не перекрываются | PASS |
| 1440×900 | 288 px | 1104 px | 48 px rail | tabs/HUD/composer не перекрываются | PASS |
| 1920×1080 | 340 px | 1220 px | 360 px inspector | tabs/HUD/composer не перекрываются | PASS |

Скриншоты: `21-current-default-1280x720.png`,
`14-current-default-1440x900.png`, `22-current-default-1920x1080.png`.

## Запрещённые действия этого review

Не выполнялись production deploy, B3D/Cutting cloud API, внешние AI providers,
публикация review link, сохранение тестовых правок и изменение пользовательских
изделий. Временный draft для empty-state был создан только в локальном
worktree, снят в кадр и удалён до проверки diff.
