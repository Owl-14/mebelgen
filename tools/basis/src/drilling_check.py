"""Валидатор геометрии присадок (AKD-171): физика сверления + паттерны БАЗИС.

Проверяет, что каждое отверстие compute_drilling физически осмысленно:
лежит на поверхности своей панели, направлено внутрь тела, глухие не
пробивают деталь насквозь, встречные отверстия соосны, пары конфирматов
держат шаг 64 (реверс готовых изделий — docs/BASIS_FASTENERS_REVERSE.md).

Возвращает списки errors (сверлить нельзя) и warnings (паттерн нарушен).
"""

from __future__ import annotations

from typing import Any

_TOL = 0.6           # мм: допуск «точка на грани»
_EPS = 0.9           # мм: заглубление для проверки «внутрь тела»

# сквозные по замыслу: глубина может превышать толщину пробиваемой панели
# (гвоздь пробивает ДВП-задник насквозь и уходит в торец панели за ним)
_THROUGH = ("стяжка (конфирмат)", "задник (гвоздь)", "задник (саморез)",
            "короб ящика (саморез)")
# встречные пары: (purpose_a, purpose_b) должны быть соосны
_COAXIAL = (("шкант 8×30 (торец)", "шкант 8×30 (пласть)"),
            ("эксцентрик (шток)", "эксцентрик (чашка Ø15)"))


def _axes(axis: str) -> tuple[str, str, str]:
    """Ось сверления + две поперечные."""
    return {"x": ("x", "y", "z"), "y": ("y", "x", "z"), "z": ("z", "x", "y")}[axis]


