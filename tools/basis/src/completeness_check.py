"""Валидатор полноты модели (AKD-182): всё заявленное построено и держится.

Причина появления: регресс был зелёным (36/36 VALID) при 28 спеках с
дефектами — существующие проверки смотрят ГЕОМЕТРИЮ (пересечения, физика
сверления), но не ПОЛНОТУ. Здесь проверяем:

1. Каждая панель закреплена: на ней есть присадка, либо в её тело входит
   сквозной крепёж соседней панели (конфирмат/гвоздь/саморез).
2. Заявленное в ParamSpec построено: полки (shelf_levels/shelves) → панели,
   rod → hardware.rods, опоры (legs.height>0) → цоколь/опоры/металлокаркас.

Возвращает список ошибок (пустой — модель полная).
"""

from __future__ import annotations

from typing import Any

from .hardware import compute_drilling, leg_positions


def _held(pl: dict[str, float], holes: list[dict[str, Any]]) -> bool:
    """В тело панели входит хотя бы одно отверстие (само или сквозной крепёж)."""
    for h in holes:
        ax = h["axis"]
        others = [a for a in "xyz" if a != ax]
        tip = h[ax] + h["dir"] * h["depth"]
        lo, hi = sorted((h[ax], tip))
        if (pl[f"{ax}1"] - 1 <= hi and lo <= pl[f"{ax}2"] + 1
                and all(pl[f"{o}1"] - 1 <= h[o] <= pl[f"{o}2"] + 1 for o in others)):
            return True
    return False


def check_completeness(project: dict[str, Any],
                       spec: dict[str, Any] | None = None) -> list[str]:
    errors: list[str] = []
    panels = [p for p in project.get("panels", []) if isinstance(p.get("placement"), dict)]
    holes = compute_drilling(project)
    drilled = {h["panel"] for h in holes}

    # 1. каждая панель держится
    for p in panels:
        if p["name"] in drilled or _held(p["placement"], holes):
            continue
        errors.append(f"крепёж: «{p['name']}» ({p.get('type')}) — ни одной присадки")

    if spec is None:
        return errors

    # 2а. полки: заявлено vs построено
    want = 0
    for s in spec.get("sections") or []:
        lv = s.get("shelf_levels")
        want += len(lv) if lv else (s.get("shelves") or 0)
    got = sum(1 for p in panels
              if p.get("type") == "shelf" and "ниш" not in str(p["name"]).lower())
    if want and got < want:
        errors.append(f"полки: заявлено {want}, построено {got}")

    # 2б. штанга: rod в секции → hardware.rods
    want_rods = sum(1 for s in (spec.get("sections") or []) if s.get("rod"))
    got_rods = len((project.get("hardware") or {}).get("rods") or [])
    if want_rods and got_rods < want_rods:
        errors.append(f"штанга: заявлено {want_rods}, построено {got_rods}")

    # 2в. опоры: заявлены → цоколь-панель / опоры / металлокаркас
    legs = spec.get("legs") or {}
    lt = str(legs.get("type", "нет")).lower()
    lh = legs.get("height", 0) or 0
    if lt not in ("нет", "", "-", "—") and lh > 0:
        construction = str((project.get("carcass_calculation") or {}).get("construction", ""))
        has_plinth = any(p.get("type") == "plinth" for p in panels)
        pedestal = construction == "round_pedestal"
        if not (has_plinth or pedestal or construction == "top_on_metal_frame"
                or leg_positions(project)):
            errors.append(f"опоры: legs.type='{legs.get('type')}' h={lh} — "
                          "нет ни цоколя, ни опор, ни каркаса")
    return errors
