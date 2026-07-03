"""Расчёт присадок (сверловки) под фурнитуру из геометрии проекта (AKD-88).

Формат-независимое ядро: по `project` (панели + hardware) считает КООРДИНАТЫ
отверстий под ручки, петли, полкодержатели и стяжки — это то, что сверлит ЧПУ и
что нужно для производственной модели. Кодирование в `.cfrn` (table.holes +
objType 5) — отдельный шаг (нужна облачная проверка). 3D-меш фурнитуры берётся из
каталога БАЗИС (десктоп-импортёр, AKD-14) — здесь не строим.

Каждое отверстие: {panel, purpose, x, y, z, diameter, depth, axis, dir}
где (x,y,z) — точка входа сверла в мировых координатах, axis∈{x,y,z}, dir=±1.
"""
from __future__ import annotations

from typing import Any

_VERT = ("side_left", "side_right", "vertical_partition")
_FACADE = ("door_front", "drawer_front")


def _hole(panel: str, purpose: str, x: float, y: float, z: float,
          diameter: float, depth: float, axis: str, dir: int) -> dict[str, Any]:
    return {"panel": panel, "purpose": purpose,
            "x": round(x, 1), "y": round(y, 1), "z": round(z, 1),
            "diameter": diameter, "depth": depth, "axis": axis, "dir": dir}


def _spread(a: float, b: float, step: float) -> list[float]:
    """Точки от a до b включительно с шагом ≤ step (минимум 2, если b>a)."""
    if b <= a:
        return [a]
    import math
    n = max(1, math.ceil((b - a) / step))
    return [a + (b - a) * i / n for i in range(n + 1)]


def _pair_centers(a: float, b: float, *, step: float = 64.0, margin: float = 31.0,
                  two_pairs_at: float = 400.0) -> list[float]:
    """Позиции отверстий конфирматов вдоль отрезка стыка [a, b].

    Реверс готовых изделий БАЗИС (BASIS_FASTENERS_REVERSE): конфирматы идут
    ПАРАМИ с шагом 64 мм, отступ пары от торца ~31; при длинном стыке (≥400)
    пары у обоих концов, при коротком — одна пара по центру, при совсем
    узком — одиночный конфирмат в центре.
    """
    L = b - a
    if L < step + 20:
        return [(a + b) / 2]
    if L < two_pairs_at:
        c = (a + b) / 2
        return [c - step / 2, c + step / 2]
    return [a + margin, a + margin + step, b - margin - step, b - margin]


def _n_hinges(h: float) -> int:
    if h <= 900:
        return 2
    if h <= 1600:
        return 3
    if h <= 2000:
        return 4
    return 5


def _hinge_levels(y1: float, y2: float, n: int, margin: float = 100.0) -> list[float]:
    lo, hi = y1 + margin, y2 - margin
    if n <= 1:
        return [(lo + hi) / 2]
    return [lo + (hi - lo) * k / (n - 1) for k in range(n)]


