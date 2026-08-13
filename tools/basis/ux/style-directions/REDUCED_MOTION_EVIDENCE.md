# MEB-094 — reduced-motion evidence

## Production baseline

`src/studio.py::PAGE` уже содержит глобальный
`@media (prefers-reduced-motion:reduce)`: scroll-behavior отключается, длительности
animation/transition сводятся к `0.01ms`, iteration count — к `1`. Контракт
проверяет `tests/test_studio_visual_system_contract.py`.

## Review directions

- Ни один из трёх review CSS не добавляет `animation`, `transition`, parallax,
  hover-lift, bounce или page-load motion.
- Hover меняет только цвет/background/border и наследует production reduce-rule.
- Busy/warning/error выражаются текстом, атрибутом/состоянием и статическим marker;
  motion не является обязательным сигналом.
- Реальная анимация мебели принадлежит `MebelScene` и этими assets не меняется.

## Browser evidence

Для каждой direction проверяется одна и та же страница при обычной media setting
и `reduce`: production media query присутствует, direction stylesheet не создаёт
анимаций, UI остаётся читаемым. Скриншоты не доказывают тайминг сами по себе;
машиночитаемый контракт ниже запрещает motion declarations в review CSS.
