"""Конвертер старого FurnitureSpec (three-spike) → ParamSpec (AKD-19).

Старый формат: {type, dimensions{*Mm}, parts{top,sidePanels,doors,plinth,...},
features{shelves,drawers,sectionCount,rod,...}}. Маппим эвристически на архетипы
текущего генератора и прогоняем как regression-набор.
"""

from __future__ import annotations

from typing import Any

ARCHETYPE = {
    "cabinet": "cabinet", "wardrobe": "wardrobe", "drawer_unit": "drawer_unit",
    "desk_panel": "desk", "countertop": "desk", "lectern": "corpus",
    "coffee_round": "round_table", "coffee_fluted": "round_table", "coffee_rect": "corpus",
    "built_in_run": "cabinet", "kitchen_run": "cabinet",
}


def old_to_paramspec(old: dict[str, Any]) -> dict[str, Any]:
    t = old.get("type", "")
    dim = old.get("dimensions", {})
    parts = old.get("parts", {}) or {}
    feat = old.get("features", {}) or {}
    top = parts.get("top") or {}
    sides = parts.get("sidePanels") or {}
    plinth = parts.get("plinth") or {}

    W = float(dim.get("widthMm", 0))
    D = float(dim.get("depthMm", 0))
    H = float(dim.get("heightMm", 0))
    th = float(sides.get("thicknessMm") or top.get("thicknessMm") or 16)
    ph = float(plinth.get("heightMm", 0)) if feat.get("plinth") else 0.0
    color_code = top.get("colorCode")
    color_sys = top.get("colorSystem")
    color = f"{color_sys} {color_code}".strip() if color_code else "по согласованию"

    arche = ARCHETYPE.get(t, "corpus")
    spec: dict[str, Any] = {
        "schemaVersion": "paramspec-v1",
        "project_name": old.get("title") or old.get("id") or "item",
        "furniture_type": t,
        "archetype": arche,
        "dimensions": {"width": W, "depth": D, "height": H},
        "materials": {
            "board_thickness": th, "back_thickness": 16,
            "board_material": top.get("material") or "ЛДСП",
            "color": color, "color_code": color if color_code else "",
        },
        "legs": ({"type": "цоколь", "height": ph, "as_panel": True} if ph else {"type": "нет", "height": 0}),
        "gaps": {"facade": 2, "default": 2},
        "warnings": [f"Конвертировано из старого FurnitureSpec (type={t})."],
        "estimated_values": ["конструкция секций (эвристика конвертера)"],
    }

    doors = int((parts.get("doors") or {}).get("count", 0) or 0)
    shelves = int(feat.get("shelves", 0) or 0)
    drawers = int(feat.get("drawers", 0) or 0)
    nsec = int(feat.get("sectionCount", 0) or 0)

    if arche == "round_table":
        d = float(dim.get("diameterMm", W) or W)
        spec["dimensions"]["width"] = spec["dimensions"]["depth"] = d
        spec["top_thickness"] = float(top.get("thicknessMm") or 50)
        spec["base"] = True
    elif arche == "desk":
        screen = parts.get("frontScreen")
        spec["apron"] = bool(screen)
        if screen:
            spec["apron_height"] = float(screen.get("heightMm") or 300)
    elif arche == "corpus":
        pass
    elif arche == "drawer_unit":
        spec["sections"] = [{"id": "d", "kind": "drawers", "drawers": max(1, drawers)}]
    else:  # cabinet / wardrobe — многоколоночный
        cols = nsec if nsec else (2 if doors >= 2 else 1)
        cols = max(1, min(cols, 8))
        per = shelves // cols if cols else shelves
        secs = []
        for i in range(cols):
            s: dict[str, Any] = {"id": f"s{i + 1}", "kind": "door" if doors else "shelves"}
            if per:
                s["shelves"] = per
            if doors:
                s["door"] = 1
            secs.append(s)
        spec["sections"] = secs
        spec["interior_z_front"] = th

    return spec
