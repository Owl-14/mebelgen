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


# Стандартные длины шариковых направляющих полного выдвижения (H=45), мм.
GUIDE_LENGTHS = (250, 300, 350, 400, 450, 500, 550, 600)
# Отступ боковины короба от боковины/перегородки корпуса: направляющая 12,7 мм
# (эталон технолога: 13 от пласти при слоте 568 → короб 550).
GUIDE_GAP = 13.0
# Короб относительно фасада (эталон): низ короба на 32 выше низа фасада,
# боковина ниже фасада на 64 (по 32 сверху и снизу), глубина = направляющая,
# которая помещается с запасом 100 до задника.
BOX_Y_OFFSET = 32.0
BOX_HEIGHT_MARGIN = 64.0
BOX_DEPTH_CLEARANCE = 100.0


def std_guide_length(available: float) -> float:
    """Самая длинная стандартная направляющая, помещающаяся в available мм."""
    fitting = [length for length in GUIDE_LENGTHS if length <= available]
    return float(fitting[-1] if fitting else GUIDE_LENGTHS[0])


def drawer_facade_span(i: int, bounds: list[tuple[float, float]], W: float, T: float,
                       reveal: float) -> tuple[float, float]:
    """Внешний X-пролёт фасада ящика (эталон технолога): от края корпуса или
    центра перегородки с зазором reveal с обеих сторон — 596 на колонну 600.
    Дверь соседней секции заканчивается на полузазоре от центра перегородки,
    два соседних стека ящиков получают между фасадами 2·reveal."""
    cx1, cx2 = bounds[i]
    left = reveal if i == 0 else round(cx1 - T / 2 + reveal, 2)
    right = round(W - reveal, 2) if i == len(bounds) - 1 else round(cx2 + T / 2 - reveal, 2)
    return left, right


def facade_x_span(i: int, bounds: list[tuple[float, float]], W: float, T: float,
                  reveal: float, gap: float) -> tuple[float, float]:
    """Внешний X-пролёт НАКЛАДНОГО фасада секции i.

    Крайняя секция перекрывает боковину (край = reveal / W−reveal); внутренняя
    доходит до центра перегородки минус полузазор. Так фасады закрывают корпус и
    стыкуются друг с другом с зазором gap, не открывая петли/направляющие."""
    cx1, cx2 = bounds[i]
    left = reveal if i == 0 else round(cx1 - T / 2 + gap / 2, 2)
    right = round(W - reveal, 2) if i == len(bounds) - 1 else round(cx2 + T / 2 - gap / 2, 2)
    return left, right


def partitions(bounds, H, T, Hleg, mat, z1, z2, t_top: float | None = None) -> list[dict[str, Any]]:
    """Вертикальные перегородки между колонками.

    Верх — под крышкой: при толстой столешнице (50 мм при плите 25) её низ
    ниже, чем H−T, и перегородка обязана останавливаться там же, иначе она
    входит в крышку (пересечение ровно на разницу толщин).
    """
    top = H - float(t_top or T)
    out = []
    for i in range(len(bounds) - 1):
        px = bounds[i][1]
        out.append(panel(f"Перегородка {i + 1}", "vertical_partition", "vertical",
                         (px, px + T), (Hleg + T, top), (z1, z2), thickness=T, material=mat))
    return out


def shelves_in_column(cx1, cx2, levels, T, z1, z2, mat, sid, label) -> list[dict[str, Any]]:
    out = []
    for i, y in enumerate(levels, start=1):
        out.append(panel(f"{label} {i}" if len(levels) > 1 else label, "shelf", "horizont",
                         (cx1, cx2), (y, y + T), (z1, z2), thickness=T, material=mat, section_id=sid))
    return out


def rod_in_column(cx1, cx2, sec, yt, iz1, iz2, sid) -> dict[str, Any] | None:
    """Штанга-вешало (AKD-177): метаданные фурнитуры (не панель) для
    hardware.rods. axis=x — поперечная между боковинами/перегородками;
    axis=z — продольная выдвижная (для малой глубины), крепится к
    горизонту над ней. height — ось штанги по Y (иначе верх проёма − 80)."""
    r = sec.get("rod")
    if not r:
        return None
    r = r if isinstance(r, dict) else {}
    dia = float(r.get("diameter", 25))
    axis = r.get("axis", "x")
    y = float(r.get("height", yt - 80))
    zc = iz1 + (iz2 - iz1) / 2
    rod: dict[str, Any] = {"id": f"rod_{sid}", "section_id": sid, "axis": axis,
                           "diameter": dia}
    if axis == "z":
        ln = float(r.get("length", min(450.0, iz2 - iz1 - 100)))
        xc = (cx1 + cx2) / 2
        z1 = iz1 + 60
        rod.update({"x1": round(xc - dia / 2, 2), "x2": round(xc + dia / 2, 2),
                    "y1": round(y - dia / 2, 2), "y2": round(y + dia / 2, 2),
                    "z1": round(z1, 2), "z2": round(z1 + ln, 2), "length": ln})
    else:
        rod.update({"x1": round(cx1, 2), "x2": round(cx2, 2),
                    "y1": round(y - dia / 2, 2), "y2": round(y + dia / 2, 2),
                    "z1": round(zc - dia / 2, 2), "z2": round(zc + dia / 2, 2),
                    "length": round(cx2 - cx1, 2)})
    return rod


