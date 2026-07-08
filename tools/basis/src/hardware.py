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


def _sys32_pts(a: float, b: float) -> tuple[list[float], list[float]]:
    """Система 32 (AKD-202, стандарт производства): точки крепежа вдоль стыка
    [a, b] на присадочной сетке 32 мм — шканты Ø8 на 32 от краёв, эксцентриковые
    стяжки ещё на 32 внутрь. Короткий стык — по одной точке каждого."""
    L = b - a
    if L < 128:
        c = (a + b) / 2
        return [round(c - 16, 1)], [round(c + 16, 1)]
    # все позиции — на одной сетке 32 от начала стыка (base = a+32):
    # шканты по краям ряда, стяжки на 32 внутрь; взаимные шаги кратны 32
    last = a + 32 + 32 * int((L - 64) // 32)
    dws = [round(a + 32, 1), round(last, 1)]
    cams = [round(a + 64, 1)]
    if last - 32 > a + 64:
        cams.append(round(last - 32, 1))
    return dws, cams


def door_hinge_side(p: dict[str, Any], model_w: float | None = None) -> str:
    """Сторона петель фасада двери: 'left'|'right'|'up'|'down' (AKD-223/224).

    Приоритет: явный p['swing'] (sections[].door_swing) → имя («прав»/«лев») →
    ПО ПОЛОЖЕНИЮ: дверь в правой половине изделия навешивается справа
    (открывание наружу), в левой — слева. Раньше решало только имя, и
    одиночная дверь правой секции всегда открывалась в центр тумбы."""
    sw = str(p.get("swing") or "").lower()
    if sw in ("left", "right", "up", "down"):
        return sw
    nm = str(p.get("name", "")).lower()
    if "прав" in nm:
        return "right"
    if "лев" in nm:
        return "left"
    pl = p.get("placement") or {}
    if model_w and pl:
        c = (pl.get("x1", 0) + pl.get("x2", 0)) / 2
        return "right" if c > model_w / 2 + 1 else "left"
    return "left"


def _model_width(panels: list[dict[str, Any]]) -> float:
    return max((p["placement"]["x2"] for p in panels
                if isinstance(p.get("placement"), dict)), default=0.0)


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


def _shelf_spans(panels: list[dict[str, Any]], x1: float, x2: float) -> list[tuple[float, float]]:
    """Y-интервалы полок, пересекающих X-пролёт [x1, x2] — запретные зоны петель."""
    out: list[tuple[float, float]] = []
    for p in panels:
        if p.get("type") != "shelf" or not isinstance(p.get("placement"), dict):
            continue
        pl = p["placement"]
        if min(pl["x2"], x2) - max(pl["x1"], x1) < 30:
            continue
        out.append((pl["y1"], pl["y2"]))
    return out


def hinge_levels_clear(y1: float, y2: float, n: int,
                       spans: list[tuple[float, float]],
                       margin: float = 100.0, half: float = 35.0) -> list[float]:
    """Уровни петель, обходящие полки (AKD-185): равномерная раскладка, но
    петля, попавшая в зону полки ±half (планка 60 высотой + зазор), сдвигается
    к ближайшему свободному краю зоны."""
    lo, hi = y1 + 60, y2 - 60
    out: list[float] = []
    for y in _hinge_levels(y1, y2, n, margin):
        for _ in range(3):                      # каскад зон — до 3 сдвигов
            hit = next(((a, b) for a, b in spans if a - half < y < b + half), None)
            if hit is None:
                break
            cand = [c for c in (hit[0] - half, hit[1] + half) if lo <= c <= hi]
            if not cand:
                break
            y = min(cand, key=lambda c: abs(c - y))
        out.append(round(y, 1))
    return out


def leg_positions(project: dict[str, Any]) -> list[dict[str, Any]]:
    """Точки регулируемых опор/подпятников (AKD-178): [{x, z, y_top, panel}].

    Опоры ставятся под нижние опорные панели (дно — по углам с отступом 50,
    +2 в середине при ширине > 1200; панельные опоры столов — по 2 на опору).
    Пусто, если опор нет (цоколь-панель, металлокаркас, height=0)."""
    hw = project.get("hardware", {}) or {}
    legs = hw.get("legs") or {}
    lt = str(legs.get("type") or "").lower()
    lh = float(legs.get("height") or 0)
    construction = str((project.get("carcass_calculation") or {}).get("construction", ""))
    panels = [p for p in project.get("panels", []) if isinstance(p.get("placement"), dict)]
    if lh <= 0 or lt in ("", "нет", "-", "—") or construction == "top_on_metal_frame" \
            or any(p.get("type") == "plinth" for p in panels):
        return []
    out: list[dict[str, Any]] = []
    for p in panels:
        pl = p["placement"]
        if abs(pl["y1"] - lh) > 0.5:                  # панель не опирается на опоры
            continue
        if p.get("type") == "bottom":
            m = 50.0
            xs = [pl["x1"] + m, pl["x2"] - m]
            if pl["x2"] - pl["x1"] > 1200:
                xs.insert(1, (pl["x1"] + pl["x2"]) / 2)
            for x in xs:
                for z in (pl["z1"] + m, pl["z2"] - m):
                    out.append({"x": round(x, 1), "z": round(z, 1),
                                "y_top": pl["y1"], "panel": p["name"]})
        elif p.get("type") in _VERT:                  # панельные опоры стола до пола
            xc = (pl["x1"] + pl["x2"]) / 2
            for z in (pl["z1"] + 40, pl["z2"] - 40):
                out.append({"x": round(xc, 1), "z": round(z, 1),
                            "y_top": pl["y1"], "panel": p["name"]})
    return out


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
            else:  # door_front: ручка у кромки, ПРОТИВОПОЛОЖНОЙ петлям (AKD-223)
                sd = door_hinge_side(p, _model_width(panels))
                cy = (pl["y1"] + pl["y2"]) / 2
                if sd in ("up", "down"):              # откидная: ручка снизу/сверху по центру
                    cx = (pl["x1"] + pl["x2"]) / 2
                    hy = pl["y1"] + 40 if sd == "up" else pl["y2"] - 40
                    for dx in (-size / 2, size / 2):
                        holes.append(_hole(p["name"], "ручка (винт)", cx + dx, hy, z_in, 5, t_f, "z", -1))
                else:
                    hx = pl["x1"] + 40 if sd == "right" else pl["x2"] - 40
                    for dy in (-size / 2, size / 2):
                        holes.append(_hole(p["name"], "ручка (винт)", hx, cy + dy, z_in, 5, t_f, "z", -1))

    # --- Петли: чашка Ø35 на фасаде + 2 отв ответной планки. Сторона — по
    #     положению секции/параметру swing (AKD-223/224), не по имени ---
    _mw = _model_width(panels)
    for p in panels:
        if p.get("type") != "door_front":
            continue
        pl = p["placement"]
        sd = door_hinge_side(p, _mw)
        if sd in ("up", "down"):
            # откидная дверь: чашки вдоль верхней/нижней кромки, планки —
            # на пласти примыкающего горизонта (крышка/дно/полка)
            n = _n_hinges(pl["x2"] - pl["x1"])
            cup_y = pl["y2"] - 22 if sd == "up" else pl["y1"] + 22
            edge_y = pl["y2"] if sd == "up" else pl["y1"]
            host = None
            for q in panels:                          # горизонт за/над кромкой двери
                if q.get("type") not in ("top", "bottom", "shelf"):
                    continue
                qp = q["placement"]
                near = (qp["y1"] - 1 <= edge_y <= qp["y2"] + 1
                        or (sd == "up" and 0 <= qp["y1"] - edge_y <= 6)     # зазор фасада
                        or (sd == "down" and 0 <= edge_y - qp["y2"] <= 6))
                if near and min(qp["x2"], pl["x2"]) - max(qp["x1"], pl["x1"]) > 60:
                    host = q
                    break
            for x in _hinge_levels(pl["x1"], pl["x2"], n):
                holes.append(_hole(p["name"], "петля (чашка Ø35)", x, cup_y, pl["z2"], 35, 12, "z", -1))
                if host is not None:
                    hp = host["placement"]
                    hy = hp["y1"] if sd == "up" else hp["y2"]
                    hdir = 1 if sd == "up" else -1    # с пласти внутрь тела горизонта
                    z_pl = hp["z1"] + 37
                    for dx in (-16, 16):
                        holes.append(_hole(host["name"], "петля (планка)", x + dx, hy, z_pl, 5, 12, "y", hdir))
            continue
        hinge_left = sd == "left"
        h = pl["y2"] - pl["y1"]
        n = _n_hinges(h)
        cup_x = pl["x1"] + 22 if hinge_left else pl["x2"] - 22
        # ближайшая вертикаль со стороны петель
        side_x = pl["x1"] if hinge_left else pl["x2"]
        side = min(verticals, key=lambda v: abs(((v["placement"]["x1"] + v["placement"]["x2"]) / 2) - side_x), default=None)
        for y in hinge_levels_clear(pl["y1"], pl["y2"], n, _shelf_spans(panels, pl["x1"], pl["x2"])):
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
        # отступ от кромок — кратен 32 (присадочная сетка производства)
        m = 32.0 * max(1, min(3, round((pl["z2"] - pl["z1"]) * 0.25 / 32)))
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
                holes.append(_hole(v["name"], "полкодержатель", edge_x, yc, z, 8, 10, "x", into))

    # --- Стяжки Y-стыков: дно/крышка/столешница ↔ боковины/перегородки.
    #     Система 32 (AKD-202, стандарт производства): шкант Ø8×30 + минификс
    #     (канал Ø8 в торце боковины, шток Ø8 в пласти горизонта, чашка Ø15
    #     в пласти боковины на 32 от торца) — конфирматы Ø7 не используются,
    #     присадочный центр сверлит Ø8 по сетке 32. ---
    construction = str((project.get("carcass_calculation") or {}).get("construction", ""))
    for p in panels:
        if p.get("type") not in ("bottom", "top"):
            continue
        pl = p["placement"]
        for v in verticals:
            vp = v["placement"]
            if not (vp["x1"] >= pl["x1"] - 1 and vp["x2"] <= pl["x2"] + 1):
                continue
            if p["type"] == "bottom" and abs(vp["y1"] - pl["y2"]) < 1:
                t_face, t_dir = vp["y1"], 1     # торец боковины снизу
                b_face, b_dir = pl["y2"], -1    # пласть дна сверху
                cup_y = vp["y1"] + 32
            elif p["type"] == "top" and abs(vp["y2"] - pl["y1"]) < 1:
                t_face, t_dir = vp["y2"], -1    # торец боковины сверху
                b_face, b_dir = pl["y1"], 1     # пласть крышки снизу
                cup_y = vp["y2"] - 32
            else:
                continue
            xc = (vp["x1"] + vp["x2"]) / 2
            cz1, cz2 = max(pl["z1"], vp["z1"]), min(pl["z2"], vp["z2"])
            dws, cams = _sys32_pts(cz1, cz2)
            cup_x, cup_dir = ((vp["x1"], 1) if v.get("type") == "side_right"
                              else (vp["x2"], -1))    # чашка с внутренней пласти
            for z in dws:
                holes.append(_hole(v["name"], "шкант 8×30 (торец)", xc, t_face, z, 8, 20, "y", t_dir))
                holes.append(_hole(p["name"], "шкант 8×30 (пласть)", xc, b_face, z, 8, 12, "y", b_dir))
            for z in cams:
                holes.append(_hole(v["name"], "эксцентрик (канал Ø8)", xc, t_face, z, 8, 34, "y", t_dir))
                holes.append(_hole(p["name"], "эксцентрик (шток)", xc, b_face, z, 8, 11, "y", b_dir))
                holes.append(_hole(v["name"], "эксцентрик (чашка Ø15)", cup_x, cup_y, z, 15, 13, "x", cup_dir))

    # --- Стяжки X-стыков: торец царги/экрана/задника → пласть боковины.
    #     Система 32 (AKD-202): шкант Ø8 (торец панели ↔ пласть боковины) +
    #     минификс (канал Ø8 в торце панели, шток Ø8 в пласти боковины,
    #     чашка Ø15 в пласти панели на 32 от торца). ---
    x_joint_types = ("bottom", "top", "back", "screen", "plinth")
    for p in panels:
        if p.get("type") not in x_joint_types:
            continue
        if p.get("type") == "back" and float(p.get("thickness", 16)) <= 6:
            continue                                  # тонкий ДВП-задник — гвозди
        pl = p["placement"]
        for v in verticals:
            vp = v["placement"]
            if abs(pl["x1"] - vp["x2"]) < 1:          # панель справа от вертикали
                t_face, t_dir = pl["x1"], 1           # торец панели слева
                b_face, b_dir = vp["x2"], -1          # контактная пласть боковины
                cup_x = pl["x1"] + 32
            elif abs(pl["x2"] - vp["x1"]) < 1:        # панель слева от вертикали
                t_face, t_dir = pl["x2"], -1
                b_face, b_dir = vp["x1"], 1
                cup_x = pl["x2"] - 32
            else:
                continue
            cy1, cy2 = max(pl["y1"], vp["y1"]), min(pl["y2"], vp["y2"])
            cz1, cz2 = max(pl["z1"], vp["z1"]), min(pl["z2"], vp["z2"])
            # у задника в проём контакт по Z = его толщина (16 < 20) — порог
            # по короткой стороне снижаем, иначе задник остаётся без крепежа
            thin = min(20.0, float(p.get("thickness", 16)) - 2) if p["type"] == "back" else 20.0
            if cy2 - cy1 < min(20.0, thin) or cz2 - cz1 < thin:   # нет полноценного контакта
                continue
            # раскладка вдоль длинной стороны зоны контакта; чашка — в пласти
            # панели (для front-панелей нормаль Z: вход с задней грани)
            along_z = (cz2 - cz1) >= (cy2 - cy1)
            if along_z:
                lvl = (cy1 + cy2) / 2
                dws, cams = _sys32_pts(cz1, cz2)
            else:
                lvl = (cz1 + cz2) / 2
                dws, cams = _sys32_pts(cy1, cy2)
            for t in dws:
                y, z = (lvl, t) if along_z else (t, lvl)
                holes.append(_hole(p["name"], "шкант 8×30 (торец)", t_face, y, z, 8, 20, "x", t_dir))
                holes.append(_hole(v["name"], "шкант 8×30 (пласть)", b_face, y, z, 8, 12, "x", b_dir))
            for t in cams:
                y, z = (lvl, t) if along_z else (t, lvl)
                holes.append(_hole(p["name"], "эксцентрик (канал Ø8)", t_face, y, z, 8, 34, "x", t_dir))
                holes.append(_hole(v["name"], "эксцентрик (шток)", b_face, y, z, 8, 11, "x", b_dir))
                if along_z:                           # горизонталь: чашка с нижней пласти
                    holes.append(_hole(p["name"], "эксцентрик (чашка Ø15)", cup_x, pl["y1"], z, 15, 13, "y", 1))
                else:                                 # front-панель: чашка с задней пласти
                    holes.append(_hole(p["name"], "эксцентрик (чашка Ø15)", cup_x, y, pl["z2"], 15, 13, "z", -1))

    # --- Цоколь (AKD-180): боковины начинаются выше дна — единственный стык
    #     цоколя это его верхний торец под дном. Конфирматы сквозь дно вниз ---
    for p in panels:
        if p.get("type") != "plinth":
            continue
        pl = p["placement"]
        zc = (pl["z1"] + pl["z2"]) / 2
        for q in panels:
            if q.get("type") != "bottom":
                continue
            qp = q["placement"]
            if abs(pl["y2"] - qp["y1"]) > 1:
                continue
            x1o, x2o = max(pl["x1"], qp["x1"]), min(pl["x2"], qp["x2"])
            if x2o - x1o < 60 or not (qp["z1"] - 1 <= zc <= qp["z2"] + 1):
                continue
            # система 32: шкант + минификс (канал в торце цоколя, шток снизу дна,
            # чашка с задней пласти цоколя)
            dws, cams = _sys32_pts(x1o, x2o)
            for x in dws:
                holes.append(_hole(p["name"], "шкант 8×30 (торец)", x, pl["y2"], zc, 8, 20, "y", -1))
                holes.append(_hole(q["name"], "шкант 8×30 (пласть)", x, qp["y1"], zc, 8, 12, "y", 1))
            for x in cams:
                holes.append(_hole(p["name"], "эксцентрик (канал Ø8)", x, pl["y2"], zc, 8, 34, "y", -1))
                holes.append(_hole(q["name"], "эксцентрик (шток)", x, qp["y1"], zc, 8, 11, "y", 1))
                holes.append(_hole(p["name"], "эксцентрик (чашка Ø15)", x, pl["y2"] - 32, pl["z2"], 15, 13, "z", -1))

    # --- Толстый задник в проём (>6, ЛДСП): помимо стяжек через боковины
    #     (X-стыки выше) — шкант+минификс через дно/крышку в его торцы и
    #     саморезы сквозь пласть в торцы примыкающих перегородок/полок ---
    thick_backs = [p for p in panels if p.get("type") == "back"
                   and float(p.get("thickness", 16)) > 6]
    for b in thick_backs:
        bp = b["placement"]
        zc = (bp["z1"] + bp["z2"]) / 2
        for p in panels:
            if p.get("type") not in ("bottom", "top"):
                continue
            if p["type"] == "top" and construction == "top_on_supports":
                continue                           # видимую пласть столешницы не сверлим
            pl = p["placement"]
            if not (pl["x1"] - 1 <= bp["x1"] and bp["x2"] <= pl["x2"] + 1
                    and pl["z1"] - 1 <= zc <= pl["z2"] + 1):
                continue
            if p["type"] == "bottom" and abs(bp["y1"] - pl["y2"]) < 1:
                t_face, t_dir = bp["y1"], 1        # торец задника снизу
                b_face, b_dir = pl["y2"], -1       # пласть дна сверху
                cup_y = bp["y1"] + 32
            elif p["type"] == "top" and abs(bp["y2"] - pl["y1"]) < 1:
                t_face, t_dir = bp["y2"], -1
                b_face, b_dir = pl["y1"], 1
                cup_y = bp["y2"] - 32
            else:
                continue
            dws, cams = _sys32_pts(bp["x1"], bp["x2"])
            for x in dws:
                holes.append(_hole(b["name"], "шкант 8×30 (торец)", x, t_face, zc, 8, 20, "y", t_dir))
                holes.append(_hole(p["name"], "шкант 8×30 (пласть)", x, b_face, zc, 8, 12, "y", b_dir))
            for x in cams:
                holes.append(_hole(b["name"], "эксцентрик (канал Ø8)", x, t_face, zc, 8, 34, "y", t_dir))
                holes.append(_hole(p["name"], "эксцентрик (шток)", x, b_face, zc, 8, 11, "y", b_dir))
                holes.append(_hole(b["name"], "эксцентрик (чашка Ø15)", x, cup_y, bp["z2"], 15, 13, "z", -1))
        # перегородки/полки, упирающиеся торцом в пласть задника
        t_b = float(b.get("thickness", 16))
        for q in panels:
            qp = q.get("placement")
            if q.get("type") not in ("vertical_partition", "shelf") or not qp:
                continue
            if abs(qp["z2"] - bp["z1"]) > 1.5:
                continue
            if q["type"] == "vertical_partition":
                cx = (qp["x1"] + qp["x2"]) / 2
                y1o, y2o = max(qp["y1"], bp["y1"]), min(qp["y2"], bp["y2"])
                if not (bp["x1"] < cx < bp["x2"]) or y2o - y1o < 100:
                    continue
                pts = [(cx, y) for y in _spread(y1o + 40, y2o - 40, 300)]
            else:
                cy = (qp["y1"] + qp["y2"]) / 2
                x1o, x2o = max(qp["x1"], bp["x1"]), min(qp["x2"], bp["x2"])
                if not (bp["y1"] < cy < bp["y2"]) or x2o - x1o < 100:
                    continue
                pts = [(x, cy) for x in _spread(x1o + 40, x2o - 40, 300)]
            for x, y in pts:
                holes.append(_hole(b["name"], "задник (саморез)", x, y, bp["z2"],
                                   3.5, round(t_b + 14, 1), "z", -1))

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

    # --- Дно ПОД боковинами (box_bottom_mode=under): боковины стоят на дне —
    #     саморезы снизу дна вверх в нижние торцы боковин (AKD-181) ---
    for p in panels:
        if p.get("type") not in ("drawer_side_left", "drawer_side_right"):
            continue
        pl = p["placement"]
        for b in panels:
            if b.get("type") != "drawer_bottom":
                continue
            bp = b["placement"]
            if abs(pl["y1"] - bp["y2"]) > 1:
                continue
            if not (bp["x1"] - 1 <= pl["x1"] and pl["x2"] <= bp["x2"] + 1):
                continue
            zlo, zhi = max(pl["z1"], bp["z1"]), min(pl["z2"], bp["z2"])
            if zhi - zlo < 100:
                continue
            xc = (pl["x1"] + pl["x2"]) / 2
            for z in _spread(zlo + 60, zhi - 60, 300):
                holes.append(_hole(b["name"], "короб ящика (саморез)",
                                   xc, bp["y1"], z, 3.5, 30, "y", 1))

    # --- Задняя стенка ящика ЗА боковинами (накладная): саморезы сквозь стенку
    #     по −Z в торец дна и в задние торцы боковин ---
    seen_back: set[str] = set()
    for q in panels:
        if q.get("type") != "drawer_back" or q["name"] in seen_back:
            continue
        qp = q["placement"]
        sides_reach = any(p.get("type") in ("drawer_side_left", "drawer_side_right")
                          and p["placement"]["z2"] >= qp["z1"] + 1 for p in panels
                          if p.get("placement") and p["placement"]["y1"] < qp["y2"]
                          and p["placement"]["y2"] > qp["y1"])
        if sides_reach:
            continue                                   # покрыто сквозь-бок веткой
        bottom = next((b for b in panels if b.get("type") == "drawer_bottom"
                       and abs(b["placement"]["z2"] - qp["z1"]) < 1.5
                       and b["placement"]["x1"] < (qp["x1"] + qp["x2"]) / 2 < b["placement"]["x2"]
                       and qp["y1"] - 1 <= b["placement"]["y1"] <= qp["y2"] + 1), None)
        if bottom is not None:
            seen_back.add(q["name"])
            bp = bottom["placement"]
            yb = (bp["y1"] + bp["y2"]) / 2             # уровень торца дна
            for x in _spread(max(qp["x1"], bp["x1"]) + 40, min(qp["x2"], bp["x2"]) - 40, 300):
                holes.append(_hole(q["name"], "короб ящика (саморез)",
                                   x, yb, qp["z2"], 3.5, 30, "z", -1))
        # задние торцы боковин, перекрытые стенкой
        for p in panels:
            if p.get("type") not in ("drawer_side_left", "drawer_side_right"):
                continue
            pl = p["placement"]
            xc = (pl["x1"] + pl["x2"]) / 2
            if abs(pl["z2"] - qp["z1"]) > 1.5 or not (qp["x1"] < xc < qp["x2"]):
                continue
            y1o, y2o = max(pl["y1"], qp["y1"]), min(pl["y2"], qp["y2"])
            if y2o - y1o < 60:
                continue
            seen_back.add(q["name"])
            for y in (y1o + 25, y2o - 25):
                holes.append(_hole(q["name"], "короб ящика (саморез)",
                                   xc, y, qp["z2"], 3.5, 30, "z", -1))

    # --- Фасад ящика ↔ короб (AKD-181): фасадная стяжка — шток в заднюю пласть
    #     фасада + эксцентрик Ø15 с внутренней пласти боковины короба. Ручки
    #     (push-to-open) крепёж фасада не заменяют ---
    for f in panels:
        if f.get("type") != "drawer_front":
            continue
        fp = f["placement"]
        for p in panels:
            if p.get("type") not in ("drawer_side_left", "drawer_side_right"):
                continue
            pl = p["placement"]
            # короб этого же ящика: сразу за фасадом и внутри его Y-диапазона
            if not (fp["z2"] - 1 <= pl["z1"] <= fp["z2"] + 25):
                continue
            y1o, y2o = max(pl["y1"], fp["y1"]), min(pl["y2"], fp["y2"])
            xc = (pl["x1"] + pl["x2"]) / 2
            if y2o - y1o < 60 or not (fp["x1"] < xc < fp["x2"]):
                continue
            yc = (y1o + y2o) / 2
            holes.append(_hole(f["name"], "фасадная стяжка (шток)",
                               xc, yc, fp["z2"], 8, 11, "z", -1))
            z_cam = pl["z1"] + 20                      # чашка у переднего торца боковины
            if p["type"] == "drawer_side_left":
                sx, sdir = pl["x2"], -1                # вход с внутренней пласти
            else:
                sx, sdir = pl["x1"], 1
            holes.append(_hole(p["name"], "фасадная стяжка (эксцентрик Ø15)",
                               sx, yc, z_cam, 15, 13, "x", sdir))

    # --- Опоры/подпятники (AKD-178): по 2 самореза на опору вверх в панель ---
    for leg in leg_positions(project):
        for dx in (-12, 12):
            holes.append(_hole(leg["panel"], "опора (саморез)",
                               leg["x"] + dx, leg["y_top"], leg["z"], 3.5, 14, "y", 1))

    # --- Металлокаркас стола (AKD-178): саморезы подстолья снизу столешницы
    #     + отверстия крепления экрана к каркасу ---
    if construction == "top_on_metal_frame":
        for p in panels:
            if p.get("type") != "top":
                continue
            pl = p["placement"]
            for z in (pl["z1"] + 80, pl["z2"] - 80):
                for x in _spread(pl["x1"] + 100, pl["x2"] - 100, 500)[:4]:
                    holes.append(_hole(p["name"], "каркас (саморез)",
                                       x, pl["y1"], z, 5, 12, "y", 1))
        for p in panels:
            if p.get("type") != "screen":
                continue
            pl = p["placement"]
            for x in (pl["x1"] + 40, pl["x2"] - 40):
                for y in (pl["y1"] + 40, pl["y2"] - 40):
                    holes.append(_hole(p["name"], "каркас (саморез)",
                                       x, y, pl["z2"], 5, 10, "z", -1))

    # --- Штанга-вешало (AKD-177): саморезы штангодержателей. Поперечная (axis x)
    #     — по 2 винта в боковину/перегородку у каждого конца; продольная
    #     выдвижная (axis z) — 3 винта вверх в горизонт над ней ---
    for rod in hw.get("rods") or []:
        yc = (float(rod["y1"]) + float(rod["y2"])) / 2
        zc = (float(rod["z1"]) + float(rod["z2"])) / 2
        xc = (float(rod["x1"]) + float(rod["x2"])) / 2
        if rod.get("axis") == "z":
            host = min((p for p in panels
                        if p.get("type") in ("shelf", "top", "bottom")
                        and 5 <= p["placement"]["y1"] - float(rod["y2"]) <= 120
                        and p["placement"]["x1"] - 1 <= xc <= p["placement"]["x2"] + 1
                        and p["placement"]["z1"] - 1 <= zc <= p["placement"]["z2"] + 1),
                       key=lambda p: p["placement"]["y1"], default=None)
            if host is None:
                continue
            hp = host["placement"]
            z1, z2 = max(float(rod["z1"]), hp["z1"]), min(float(rod["z2"]), hp["z2"])
            for z in _spread(z1 + 25, z2 - 25, 200)[:3]:
                holes.append(_hole(host["name"], "штангодержатель (саморез)",
                                   xc, hp["y1"], z, 3.5, 12, "y", 1))
        else:
            for edge_x, want in ((float(rod["x1"]), "x2"), (float(rod["x2"]), "x1")):
                v = min((v for v in verticals if abs(v["placement"][want] - edge_x) < 1.0),
                        key=lambda v: abs(v["placement"][want] - edge_x), default=None)
                if v is None:
                    continue
                into = -1 if want == "x2" else 1
                for dy in (-14, 14):
                    holes.append(_hole(v["name"], "штангодержатель (саморез)",
                                       edge_x, yc + dy, zc, 3.5, 14, "x", into))

    # --- Замки (AKD-137/223): цилиндр Ø18 сквозь фасад, сторона ручки.
    #     locks.target (right_door/left_door/имя) — замок ТОЛЬКО на этой двери ---
    if hw.get("locks"):
        # цели из спеки: right_door/left_door/точное имя фасада; без target — все
        targets: list[str] = []
        for lk in (hw.get("locks") if isinstance(hw.get("locks"), list) else []):
            if isinstance(lk, dict) and lk.get("target"):
                targets.append(str(lk["target"]).lower())
        doors = [p for p in panels if p.get("type") == "door_front"]

        def _lock_matches(p: dict[str, Any]) -> bool:
            if not targets:
                return True
            nm = str(p.get("name", "")).lower()
            sdp = door_hinge_side(p, _mw)
            for t in targets:
                if t in ("right_door", "правая") and (sdp == "right" or "прав" in nm):
                    return True
                if t in ("left_door", "левая") and (sdp == "left" or "лев" in nm):
                    return True
                if t not in ("right_door", "left_door") and t in nm:
                    return True
            return False

        for p in doors:
            if not _lock_matches(p):
                continue
            pl = p["placement"]
            sdl = door_hinge_side(p, _mw)
            lx = pl["x1"] + 30 if sdl == "right" else pl["x2"] - 30
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
    "задник (гвоздь)": ("Гвоздь 1.6×25", "гвоздь 1,6", 1.0),
    "задник (саморез)": ("Саморез 3,5×30", "саморез потай 3,5 30", 1.0),
    "короб ящика (саморез)": ("Саморез 3,5×16", "саморез потай 3,5 16", 1.0),
    "направляющая (винт)": ("Саморез 3,5×16 (направляющие)", "саморез потай 3,5 16", 1.0),
    "ручка (винт)": ("Винт М4×16", "винт м4 16", 1.0),
    "полкодержатель": ("Полкодержатель", "полкодержатель", 1.0),
    "петля (чашка Ø35)": ("Петля накладная", "петля наклад", 1.0),
    "шкант 8×30 (торец)": ("Шкант 8×30", "шкант 8", 0.5),
    "шкант 8×30 (пласть)": ("Шкант 8×30", "шкант 8", 0.5),
    "эксцентрик (чашка Ø15)": ("Эксцентрик Ø15 + шток", "эксцентрик", 1.0),
    "эксцентрик (шток)": ("Эксцентрик Ø15 + шток", "эксцентрик", 0.0),
    "эксцентрик (канал Ø8)": ("Эксцентрик Ø15 + шток", "эксцентрик", 0.0),
    "фасадная стяжка (эксцентрик Ø15)": ("Стяжка фасадная (эксцентрик+шток)", "эксцентрик", 1.0),
    "фасадная стяжка (шток)": ("Стяжка фасадная (эксцентрик+шток)", "эксцентрик", 0.0),
    "замок (цилиндр Ø18)": ("Замок мебельный", "замок", 1.0),
    "штангодержатель (саморез)": ("Штангодержатель (комплект)", "штангодержатель", 0.5),
    "опора (саморез)": ("Саморез 3,5×16 (опоры)", "саморез потай 3,5 16", 1.0),
    "каркас (саморез)": ("Саморез 5×12 (каркас/экран)", "саморез 5 12", 1.0),
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
    return out


def drilling_summary(holes: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for h in holes:
        out[h["purpose"]] = out.get(h["purpose"], 0) + 1
    return out
