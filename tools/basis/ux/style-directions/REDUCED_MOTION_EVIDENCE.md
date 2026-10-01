# MEB-094 — reduced-motion browser evidence

Это не только статический CSS-контракт. `capture_live_studio.py` запускает настоящий локальный Studio в Chromium для каждой A/B/C direction, создаёт browser context с `reduced_motion="reduce"`, внедряет review CSS через `page.add_style_tag()` и проверяет:

- `matchMedia('(prefers-reduced-motion: reduce)').matches === true`;
- вычисленные `animation-duration` и `transition-duration` каждого DOM-элемента не превышают `0.011ms`;
- список нарушителей пуст.

Машиночитаемый результат хранится в `browser-evidence.json` в секции `directions.*.reduced_motion`. Тот же сценарий является частью CI browser gate в `tests/test_studio_browser_state.py::test_review_direction_is_live_and_reduced_motion_is_effective`.

Воспроизведение из `tools/basis`:

```powershell
python ux/style-directions/capture_live_studio.py --base-sha bbe3b4bb43003b17dc45d6ec8967da0946963dca
python -m pytest tests/test_studio_browser_state.py -q
```

Review CSS не добавляет собственных `animation` или `transition`; production media query остаётся источником поведения reduced motion. Это доказательство не означает пользовательский выбор A, B или C.
