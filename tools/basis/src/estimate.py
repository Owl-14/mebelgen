"""Смета изделия по производственной базе (Studio C1, AKD-128).

Считает стоимость live: плита по площади деталей (×coef отхода позиции),
кромка по длине кромления, фурнитура по выбранным/первым кандидатам слотов,
крепёж по присадкам. Цены — из базы (`cost`); позиции без цены дают строку
с qty и предупреждение, в итог не входят.

Это ОЦЕНКА материалов (себестоимость закупки), не коммерческая цена изделия.
"""

from __future__ import annotations

from typing import Any

_M2 = 1e-6      # мм² → м²
_MM = 1e-3      # мм → м


def _face(p: dict[str, Any]) -> tuple[float, float]:
    """Два больших габарита детали (плоскость детали), мм."""
    pl = p["placement"]
    dims = sorted((pl["x2"] - pl["x1"], pl["y2"] - pl["y1"], pl["z2"] - pl["z1"]),
                  reverse=True)
    return dims[0], dims[1]


def _row(group: str, name: str, qty: float, unit: str,
         unit_cost: float | None, note: str = "") -> dict[str, Any]:
    cost = round(qty * unit_cost, 2) if unit_cost and unit_cost > 0 else None
    return {"group": group, "name": name, "qty": round(qty, 2), "unit": unit,
            "unit_cost": unit_cost, "cost": cost, "note": note}


def estimate_project(project: dict[str, Any],
                     base: dict[str, Any] | None = None) -> dict[str, Any]:
    from .materials import load_base, resolve_project_materials
    base = base or load_base()
    refs = project.get("material_refs") or resolve_project_materials(project, base=base)
    panels = [p for p in project.get("panels", []) if isinstance(p.get("placement"), dict)]
    hw = project.get("hardware", {}) or {}
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []

    # --- плита: площади деталей по слоям корпус/фасады/задник ---
    from .decor_colors import FACADE_TYPES
    back_names = {p["name"] for p in panels
                  if p.get("type") == "back" and float(p.get("thickness", 16)) <= 6}
    has_facade_ref = bool((refs.get("facade") or {}).get("resolved"))
    facade_names = {p["name"] for p in panels
                    if has_facade_ref and p.get("type") in FACADE_TYPES}
    area_board = sum(_face(p)[0] * _face(p)[1] for p in panels
                     if p["name"] not in back_names and p["name"] not in facade_names) * _M2
    area_facade = sum(_face(p)[0] * _face(p)[1] for p in panels
                      if p["name"] in facade_names) * _M2
    area_back = sum(_face(p)[0] * _face(p)[1] for p in panels
                    if p["name"] in back_names) * _M2

    def _sheet_row(slot: str, label: str, area: float):
        if area <= 0:
            return
        r = refs.get(slot) or {}
        coef = 1.2                                     # запас на раскрой по умолчанию
        item = r if r.get("resolved") and r.get("cost") is not None else None
        if item:
            rows.append(_row("Материалы", f"{label}: {r.get('name')}",
                             round(area * coef, 2), "кв.м", r.get("cost"),
                             f"площадь деталей {area:.2f} м² × {coef} раскрой"))
        else:
            rows.append(_row("Материалы", f"{label}: позиция не подобрана",
                             round(area * coef, 2), "кв.м", None))
            warnings.append(f"{label}: нет цены (слот {slot})")

    _sheet_row("board", "Плита", area_board)
    _sheet_row("facade", "Плита фасадов", area_facade)
    _sheet_row("back", "Задник", area_back)

    # --- кромка: длина кромления по edge_banding деталей ---
    edge_len = 0.0
    for p in project.get("panels", []):
        eb = p.get("edge_banding") or {}
        if not isinstance(p.get("placement"), dict) or not eb:
            continue
        a, b = _face(p)
        for side, ln in (("top", a), ("bottom", a), ("left", b), ("right", b)):
            if eb.get(side):
                edge_len += ln * _MM
    if edge_len > 0:
        r = refs.get("edge") or {}
        if r.get("resolved") and r.get("cost") is not None:
            rows.append(_row("Материалы", f"Кромка: {r.get('name')}",
                             round(edge_len * 1.1, 2), "пог.м", r.get("cost"),
                             "×1.1 запас"))
        else:
            rows.append(_row("Материалы", "Кромка: позиция не подобрана",
                             round(edge_len * 1.1, 2), "пог.м", None))
            warnings.append("Кромка: нет цены")

    # --- фурнитура: выбранная позиция (или первый кандидат шорт-листа) × количество ---
    holes = None

    def _n_holes(substr: str) -> int:
        nonlocal holes
        if holes is None:
            try:
                from .hardware import compute_drilling
                holes = compute_drilling(project)
            except Exception:
                holes = []
        return sum(1 for h in holes if substr in h["purpose"])

    qty_by_slot = {
        "handles": (hw.get("handles") or {}).get("count") or 0,
        "hinges": _n_holes("петля (чашка"),
        "drawer_guides": len(project.get("drawers", [])),   # комплектов (пар)
        "legs": (hw.get("legs") or {}).get("count") or 0,
        "locks": 1 if hw.get("locks") else 0,
    }
    labels = {"handles": "Ручки", "hinges": "Петли", "drawer_guides": "Направляющие",
              "legs": "Опоры", "locks": "Замки"}
    for slot, qty in qty_by_slot.items():
        if qty <= 0:
            continue
        r = refs.get(slot) or {}
        cand = (r.get("candidates") or [{}])[0]
        name = cand.get("name") or r.get("name")
        cost = cand.get("cost") if cand else r.get("cost")
        mark = "" if r.get("chosen") else " (первый кандидат)"
        if name and cost and cost > 0:
            rows.append(_row("Фурнитура", f"{labels[slot]}: {name}{mark}",
                             qty, "шт", cost))
        elif name:
            rows.append(_row("Фурнитура", f"{labels[slot]}: {name}{mark}", qty, "шт", None))
            warnings.append(f"{labels[slot]}: нет цены")

    # --- крепёж по присадкам ---
    try:
        from .hardware import compute_drilling, fastener_bom
        from .materials import by_article
        if holes is None:
            holes = compute_drilling(project)
        for f in fastener_bom(holes, resolve=True):
            cost = None
            if f.get("article"):
                item = by_article(str(f["article"]), base=base)
                if item and isinstance(item.get("cost"), (int, float)) and item["cost"] > 0:
                    cost = float(item["cost"])
            rows.append(_row("Крепёж", f.get("base_name") or f["name"], f["qty"], "шт", cost))
    except Exception as e:
        warnings.append(f"крепёж: {e}")

    total = round(sum(r["cost"] for r in rows if r["cost"]), 2)
    return {"rows": rows, "total": total, "currency": "₽", "warnings": warnings}
