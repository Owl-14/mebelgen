"""Слой оверрайдов деталей (AKD-121): точечные правки поверх генератора.

Пользователь двигает/растягивает/удаляет отдельные детали в Studio — правки
хранятся в спеке (`spec.overrides[]`) и применяются ПОСЛЕ генератора, до
валидаторов. Поэтому они переживают регенерацию (смену габаритов/секций),
а чертёж, присадки, смета и .cfrn→.b3d пересчитываются автоматически —
весь конвейер работает по итоговому (пост-оверрайд) project.json.

Формат оверрайда:
  {"panel": "<имя>", "action": "transform",     # действие по умолчанию
   "placement": {"x1": …, "y2": …},             # абсолютные правки граней
   "move": [dx, dy, dz]}                        # и/или сдвиг целиком
  {"panel": "<имя>", "action": "delete"}
  {"panel": "<имя>", "action": "add", "type": "shelf",
   "orientation": "horizont", "thickness": 16, "material": "ЛДСП",
   "placement": {x1..z2}}

Конфликт (панель генератором больше не создаётся) → warning в
project.warnings, не молчаливая поломка.
"""

from __future__ import annotations

from typing import Any

_DIM_AXES = {"horizont": ("x", "z"), "horizontal": ("x", "z"),
             "vertical": ("z", "y"), "front": ("x", "y")}
_EDGES = ("x1", "x2", "y1", "y2", "z1", "z2")


def _r(v: float) -> float:
    v = round(float(v), 2)
    return int(v) if v == int(v) else v


def _refresh_derived(p: dict[str, Any]) -> None:
    """placement — источник истины: пересчитать dimensions/position/thickness."""
    pl = p["placement"]
    orient = str(p.get("basis_orientation", "front")).lower()
    wa, ha = _DIM_AXES.get(orient, ("x", "y"))
    span = {"x": pl["x2"] - pl["x1"], "y": pl["y2"] - pl["y1"], "z": pl["z2"] - pl["z1"]}
    p["dimensions"] = {"width": _r(span[wa]), "height": _r(span[ha])}
    p["position"] = {"x": _r(pl["x1"]), "y": _r(pl["y1"]), "z": _r(pl["z1"])}
    thick_axis = next(a for a in ("x", "y", "z") if a not in (wa, ha))
    p["thickness"] = _r(span[thick_axis])


def apply_back_mount(project: dict[str, Any], mode: str | None) -> dict[str, Any]:
    """Накладной задник (AKD-137): mode="overlay" перекладывает тонкий
    ДВП-задник ПОВЕРХ задних торцов корпуса на всю его ширину/высоту —
    как в реальных изделиях БАЗИС (реверс: гвозди по периметру в торцы).
    По умолчанию (inset) задник остаётся врезным — golden-эталоны не меняются.
    """
    if mode != "overlay":
        return project
    panels = project.get("panels", [])
    back = next((p for p in panels
                 if p.get("type") == "back" and float(p.get("thickness", 16)) <= 6
                 and isinstance(p.get("placement"), dict)), None)
    if back is None:
        return project
    carcass = [p for p in panels
               if p is not back and isinstance(p.get("placement"), dict)
               and p.get("type") in ("side_left", "side_right", "top", "bottom",
                                     "vertical_partition", "shelf", "plinth")]
    if not carcass:
        return project
    t = float(back.get("thickness", 3))
    pl = back["placement"]
    pl["x1"] = min(q["placement"]["x1"] for q in carcass)
    pl["x2"] = max(q["placement"]["x2"] for q in carcass)
    pl["y1"] = min(q["placement"]["y1"] for q in carcass)
    pl["y2"] = max(q["placement"]["y2"] for q in carcass)
    z_rear = max(q["placement"]["z2"] for q in carcass)
    pl["z1"], pl["z2"] = _r(z_rear), _r(z_rear + t)
    back["override"] = True
    _refresh_derived(back)
    return project


def apply_overrides(project: dict[str, Any],
                    overrides: list[dict[str, Any]] | None) -> dict[str, Any]:
    if not overrides:
        return project
    panels = project.get("panels", [])
    by_name = {str(p.get("name")): p for p in panels}
    warnings: list[str] = []

    for ov in overrides:
        name = str(ov.get("panel", ""))
        action = ov.get("action", "transform")

        if action == "add":
            if name in by_name:
                warnings.append(f"оверрайд add: деталь «{name}» уже есть — пропущен")
                continue
            pl = ov.get("placement") or {}
            if any(e not in pl for e in _EDGES):
                warnings.append(f"оверрайд add «{name}»: не задан placement целиком")
                continue
            p = {
                "name": name, "type": ov.get("type", "shelf"),
                "basis_orientation": ov.get("orientation", "horizont"),
                "material": ov.get("material", "ЛДСП"),
                "placement": {e: _r(pl[e]) for e in _EDGES},
                "rotation": {"x": 0, "y": 0, "z": 0},
                "edge_banding": {"top": 0.4, "bottom": 0.4, "left": 0.4, "right": 0.4},
                "estimated": False,
                "override": True,
            }
            _refresh_derived(p)
            panels.append(p)
            by_name[name] = p
            continue

        target = by_name.get(name)
        if target is None:
            warnings.append(f"оверрайд: деталь «{name}» не найдена "
                            f"(изменились секции/габариты?) — правка пропущена")
            continue

        if action == "delete":
            panels.remove(target)
            by_name.pop(name, None)
            continue

        if action == "rename":
            new = str(ov.get("to", "")).strip()
            if new and new not in by_name:
                by_name.pop(name, None)
                target["name"] = new
                by_name[new] = target
            continue

        # transform: абсолютные правки граней + сдвиг
        pl = target["placement"]
        for e, v in (ov.get("placement") or {}).items():
            if e in _EDGES and isinstance(v, (int, float)):
                pl[e] = _r(v)
        mv = ov.get("move")
        if isinstance(mv, (list, tuple)) and len(mv) == 3:
            for axis, d in zip(("x", "y", "z"), mv):
                if d:
                    pl[f"{axis}1"] = _r(pl[f"{axis}1"] + d)
                    pl[f"{axis}2"] = _r(pl[f"{axis}2"] + d)
        for axis in ("x", "y", "z"):                   # грани не перепутаны
            if pl[f"{axis}1"] > pl[f"{axis}2"]:
                pl[f"{axis}1"], pl[f"{axis}2"] = pl[f"{axis}2"], pl[f"{axis}1"]
        target["override"] = True
        _refresh_derived(target)

    if warnings:
        project.setdefault("warnings", []).extend(warnings)
    return project