def compute_drilling(project: dict[str, Any]) -> list[dict[str, Any]]:
    panels = project.get("panels", [])
    hw = project.get("hardware", {}) or {}
    holes: list[dict[str, Any]] = []

    by_type: dict[str, list[dict[str, Any]]] = {}
    for p in panels:
        by_type.setdefault(p.get("type", ""), []).append(p)
    verticals = [p for p in panels if p.get("type") in _VERT]

    # --- Ручки: 2 винта на фасад по межцентровому = handles.size ---
    handles = hw.get("handles") or {}
    size = float(handles.get("size") or 0)
    off = float(handles.get("offset_from_top", 40))
    if (handles.get("count") or 0) > 0 and size > 0:
        for p in panels:
            t = p.get("type")
            if t not in _FACADE:
                continue
            pl = p["placement"]
            # винт входит с ЗАДНЕЙ (внутренней) грани фасада, сквозной
            z_in, t_f = pl["z2"], pl["z2"] - pl["z1"]
            if t == "drawer_front":
                cx = (pl["x1"] + pl["x2"]) / 2
                y = pl["y2"] - off
                for dx in (-size / 2, size / 2):
                    holes.append(_hole(p["name"], "ручка (винт)", cx + dx, y, z_in, 5, t_f, "z", -1))
            else:  # door_front: ручка у кромки открывания, 2 винта по вертикали
                nm = p["name"].lower()
                hx = pl["x1"] + 40 if "прав" in nm else pl["x2"] - 40
                cy = (pl["y1"] + pl["y2"]) / 2
                for dy in (-size / 2, size / 2):
                    holes.append(_hole(p["name"], "ручка (винт)", hx, cy + dy, z_in, 5, t_f, "z", -1))

    # --- Петли: чашка Ø35 на фасаде + 2 отв ответной планки на боковине ---
    for p in panels:
        if p.get("type") != "door_front":
            continue
        pl = p["placement"]
        h = pl["y2"] - pl["y1"]
        n = _n_hinges(h)
        nm = p["name"].lower()
        hinge_left = "прав" not in nm            # левая дверь — петли слева
        cup_x = pl["x1"] + 22 if hinge_left else pl["x2"] - 22
        # ближайшая вертикаль со стороны петель
        side_x = pl["x1"] if hinge_left else pl["x2"]
        side = min(verticals, key=lambda v: abs(((v["placement"]["x1"] + v["placement"]["x2"]) / 2) - side_x), default=None)
        for y in _hinge_levels(pl["y1"], pl["y2"], n):
            # чашка сверлится с ВНУТРЕННЕЙ (задней) грани двери, глухая 12 мм
            holes.append(_hole(p["name"], "петля (чашка Ø35)", cup_x, y, pl["z2"], 35, 12, "z", -1))
            if side is not None:
                sp = side["placement"]
                sx = sp["x2"] if hinge_left else sp["x1"]
                sdir = -1 if hinge_left else 1
                z_pl = sp["z1"] + 37                  # планка на 37 от переднего края боковины
                for dy in (-16, 16):
                    holes.append(_hole(side["name"], "петля (планка)", sx, y + dy, z_pl, 5, 12, "x", sdir))

    # --- Полкодержатели: 4 отв на съёмную полку (2 на каждую боковину/перегородку) ---
    for p in panels:
        if p.get("type") != "shelf":
            continue
        pl = p["placement"]
        yc = (pl["y1"] + pl["y2"]) / 2
        m = min(110.0, (pl["z2"] - pl["z1"]) * 0.25)   # отступ ~110 (реверс БАЗИС)
        zf, zb = pl["z1"] + m, pl["z2"] - m
        for edge_x, want in ((pl["x1"], "x2"), (pl["x2"], "x1")):
            v = min((v for v in verticals if abs(v["placement"][want] - edge_x) < 1.0),
                    key=lambda v: abs(v["placement"][want] - edge_x), default=None)
            if v is None:
                continue
            # сверлим В ТЕЛО боковины: у левой (её x2 = грань полки) — в −X,
            # у правой (её x1) — в +X. Раньше был инверт (дырка уходила в полку).
            into = -1 if want == "x2" else 1
            for z in (zf, zb):
                holes.append(_hole(v["name"], "полкодержатель", edge_x, yc, z, 5, 10, "x", into))

    # --- Стяжки Y-стыков: дно/крышка/столешница ↔ боковины/перегородки.
    #     Боковина стоит на дне (стык по Y, X-диапазоны вложены). Конфирматы —
    #     ПАРАМИ с шагом 64 (реверс BASIS_FASTENERS_REVERSE); столешница на
    #     опорах (top_on_supports) — скрытый крепёж: шкант 8×30 + minifix
    #     (видимую пласть столешницы не сверлим насквозь). ---
    construction = str((project.get("carcass_calculation") or {}).get("construction", ""))
    hidden_top = construction == "top_on_supports"
    for p in panels:
        if p.get("type") not in ("bottom", "top"):
            continue
        pl = p["placement"]
        for v in verticals:
            vp = v["placement"]
            if not (vp["x1"] >= pl["x1"] - 1 and vp["x2"] <= pl["x2"] + 1):
                continue
            if p["type"] == "bottom" and abs(vp["y1"] - pl["y2"]) < 1:
                y, ydir = pl["y1"], 1          # снизу дна вверх в торец боковины
            elif p["type"] == "top" and abs(vp["y2"] - pl["y1"]) < 1:
                y, ydir = pl["y2"], -1         # сверху крышки вниз в торец боковины
            else:
                continue
            xc = (vp["x1"] + vp["x2"]) / 2
            cz1, cz2 = max(pl["z1"], vp["z1"]), min(pl["z2"], vp["z2"])
            if p["type"] == "top" and hidden_top:
                # шканты по краям зоны контакта + эксцентрики на 34 внутрь
                for z in (cz1 + 50, cz2 - 50):
                    holes.append(_hole(v["name"], "шкант 8×30 (торец)", xc, vp["y2"], z, 8, 20, "y", -1))
                    holes.append(_hole(p["name"], "шкант 8×30 (пласть)", xc, pl["y1"], z, 8, 12, "y", 1))
                for z in (cz1 + 50 + 34, cz2 - 50 - 34):
                    holes.append(_hole(p["name"], "эксцентрик (чашка Ø15)", xc, pl["y1"], z, 15, 13, "y", 1))
                    holes.append(_hole(v["name"], "эксцентрик (шток)", xc, vp["y2"], z, 5, 34, "y", -1))
                continue
            for z in _pair_centers(cz1 + 10, cz2 - 10):
                holes.append(_hole(p["name"], "стяжка (конфирмат)", xc, y, z, 7, 50, "y", ydir))

    # --- Стяжки X-стыков: торец горизонтали/царги/цоколя → пласть боковины.
    #     Раньше НЕ покрывалось вовсе (стол был без крепежа): царга/экран к
    #     боковинам, дно/крышка МЕЖДУ боковинами, цоколь. Конфирматы парами
    #     сквозь вертикаль в торец примыкающей панели. ---
    x_joint_types = ("bottom", "top", "back", "screen", "plinth")
    for p in panels:
        if p.get("type") not in x_joint_types:
            continue
        if p.get("type") == "back" and float(p.get("thickness", 16)) <= 6:
            continue                                  # тонкий ДВП-задник — гвозди
        pl = p["placement"]
        for v in verticals:
            vp = v["placement"]
            side = None
            if abs(pl["x1"] - vp["x2"]) < 1:          # панель справа от вертикали
                x, xdir = vp["x1"], 1                 # сверлим сквозь вертикаль в +X
            elif abs(pl["x2"] - vp["x1"]) < 1:        # панель слева от вертикали
                x, xdir = vp["x2"], -1
            else:
                continue
            cy1, cy2 = max(pl["y1"], vp["y1"]), min(pl["y2"], vp["y2"])
            cz1, cz2 = max(pl["z1"], vp["z1"]), min(pl["z2"], vp["z2"])
            if cy2 - cy1 < 20 or cz2 - cz1 < 20:      # нет полноценного контакта
                continue
            # раскладка пар вдоль длинной стороны зоны контакта
            if (cz2 - cz1) >= (cy2 - cy1):
                yc = (cy1 + cy2) / 2
                pts = [(yc, z) for z in _pair_centers(cz1 + 10, cz2 - 10)]
            else:
                zc = (cz1 + cz2) / 2
                pts = [(y, zc) for y in _pair_centers(cy1 + 10, cy2 - 10)]
            for y, z in pts:
                holes.append(_hole(v["name"], "стяжка (конфирмат)", x, y, z, 7, 50, "x", xdir))

    # --- Гвозди задника: по периметру + вдоль внутренних полок/стоек (реверс
    #     готовой тумбы БАЗИС: гвоздь 1.6×25, шаг ≤250, см. BASIS_FASTENERS_REVERSE) ---
    for p in panels:
        if p.get("type") != "back" or float(p.get("thickness", 16)) > 6:
            continue                                  # только тонкий ДВП-задник
        pl = p["placement"]
        m = 12.0                                      # отступ от кромки ДВП
        xs = _spread(pl["x1"] + m, pl["x2"] - m, 250)
        ys = _spread(pl["y1"] + m, pl["y2"] - m, 250)
        pts = [(x, pl["y1"] + m) for x in xs] + [(x, pl["y2"] - m) for x in xs] \
            + [(pl["x1"] + m, y) for y in ys[1:-1]] + [(pl["x2"] - m, y) for y in ys[1:-1]]
        # внутренние стойки/полки, примыкающие к заднику сзади
        for q in panels:
            qp = q.get("placement")
            if q.get("type") not in ("vertical_partition", "shelf") or not qp:
                continue
            if qp["z2"] < pl["z1"] - 20:              # не доходит до задника
                continue
            if q["type"] == "vertical_partition":
                cx = (qp["x1"] + qp["x2"]) / 2
                if pl["x1"] < cx < pl["x2"]:
                    pts += [(cx, y) for y in _spread(max(qp["y1"], pl["y1"]) + m,
                                                     min(qp["y2"], pl["y2"]) - m, 250)]
            else:
                cy = (qp["y1"] + qp["y2"]) / 2
                if pl["y1"] < cy < pl["y2"]:
                    pts += [(x, cy) for x in _spread(max(qp["x1"], pl["x1"]) + m,
                                                     min(qp["x2"], pl["x2"]) - m, 250)]
        # гвоздь ставится только там, где за ДВП есть тело (торец панели):
        # задник «в проём» (между боковинами) не перекрывает их торцы — такие
        # точки периметра пропускаем, держат дно/крышка/стойки
        bodies = [q["placement"] for q in panels
                  if q.get("placement") and q is not p
                  and q["placement"]["z2"] >= pl["z1"] - 1.5]
        tip_z = pl["z2"] - 25 * 0.6                   # середина заглубления гвоздя
        for x, y in dict.fromkeys(pts):
            if not any(b["x1"] - 1 <= x <= b["x2"] + 1 and b["y1"] - 1 <= y <= b["y2"] + 1
                       and b["z1"] - 1 <= tip_z <= b["z2"] + 1 for b in bodies):
                continue
            holes.append(_hole(p["name"], "задник (гвоздь)", x, y, pl["z2"], 1.6, 25, "z", -1))

    # --- Короб ящика: саморезы 3.5×16 сквозь бок в торцы дна и задней стенки ---
    for p in panels:
        if p.get("type") not in ("drawer_side_left", "drawer_side_right"):
            continue
        pl = p["placement"]
        outer_x = pl["x1"] if p["type"] == "drawer_side_left" else pl["x2"]
        into = 1 if p["type"] == "drawer_side_left" else -1
        for q in panels:
            qp = q.get("placement")
            if not qp:
                continue
            edge = qp["x1"] if p["type"] == "drawer_side_left" else qp["x2"]
            near = (abs(edge - (pl["x2"] if into > 0 else pl["x1"])) < 1.0
                    and qp["y1"] < pl["y2"] and qp["y2"] > pl["y1"])   # тот же ящик (Y-пересечение)
            if q.get("type") == "drawer_bottom" and near:
                yc = (qp["y1"] + qp["y2"]) / 2
                zlo, zhi = max(qp["z1"], pl["z1"]), min(qp["z2"], pl["z2"])
                for z in _spread(zlo + 60, zhi - 60, 300):
                    holes.append(_hole(p["name"], "короб ящика (саморез)",
                                       outer_x, yc, z, 3.5, 16, "x", into))
            elif q.get("type") == "drawer_back" and near:
                zc = (qp["z1"] + qp["z2"]) / 2
                if zc <= pl["z2"]:
                    # стенка между боковинами: сквозь бок в торец стенки
                    for y in (qp["y1"] + 25, qp["y2"] - 25):
                        holes.append(_hole(p["name"], "короб ящика (саморез)",
                                           outer_x, y, zc, 3.5, 16, "x", into))

    # --- Задняя стенка ящика ЗА боковинами (касается только торца дна):
    #     саморезы сквозь стенку по −Z в торец дна ---
    seen_back: set[str] = set()
    for q in panels:
        if q.get("type") != "drawer_back" or q["name"] in seen_back:
            continue
        qp = q["placement"]
        bottom = next((b for b in panels if b.get("type") == "drawer_bottom"
                       and abs(b["placement"]["z2"] - qp["z1"]) < 1.5
                       and b["placement"]["x1"] < (qp["x1"] + qp["x2"]) / 2 < b["placement"]["x2"]
                       and qp["y1"] - 1 <= b["placement"]["y1"] <= qp["y2"] + 1), None)
        if bottom is None:
            continue
        sides_reach = any(p.get("type") in ("drawer_side_left", "drawer_side_right")
                          and p["placement"]["z2"] >= qp["z1"] + 1 for p in panels
                          if p.get("placement") and p["placement"]["y1"] < qp["y2"]
                          and p["placement"]["y2"] > qp["y1"])
        if sides_reach:
            continue                                   # покрыто сквозь-бок веткой
        seen_back.add(q["name"])
        bp = bottom["placement"]
        yb = (bp["y1"] + bp["y2"]) / 2                 # уровень торца дна
        for x in _spread(max(qp["x1"], bp["x1"]) + 40, min(qp["x2"], bp["x2"]) - 40, 300):
            holes.append(_hole(q["name"], "короб ящика (саморез)",
                               x, yb, qp["z2"], 3.5, 30, "z", -1))

    # --- Замки (AKD-137): цилиндр Ø18 сквозь фасад. Дверь — сторона ручки;
    #     ящики — центральный замок в верхнем фасаде ---
    if hw.get("locks"):
        doors = [p for p in panels if p.get("type") == "door_front"]
        for p in doors:
            pl = p["placement"]
            nm = p["name"].lower()
            lx = pl["x2"] - 30 if "прав" not in nm else pl["x1"] + 30
            ly = (pl["y1"] + pl["y2"]) / 2
            holes.append(_hole(p["name"], "замок (цилиндр Ø18)", lx, ly, pl["z2"],
                               18, pl["z2"] - pl["z1"], "z", -1))
        fronts = [p for p in panels if p.get("type") == "drawer_front"]
        if fronts and not doors:
            top_front = max(fronts, key=lambda q: q["placement"]["y2"])
            pl = top_front["placement"]
            holes.append(_hole(top_front["name"], "замок (цилиндр Ø18)",
                               (pl["x1"] + pl["x2"]) / 2, pl["y2"] - 30, pl["z2"],
                               18, pl["z2"] - pl["z1"], "z", -1))

    # --- Направляющие ящиков: винты на боковинах/перегородках у КОРОБА ---
    # (по коробу, а не по фасаду: накладной фасад шире проёма и не задаёт колонку)
    for d in project.get("drawers", []):
        pos, dim = d.get("position") or {}, d.get("dimensions") or {}
        if not pos or not dim:
            continue
        box_l, box_r = float(pos["x"]), float(pos["x"]) + float(dim["width"])
        guide_y = float(pos["y"]) + 12
        left = min((v for v in verticals if v["placement"]["x2"] <= box_l + 1),
                   key=lambda v: box_l - v["placement"]["x2"], default=None)
        right = min((v for v in verticals if v["placement"]["x1"] >= box_r - 1),
                    key=lambda v: v["placement"]["x1"] - box_r, default=None)
        # винт входит с ВНУТРЕННЕЙ пласти опоры и сверлится В её тело:
        # левая опора — вход с её x2, сверло в −X; правая — с x1, в +X
        for v, inx in ((left, -1), (right, 1)):
            if v is None:
                continue
            vp = v["placement"]
            sx = vp["x2"] if inx < 0 else vp["x1"]
            for z in (vp["z1"] + 30, (vp["z1"] + vp["z2"]) / 2, vp["z2"] - 50):
                holes.append(_hole(v["name"], "направляющая (винт)", sx, guide_y, z, 5, 12, "x", inx))
    return holes


