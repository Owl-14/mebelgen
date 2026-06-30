"""Движок многоколоночного корпуса: перегородки + содержимое колонок.

Колонка = вертикальная зона по X между боковинами/перегородками.
Содержимое колонки описывается параметрами секции ParamSpec.
"""

from __future__ import annotations

from typing import Any

from .helpers import panel, shelf_levels


def column_bounds(W: float, T: float, sections: list[dict[str, Any]]) -> list[tuple[float, float]]:
    """Внутренние X-границы колонок (между боковинами, минус перегородки)."""
    n = len(sections)
    shares = [s.get("width_share") for s in sections]
    inner = W - 2 * T - (n - 1) * T
    if all(s is not None for s in shares) and shares:
        tot = sum(shares)
        widths = [inner * s / tot for s in shares]
    else:
        widths = [inner / n] * n
    widths = [round(w) for w in widths]
    bounds: list[tuple[float, float]] = []
    x = T
    for i in range(n):
        x2 = (W - T) if i == n - 1 else x + widths[i]
        bounds.append((round(x, 2), round(x2, 2)))
        x = x2 + T
    return bounds


def partitions(bounds, H, T, Hleg, mat, z1, z2) -> list[dict[str, Any]]:
    out = []
    for i in range(len(bounds) - 1):
        px = bounds[i][1]
        out.append(panel(f"Перегородка {i + 1}", "vertical_partition", "vertical",
                         (px, px + T), (Hleg + T, H - T), (z1, z2), thickness=T, material=mat))
    return out


def shelves_in_column(cx1, cx2, levels, T, z1, z2, mat, sid, label) -> list[dict[str, Any]]:
    out = []
    for i, y in enumerate(levels, start=1):
        out.append(panel(f"{label} {i}" if len(levels) > 1 else label, "shelf", "horizont",
                         (cx1, cx2), (y, y + T), (z1, z2), thickness=T, material=mat, section_id=sid))
    return out


def door_in_column(cx1, cx2, y1, y2, T, mat, sid, name, *, z_mode="overlay") -> dict[str, Any]:
    if z_mode == "front":          # вынесена вперёд: z -T..0
        z = (-T, 0)
    else:                          # накладная/врезная по фасадной плоскости: 0..T
        z = (0, T)
    return panel(name, "door_front", "front", (cx1, cx2), (y1, y2), z,
                 thickness=T, material=mat, section_id=sid)


def drawer_stack(cx1, cx2, fb, heights, gap, p, T, mat, sid, prefix) -> tuple[list[dict[str, Any]], list[dict[str, Any]], float]:
    """Стек ящиков в колонке [cx1,cx2]. p — параметры короба. Возвращает (panels, drawers_meta, top_y)."""
    panels: list[dict[str, Any]] = []
    meta: list[dict[str, Any]] = []
    guide_gap = p.get("guide_gap", 14.5)
    box_z1 = p.get("box_z1", T)
    box_depth = p.get("box_depth", 350)
    box_y_off = p.get("box_y_offset", T)
    box_h = p.get("box_height", round(min(heights) * 0.52, 2))
    box_back = p.get("box_back_thickness", T)
    box_bot = p.get("box_bottom_thickness", T)
    bottom_mode = p.get("box_bottom_mode", "between")   # between | under
    boxes = p.get("boxes", True)
    fx1 = cx1 + gap                                      # врезной фасад в проём колонки
    fx2 = cx2 - gap
    y = fb
    for k, h in enumerate(heights, start=1):
        fy1, fy2 = y, y + h
        nm = f"{prefix}Фасад ящик {k}" if prefix else f"Фасад ящик {k}"
        panels.append(panel(nm, "drawer_front", "front", (fx1, fx2), (fy1, fy2), (0, T),
                            thickness=T, material=mat, section_id=sid, estimated=True))
        sides_on_bottom = p.get("box_sides_on_bottom", False)
        bxl1 = cx1 + guide_gap
        bxl2 = bxl1 + T
        bxr2 = cx2 - guide_gap
        bxr1 = bxr2 - T
        box_y1 = fy1 + box_y_off
        sy1 = box_y1 + (box_bot if sides_on_bottom else 0)
        sy2 = sy1 + box_h
        bz2 = box_z1 + box_depth
        if boxes:
            bot_x = (cx1 + guide_gap, cx2 - guide_gap) if bottom_mode == "under" else (bxl2, bxr1)
            pre = f"{prefix}Ящик {k} " if prefix else f"Ящик {k} "
            panels.append(panel(pre + "дно", "drawer_bottom", "horizont", bot_x, (box_y1, box_y1 + box_bot), (box_z1, bz2),
                                thickness=box_bot, material=mat, section_id=sid, estimated=True))
            panels.append(panel(pre + "боковина левая", "drawer_side_left", "vertical", (bxl1, bxl2), (sy1, sy2), (box_z1, bz2),
                                thickness=T, material=mat, section_id=sid, estimated=True))
            panels.append(panel(pre + "боковина правая", "drawer_side_right", "vertical", (bxr1, bxr2), (sy1, sy2), (box_z1, bz2),
                                thickness=T, material=mat, section_id=sid, estimated=True))
            back_z = (bz2 - box_back, bz2) if p.get("box_back_mode") == "inset" else (bz2, bz2 + box_back)
            panels.append(panel(pre + "задняя", "drawer_back", "front", (bxl2, bxr1), (sy1, sy2), back_z,
                                thickness=box_back, material=mat, section_id=sid, estimated=True))
            meta.append({"id": f"{sid}_drawer_{k}", "count": 1, "guide_type": p.get("guide_type", "шариковые"),
                         "soft_close": False, "lock": False,
                         "dimensions": {"width": round(bxr1 - bxl2, 2), "height": box_h, "depth": box_depth},
                         "position": {"x": round(bxl2, 2), "y": round(box_y1, 2), "z": round(box_z1, 2)}, "estimated": True})
        y = fy2 + gap
    return panels, meta, fb + sum(heights) + (len(heights) - 1) * gap
