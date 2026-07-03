"""Раскрой-превью (AKD-129): shelf bin-packing деталей по листам, локально.

Оценочная раскладка для Studio: сколько листов уйдёт и каков % отхода.
НЕ производственная карта раскроя (то — Cutting API/цех): волокна не
учитываются, поворот детали разрешён, алгоритм — жадные «полки».
"""

from __future__ import annotations

from typing import Any

SHEET_W, SHEET_H = 2800.0, 2070.0     # стандартный лист ЛДСП
KERF = 4.0                            # пропил
MARGIN = 15.0                         # обрез кромки листа


def _face(p: dict[str, Any]) -> tuple[float, float]:
    pl = p["placement"]
    d = sorted((pl["x2"] - pl["x1"], pl["y2"] - pl["y1"], pl["z2"] - pl["z1"]),
               reverse=True)
    return d[0], d[1]


def _pack(parts: list[dict[str, Any]], sw: float, sh: float) -> list[dict[str, Any]]:
    """Жадные полки: сортировка по убыванию высоты, поворот разрешён."""
    usable_w, usable_h = sw - 2 * MARGIN, sh - 2 * MARGIN
    for p in parts:                                   # ориентируем «лёжа»
        if p["h"] > p["w"]:
            p["w"], p["h"] = p["h"], p["w"]
            p["rot"] = True
        if p["w"] > usable_w:                         # не влезает лёжа — ставим
            p["w"], p["h"] = p["h"], p["w"]
            p["rot"] = not p.get("rot", False)
    parts = sorted(parts, key=lambda p: -p["h"])
    sheets: list[dict[str, Any]] = []
    for part in parts:
        placed = False
        for sheet in sheets:
            for shelf in sheet["shelves"]:
                if part["h"] <= shelf["h"] + 0.01 and \
                        shelf["x"] + part["w"] <= usable_w + 0.01:
                    part["x"], part["y"] = shelf["x"] + MARGIN, shelf["y"] + MARGIN
                    shelf["x"] += part["w"] + KERF
                    sheet["parts"].append(part)
                    placed = True
                    break
            if placed:
                break
            y = sum(s["h"] + KERF for s in sheet["shelves"])
            if y + part["h"] <= usable_h + 0.01 and part["w"] <= usable_w + 0.01:
                sheet["shelves"].append({"x": part["w"] + KERF, "y": y, "h": part["h"]})
                part["x"], part["y"] = MARGIN, y + MARGIN
                sheet["parts"].append(part)
                placed = True
                break
        if not placed:
            if part["w"] > usable_w or part["h"] > usable_h:
                part["oversize"] = True
                part["x"] = part["y"] = MARGIN
            sheets.append({"shelves": [{"x": part["w"] + KERF, "y": 0, "h": part["h"]}],
                           "parts": [part]})
            part.setdefault("x", MARGIN)
            part.setdefault("y", MARGIN)
    return sheets


def nest_project(project: dict[str, Any],
                 sheet: tuple[float, float] = (SHEET_W, SHEET_H)) -> dict[str, Any]:
    """Раскладка деталей по листам, группировка по (материал-слой, толщина)."""
    from .decor_colors import FACADE_TYPES
    groups: dict[str, list[dict[str, Any]]] = {}
    m = project.get("materials") or {}
    has_facade = bool(m.get("facade_color") or m.get("facade_article"))
    for p in project.get("panels", []):
        if not isinstance(p.get("placement"), dict):
            continue
        t = float(p.get("thickness", 16))
        if p.get("type") == "back" and t <= 6:
            label = f"Задник {t:g} мм"
        elif has_facade and p.get("type") in FACADE_TYPES:
            label = f"Фасады {t:g} мм"
        else:
            label = f"Плита {t:g} мм"
        w, h = _face(p)
        groups.setdefault(label, []).append(
            {"name": p.get("name", ""), "w": w, "h": h})

    out_groups = []
    total_used = total_sheet = 0.0
    for label, parts in groups.items():
        area_parts = sum(p["w"] * p["h"] for p in parts)
        sheets = _pack(parts, *sheet)
        out_groups.append({"label": label, "sheets": sheets,
                           "n_sheets": len(sheets), "area_parts": area_parts})
        total_used += area_parts
        total_sheet += len(sheets) * sheet[0] * sheet[1]
    waste = round(100 * (1 - total_used / total_sheet), 1) if total_sheet else 0.0
    return {"groups": out_groups, "sheet": {"w": sheet[0], "h": sheet[1]},
            "n_sheets": sum(g["n_sheets"] for g in out_groups), "waste_pct": waste}


def nesting_svg(project: dict[str, Any]) -> str:
    """SVG-схема раскроя: листы по группам материалов, детали с подписями."""
    n = nest_project(project)
    sw, sh = n["sheet"]["w"], n["sheet"]["h"]
    scale = 0.22
    pad = 26
    W = sw * scale + 2 * pad
    y_off = pad
    rows: list[str] = []
    for g in n["groups"]:
        rows.append(f'<text x="{pad}" y="{y_off + 12}" font-size="13" font-weight="600" '
                    f'fill="#1a1d21">{g["label"]} — листов: {g["n_sheets"]}</text>')
        y_off += 22
        for sheet in g["sheets"]:
            rows.append(f'<rect x="{pad}" y="{y_off}" width="{sw * scale:.1f}" '
                        f'height="{sh * scale:.1f}" fill="#f7f8f9" stroke="#8a9099"/>')
            for p in sheet["parts"]:
                x, y = pad + p["x"] * scale, y_off + p["y"] * scale
                w, h = p["w"] * scale, p["h"] * scale
                fill = "#f3c6c6" if p.get("oversize") else "#cfe3f5"
                rows.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
                            f'fill="{fill}" stroke="#4a6f9c" stroke-width="0.8"/>')
                label = p["name"][:22] + (" ⟳" if p.get("rot") else "")
                if w > 46 and h > 12:
                    rows.append(f'<text x="{x + 3:.1f}" y="{y + 11:.1f}" font-size="9" '
                                f'fill="#1a3a5c">{label}</text>')
                    rows.append(f'<text x="{x + 3:.1f}" y="{y + 21:.1f}" font-size="8" '
                                f'fill="#5c748c">{p["w"]:g}×{p["h"]:g}</text>')
            y_off += sh * scale + 14
        y_off += 8
    header = (f'<text x="{pad}" y="16" font-size="13" fill="#1a1d21">'
              f'Раскрой-превью: {n["n_sheets"]} лист(ов) {sw:g}×{sh:g}, '
              f'отход ≈{n["waste_pct"]}% · оценка, не производственная карта</text>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.0f}" '
            f'height="{y_off + pad}" viewBox="0 0 {W:.0f} {y_off + pad}">'
            f'{header}{"".join(rows)}</svg>')