def door_in_column(cx1, cx2, y1, y2, T, mat, sid, name, *, z_mode="overlay") -> dict[str, Any]:
    # накладной фасад — ПЕРЕД корпусом (z −T..0), иначе врезается в боковину/дно.
    z = (0, T) if z_mode == "inset" else (-T, 0)
    return panel(name, "door_front", "front", (cx1, cx2), (y1, y2), z,
                 thickness=T, material=mat, section_id=sid)


def drawer_stack(cx1, cx2, fb, heights, gap, p, T, mat, sid, prefix, facade_bounds=None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], float]:
    """Стек ящиков в колонке [cx1,cx2]. p — параметры короба. Возвращает (panels, drawers_meta, top_y).

    facade_bounds=(fx1,fx2) — внешний X-пролёт НАКЛАДНОГО фасада (перекрывает корпус);
    короб живёт в проёме [cx1,cx2] и прижат к фасаду (box_z1=0)."""
    panels: list[dict[str, Any]] = []
    meta: list[dict[str, Any]] = []
    guide_gap = p.get("guide_gap", GUIDE_GAP)
    box_z1 = p.get("box_z1", 0)                          # короб прижат к фасаду (z=0)
    back_limit = p.get("back_limit")
    box_depth = p.get("box_depth", std_guide_length(
        (back_limit if back_limit is not None else 450) - box_z1 - BOX_DEPTH_CLEARANCE))
    min_front = min(heights)
    # тесный фасад (ниже 64+): отступ не выше половины фасада, а короб —
    # по старой пропорции 0.52, иначе боковина уходит в ноль
    box_y_off = min(p.get("box_y_offset", BOX_Y_OFFSET), max(0.0, round(min_front / 2, 2)))
    box_h = p.get("box_height", round(max(min_front - BOX_HEIGHT_MARGIN, min_front * 0.52), 2))
    box_back = p.get("box_back_thickness", T)
    box_bot = p.get("box_bottom_thickness", T)
    # кламп: боковины короба не выше самого низкого фасада (MEB-166)
    side_lift = box_bot if p.get("box_sides_on_bottom", False) else 0
    box_h = min(box_h, round(min(heights) - box_y_off - side_lift, 2))
    top_limit = p.get("top_limit")                       # низ крышки над стеком
    if top_limit is not None and heights:
        top_side_y1 = fb + sum(heights[:-1]) + (len(heights) - 1) * gap + box_y_off + side_lift
        box_h = min(box_h, round(top_limit - top_side_y1, 2))
    box_h = max(box_h, 8.0)                              # короб всегда положительной высоты
    bottom_mode = p.get("box_bottom_mode", "between")   # between | under
    # задняя стенка: inside (эталон технолога — между боковинами у заднего
    # торца, дно до неё) | outside (накладная за боковинами, AKD-181)
    back_mode = p.get("box_back_mode", "inside")
    back_outside = back_mode == "outside"
    # короб (с задней стенкой) не должен заходить в задник корпуса
    if back_limit is not None and box_z1 + box_depth + (box_back if back_outside else 0) > back_limit:
        box_depth = max(50, back_limit - box_z1 - (box_back if back_outside else 0))
    boxes = p.get("boxes", True)
    # накладной фасад: внешний пролёт (перекрывает боковины), иначе — врезной в проём
    fx1, fx2 = facade_bounds if facade_bounds else (cx1 + gap, cx2 - gap)
    y = fb
    for k, h in enumerate(heights, start=1):
        fy1, fy2 = y, y + h
        nm = f"{prefix}Фасад ящик {k}" if prefix else f"Фасад ящик {k}"
        panels.append(panel(nm, "drawer_front", "front", (fx1, fx2), (fy1, fy2), (-T, 0),
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
            bot_z2 = bz2 if back_outside else bz2 - box_back      # дно до задней стенки
            panels.append(panel(pre + "дно", "drawer_bottom", "horizont", bot_x, (box_y1, box_y1 + box_bot), (box_z1, bot_z2),
                                thickness=box_bot, material=mat, section_id=sid, estimated=True))
            panels.append(panel(pre + "боковина левая", "drawer_side_left", "vertical", (bxl1, bxl2), (sy1, sy2), (box_z1, bz2),
                                thickness=T, material=mat, section_id=sid, estimated=True))
            panels.append(panel(pre + "боковина правая", "drawer_side_right", "vertical", (bxr1, bxr2), (sy1, sy2), (box_z1, bz2),
                                thickness=T, material=mat, section_id=sid, estimated=True))
            if not back_outside:
                back_z, back_x, back_y = (bz2 - box_back, bz2), (bxl2, bxr1), (sy1, sy2)
            else:
                # накладная стенка перекрывает торцы боковин и дна — иначе стыки
                # только рёбрами и крепёж физически невозможен (AKD-181)
                back_z, back_x, back_y = (bz2, bz2 + box_back), (bxl1, bxr2), (box_y1, sy2)
            panels.append(panel(pre + "задняя", "drawer_back", "front", back_x, back_y, back_z,
                                thickness=box_back, material=mat, section_id=sid, estimated=True))
            meta.append({"id": f"{sid}_drawer_{k}", "count": 1, "guide_type": p.get("guide_type", "шариковые"),
                         "soft_close": False, "lock": False,
                         "dimensions": {"width": round(bxr1 - bxl2, 2), "height": box_h, "depth": box_depth},
                         "position": {"x": round(bxl2, 2), "y": round(box_y1, 2), "z": round(box_z1, 2)}, "estimated": True})
        y = fy2 + gap
    return panels, meta, fb + sum(heights) + (len(heights) - 1) * gap
