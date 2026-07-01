"""Политика выбора материалов/фурнитуры (AKD-76).

Из ТЗ агент извлекает ParamSpec; часть материалов/фурнитуры в ТЗ может быть не
указана. `apply_material_policy` детерминированно ДОЗАПОЛНЯЕТ пропуски по
архетипу/габаритам/секциям — НЕ затирая явно указанное — чтобы из ОДНОГО ТЗ
собиралась полная, готовая к производству модель. Все принятые допущения
пишутся в `warnings`. Реальные артикулы подбирает потом materials.resolve_*.
"""
from __future__ import annotations

import copy
from typing import Any

_GENERIC = ("соглас", "уточн", "не задан", "любой", "по цвету")


def _is_generic_color(c: Any) -> bool:
    c = (str(c) if c is not None else "").strip().lower()
    return (not c) or c in ("—", "-", "n/a", "нет") or any(w in c for w in _GENERIC)


_GUIDE_LENGTHS = (250, 300, 350, 400, 450, 500, 550)


def _guide_len(depth: float | None) -> int:
    target = (depth or 500) - 30          # зазор от задней стенки
    ok = [L for L in _GUIDE_LENGTHS if L <= target]
    return ok[-1] if ok else _GUIDE_LENGTHS[0]


_FLOOR = ("corpus", "drawer_unit", "door_unit", "wardrobe", "cabinet")


def apply_material_policy(spec: dict[str, Any]) -> dict[str, Any]:
    """Дозаполнить materials/hardware/legs ParamSpec детерминированными дефолтами.
    Не изменяет исходный dict (работает на копии). Возвращает дополненный spec."""
    spec = copy.deepcopy(spec)
    m = spec.setdefault("materials", {})
    w = spec.setdefault("warnings", [])
    arch = str(spec.get("archetype", ""))
    dim = spec.get("dimensions", {}) or {}
    sections = spec.get("sections", []) or []
    feats = " ".join(str(f).lower() for f in spec.get("features", []) or [])
    name = str(spec.get("project_name", "")).lower()

    n_drawers = sum(int(s.get("drawers", 0) or 0) for s in sections if s.get("kind") == "drawers")
    n_doors = sum(int(s.get("door", 1) or 1) for s in sections if s.get("kind") == "door")
    if arch == "door_unit" and n_doors == 0:
        n_doors = 1
    has_drawers = n_drawers > 0 or bool((spec.get("hardware") or {}).get("drawer_guides"))
    has_doors = n_doors > 0
    facades = has_drawers or has_doors or arch in ("drawer_unit", "door_unit", "wardrobe")

    T = m.get("board_thickness", 16)
    m.setdefault("board_material", "ЛДСП")

    # Задняя стенка: ДВП 3 мм по умолчанию (реальный стандарт корпусной мебели)
    if not m.get("back_material"):
        m["back_material"] = "ДВП"
        w.append("Задняя стенка в ТЗ не указана — принята ДВП 3 мм (стандарт).")
    bm = str(m["back_material"]).lower()
    if not m.get("back_thickness"):
        m["back_thickness"] = 3 if ("двп" in bm or "хдф" in bm) else T

    # Кромка: видимые фасады/торцы — 2 мм ПВХ, скрытые — 0.4 мм
    if m.get("edge_band_thickness") in (None, ""):
        m["edge_band_thickness"] = 2.0 if facades else 0.4

    # Цвет/декор: из ТЗ (или кода декора), иначе дефолтный реальный декор + warning
    if _is_generic_color(m.get("color")) and not m.get("color_code"):
        m["color"] = "Белый"
        w.append("Цвет/декор в ТЗ не указан — принят дефолт «Белый» (ЛДСП), подтвердить с заказчиком.")

    # --- Фурнитура ---
    hw = spec.setdefault("hardware", {})
    h = hw.get("handles") or {}
    if facades and not (h.get("count") or 0) and "push" not in feats and "без ручек" not in feats:
        n_fac = (n_drawers + n_doors) or 1
        col = h.get("color") or (m.get("color") if not _is_generic_color(m.get("color")) else "—")
        hw["handles"] = {"type": h.get("type") or "ручка-скоба", "material": "металл", "color": col,
                         "size": h.get("size") or 128, "count": n_fac,
                         "offset_from_top": h.get("offset_from_top", 32), "furniture_encoded": ""}
        w.append(f"Ручки в ТЗ не детализированы — принята ручка-скоба 128 мм ×{n_fac}.")

    if has_drawers and not hw.get("drawer_guides"):
        L = _guide_len(dim.get("depth_carcass") or dim.get("depth"))
        hw["drawer_guides"] = {"type": "шариковые с доводчиком", "length_mm": L,
                               "soft_close": True, "furniture_encoded": ""}
        w.append(f"Направляющие не указаны — приняты шариковые {L} мм с доводчиком.")

    if has_doors and not hw.get("hinges"):
        hw["hinges"] = {"type": "петля накладная 110°", "furniture_encoded": ""}
        w.append("Петли не указаны — приняты накладные петли 110°.")

    # Опоры: подкатное изделие → колёса; иначе не форсируем (геометрия из legs.height ТЗ)
    legs = spec.get("legs") or {}
    if arch in _FLOOR and not legs.get("type"):
        if "подкат" in name or "подкат" in feats:
            spec["legs"] = {"type": "колёса (ролики)", "adjustable": False, "color": "—",
                            "height": legs.get("height", 0), "count": 4}
            w.append("Изделие подкатное, опоры не указаны — приняты 4 колёсные опоры.")

    return spec