# Присадка → позиция крепежа (имя для BOM, запрос в базу, шт на отверстие).
# У шканта 2 отверстия (торец+пласть) на 1 шкант; у эксцентрика чашка = 1 шт,
# отверстие штока — в комплекте (0).
_FASTENER_MAP = {
    "стяжка (конфирмат)": ("Конфирмат 7×50", "конфирмат 7", 1.0),
    "задник (гвоздь)": ("Гвоздь 1.6×25", "гвоздь 1,6", 1.0),
    "короб ящика (саморез)": ("Саморез 3,5×16", "саморез потай 3,5 16", 1.0),
    "направляющая (винт)": ("Саморез 3,5×16 (направляющие)", "саморез потай 3,5 16", 1.0),
    "ручка (винт)": ("Винт М4×16", "винт м4 16", 1.0),
    "полкодержатель": ("Полкодержатель", "полкодержатель", 1.0),
    "петля (чашка Ø35)": ("Петля накладная", "петля наклад", 1.0),
    "шкант 8×30 (торец)": ("Шкант 8×30", "шкант 8", 0.5),
    "шкант 8×30 (пласть)": ("Шкант 8×30", "шкант 8", 0.5),
    "эксцентрик (чашка Ø15)": ("Эксцентрик Ø15 + шток", "эксцентрик", 1.0),
    "эксцентрик (шток)": ("Эксцентрик Ø15 + шток", "эксцентрик", 0.0),
    "замок (цилиндр Ø18)": ("Замок мебельный", "замок", 1.0),
}