def check_drilling_geometry(project: dict[str, Any],
                            holes: list[dict[str, Any]] | None = None
                            ) -> dict[str, list[str]]:
    if holes is None:
        from .hardware import compute_drilling
        holes = compute_drilling(project)
    panels = {p.get("name"): p["placement"] for p in project.get("panels", [])
              if isinstance(p.get("placement"), dict)}
    errors: list[str] = []
    warnings: list[str] = []

    for h in holes:
        pl = panels.get(h.get("panel"))
        tag = f'{h["purpose"]} @ {h.get("panel")} ({h["x"]:.0f},{h["y"]:.0f},{h["z"]:.0f})'
        if pl is None:
            errors.append(f"{tag}: панель не найдена")
            continue
        ax, t1, t2 = _axes(h["axis"])
        d = int(h["dir"])
        # 1) точка на грани тела вдоль оси сверления (входная грань)
        face = pl[f"{ax}1"] if d > 0 else pl[f"{ax}2"]
        if abs(h[ax] - face) > _TOL:
            errors.append(f"{tag}: точка не на грани панели (ось {ax}: "
                          f"{h[ax]:.1f} ≠ {face:.1f})")
            continue
        # 2) поперечные координаты внутри тела (с зазором на радиус)
        r = h["diameter"] / 2
        ok_cross = True
        for t in (t1, t2):
            lo, hi = pl[f"{t}1"], pl[f"{t}2"]
            if not (lo - _TOL <= h[t] <= hi + _TOL):
                errors.append(f"{tag}: центр вне тела по {t} "
                              f"({h[t]:.1f} ∉ [{lo:.1f},{hi:.1f}])")
                ok_cross = False
            elif h[t] - r < lo - _TOL or h[t] + r > hi + _TOL:
                warnings.append(f"{tag}: Ø{h['diameter']} выходит за кромку по {t}")
        if not ok_cross:
            continue
        # 3) глубина против толщины тела вдоль оси
        body = pl[f"{ax}2"] - pl[f"{ax}1"]
        if h["purpose"] not in _THROUGH and h["depth"] > body + _TOL:
            errors.append(f"{tag}: глухое глубже тела ({h['depth']} > {body:.1f})")
        if h["purpose"] in _THROUGH and h["depth"] > body + _TOL:
            # сквозное: продолжение должно попадать в соседнюю панель (торец)
            tip = {ax: face + d * h["depth"], t1: h[t1], t2: h[t2]}
            hit = any(q[f"{ax}1"] - _TOL <= tip[ax] <= q[f"{ax}2"] + _TOL
                      and q[f"{t1}1"] - _TOL <= tip[t1] <= q[f"{t1}2"] + _TOL
                      and q[f"{t2}1"] - _TOL <= tip[t2] <= q[f"{t2}2"] + _TOL
                      for nm, q in panels.items() if nm != h.get("panel"))
            if not hit:
                errors.append(f"{tag}: сквозное уходит в пустоту "
                              f"(конец на {tip[ax]:.0f} по {ax})")

    # 3б) петли не на уровне полок (AKD-185): планка на боковине не должна
    #     попадать в тело примыкающей полки
    shelves = [p["placement"] for p in project.get("panels", [])
               if p.get("type") == "shelf" and isinstance(p.get("placement"), dict)]
    for h in holes:
        if h["purpose"] != "петля (планка)":
            continue
        for sp in shelves:
            if sp["y1"] - 0.5 <= h["y"] <= sp["y2"] + 0.5 \
                    and sp["x1"] - 30 <= h["x"] <= sp["x2"] + 30:
                errors.append(f'петля (планка) @ {h.get("panel")} y={h["y"]:.0f}: '
                              f'на уровне полки [{sp["y1"]:.0f},{sp["y2"]:.0f}]')
                break

    # 4) соосность встречных отверстий (поперёк общей оси)
    def _key(h):  # координаты поперёк оси сверления
        _, t1, t2 = _axes(h["axis"])
        return (round(h[t1], 1), round(h[t2], 1))

    by_purpose: dict[str, list[dict[str, Any]]] = {}
    for h in holes:
        by_purpose.setdefault(h["purpose"], []).append(h)
    for pa, pb in _COAXIAL:
        for ha in by_purpose.get(pa, []):
            ka = _key(ha)
            if not any(abs(ka[0] - _key(hb)[0]) < 0.6 and abs(ka[1] - _key(hb)[1]) < 0.6
                       for hb in by_purpose.get(pb, [])):
                errors.append(f"{pa} ({ha['x']:.0f},{ha['y']:.0f},{ha['z']:.0f}): "
                              f"нет соосного «{pb}»")

    # 5) конфирматы: пара с шагом 64 существует хотя бы по одной поперечной оси
    #    (группа: панель+ось сверления+уровень по другой поперечной)
    conf = by_purpose.get("стяжка (конфирмат)", [])
    checked: set[tuple] = set()
    for h in conf:
        ax, t1, t2 = _axes(h["axis"])
        for long_t, lvl_t in ((t1, t2), (t2, t1)):
            key = (h.get("panel"), h["axis"], long_t, round(h[lvl_t], 0))
            if key in checked:
                continue
            checked.add(key)
            xs = sorted(x[long_t] for x in conf
                        if x.get("panel") == h.get("panel") and x["axis"] == h["axis"]
                        and abs(x[lvl_t] - h[lvl_t]) < 0.5)
            if len(xs) >= 2:
                diffs = [round(b - a, 1) for a, b in zip(xs, xs[1:])]
                if any(abs(df - 64.0) < 1.5 for df in diffs):
                    key2 = (h.get("panel"), h["axis"])
                    checked.add(("ok",) + key2)
    for h in conf:
        kp = (h.get("panel"), h["axis"])
        if ("ok",) + kp not in checked and ("warned",) + kp not in checked:
            xs_all = sorted({round(x["x"], 1) for x in conf if (x.get("panel"), x["axis"]) == kp} |
                            {round(x["y"], 1) for x in conf if (x.get("panel"), x["axis"]) == kp})
            n = sum(1 for x in conf if (x.get("panel"), x["axis"]) == kp)
            if n >= 2:
                warnings.append(f"конфирматы @ {kp[0]} ось {kp[1]}: нет пары с шагом 64 ({n} шт)")
            checked.add(("warned",) + kp)
    return {"errors": errors, "warnings": warnings}