def fastener_bom(holes: list[dict[str, Any]],
                 resolve: bool = False) -> list[dict[str, Any]]:
    """Крепёж по присадкам: [{name, qty, [article, base_name]}] для BOM/сметы.

    resolve=True — подобрать позицию из группы «Крепёж» производственной базы
    (первое совпадение; точный выбор за технологом).
    """
    counts: dict[str, float] = {}
    for h in holes:
        m = _FASTENER_MAP.get(h["purpose"])
        if m:
            counts[m[0]] = counts.get(m[0], 0) + m[2]
    seen: set[str] = set()
    out = []
    for (name, query, _per) in _FASTENER_MAP.values():
        if name not in counts or name in seen:
            continue
        seen.add(name)
        row: dict[str, Any] = {"name": name, "qty": round(counts[name]) or 1}
        if resolve:
            try:
                from .materials import search_base
                hit = search_base(query, category="Крепеж", limit=1) or \
                    search_base(query, limit=1)
                if hit:
                    row["article"] = hit[0].get("article")
                    row["base_name"] = hit[0].get("name")
            except Exception:
                pass
        out.append(row)
    # заглушки на видимые конфирматы (самоклейка, по 1 на конфирмат)
    conf = counts.get("Конфирмат 7×50", 0)
    if conf:
        out.append({"name": "Заглушка самоклеящаяся D13", "qty": round(conf)})
    return out


def drilling_summary(holes: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for h in holes:
        out[h["purpose"]] = out.get(h["purpose"], 0) + 1
    return out
