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


def _prod_pts(a: float, b: float) -> tuple[list[float], list[float]]:
    """Производственная раскладка (реверс эталона технолога,
    rules/b3d_production_reference.md): стяжка/конфирмат на 96 от каждого
    торца стыка, шкант — на 32 внутрь от неё (пары через 32, всё кратно 32).
    Узкие стыки — вырожденные варианты по центру."""
    L = b - a
    if L >= 232:
        cams = [round(a + 96, 1), round(b - 96, 1)]
        dws = [round(a + 128, 1), round(b - 128, 1)]
    elif L >= 128:
        cams = [round(a + 48, 1), round(b - 48, 1)]
        dws = [round((a + b) / 2, 1)]
    else:
        c = (a + b) / 2
        cams = [round(c + 16, 1)]
        dws = [round(c - 16, 1)]
    return dws, cams


def door_hinge_side(p: dict[str, Any], model_w: float | None = None,
                    siblings: int = 1) -> str:
    """Сторона петель фасада двери: 'left'|'right'|'up'|'down' (AKD-223/224).

    Приоритет: явный p['swing'] (sections[].door_swing) → имя («прав»/«лев»,
    только для ДВУСТВОРКИ siblings>=2 — там имена парные и осмысленные) →
    ПО ПОЛОЖЕНИЮ: дверь в правой половине изделия навешивается справа
    (открывание наружу). Для одиночной двери имя игнорируется: шаблонное
    «Дверь левая» в правой секции не должно вешать петли внутрь."""
    sw = str(p.get("swing") or "").lower()
    if sw in ("left", "right", "up", "down"):
        return sw
    nm = str(p.get("name", "")).lower()
    if siblings >= 2:
        if "прав" in nm:
            return "right"
        if "лев" in nm:
            return "left"
    pl = p.get("placement") or {}
    if model_w and pl:
        c = (pl.get("x1", 0) + pl.get("x2", 0)) / 2
        return "right" if c > model_w / 2 + 1 else "left"
    # позиция неизвестна — падаем на имя, потом на left
    if "прав" in nm:
        return "right"
    return "left"


def _door_siblings(p: dict[str, Any], panels: list[dict[str, Any]]) -> int:
    """Число дверей той же секции (для приоритета имени в двустворках)."""
    sid = p.get("section_id")
    return sum(1 for q in panels if q.get("type") == "door_front"
               and (q.get("section_id") == sid if sid else True))


def _model_width(panels: list[dict[str, Any]]) -> float:
    return max((p["placement"]["x2"] for p in panels
                if isinstance(p.get("placement"), dict)), default=0.0)


def _n_hinges(h: float) -> int:
    # реверс эталона: дверь 2196 у технолога несёт 4 петли (не 5)
    if h <= 900:
        return 2
    if h <= 1600:
        return 3
    if h <= 2400:
        return 4
    return 5


def _hinge_levels(y1: float, y2: float, n: int, margin: float = 100.0,
                  prod: bool = False) -> list[float]:
    # prod (вертикальная дверь, реверс эталона): нижняя петля 160 от низа,
    # верхняя 100 от верха, промежуточные равномерно; откидные — симметрично
    lo, hi = (y1 + 160.0, y2 - 100.0) if prod else (y1 + margin, y2 - margin)
    if hi <= lo:                                   # низкая дверь — по центру зоны
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
    for y in _hinge_levels(y1, y2, n, margin, prod=True):
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


def _compute_drilling(project: dict[str, Any]) -> list[dict[str, Any]]:
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
                sd = door_hinge_side(p, _model_width(panels), _door_siblings(p, panels))
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
        sd = door_hinge_side(p, _mw, _door_siblings(p, panels))
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
        # реверс эталона (DTC Pivot-Star): центр чашки 22.5 от кромки двери,
        # наколы саморезов чашки Ø3×2 на 28.5 от кромки, ±24 по вертикали;
        # ответная планка — 2 накола Ø3×2 (не Ø5) на 37 вглубь от фронта, ±16
        cup_x = pl["x1"] + 22.5 if hinge_left else pl["x2"] - 22.5
        pin_x = pl["x1"] + 28.5 if hinge_left else pl["x2"] - 28.5
        # ближайшая вертикаль со стороны петель. Кандидат обязан пересекаться
        # с дверью по Y (AKD-235: в composite с origin.y дверь ВЕРХНЕГО шкафа
        # прибивалась планками к боковине НИЖНЕЙ тумбы на том же X)
        side_x = pl["x1"] if hinge_left else pl["x2"]
        cands = [v for v in verticals
                 if min(v["placement"]["y2"], pl["y2"])
                 - max(v["placement"]["y1"], pl["y1"]) >= 40]
        side = min(cands, key=lambda v: abs(((v["placement"]["x1"] + v["placement"]["x2"]) / 2) - side_x), default=None)
        for y in hinge_levels_clear(pl["y1"], pl["y2"], n, _shelf_spans(panels, pl["x1"], pl["x2"])):
            # чашка сверлится с ВНУТРЕННЕЙ (задней) грани двери, глухая 12 мм
            holes.append(_hole(p["name"], "петля (чашка Ø35)", cup_x, y, pl["z2"], 35, 12, "z", -1))
            for dy in (-24, 24):                      # наколы саморезов чашки
                holes.append(_hole(p["name"], "петля (накол чашки)", pin_x, y + dy, pl["z2"], 3, 2, "z", -1))
            if side is not None:
                sp = side["placement"]
                sx = sp["x2"] if hinge_left else sp["x1"]
                sdir = -1 if hinge_left else 1
                z_pl = sp["z1"] + 37                  # планка на 37 от переднего края боковины
                for dy in (-16, 16):
                    holes.append(_hole(side["name"], "петля (планка)", sx, y + dy, z_pl, 3, 2, "x", sdir))

    # --- Полки (реверс эталона, AKD-287).
    #     Съёмные: эксцентрик Ø20 в пластиковом корпусе (SE01PB) — чашка
    #     Ø20×13 в НИЖНЕЙ пласти полки на 10 от торца, шток Ø5×12 в пласть
    #     стойки; по 2 на сторону, 64 от переднего/заднего торца полки.
    #     Стационарные (fixed=True, перекрытие стека): стяжка MNFX — канал
    #     Ø8×34 в торец полки, чашка Ø15×12.5 в нижней пласти полки на 35 от
    #     торца, шток Ø5×12 в стойку; позиции 96 от торцов + шкант на 32. ---
    for p in panels:
        if p.get("type") != "shelf":
            continue
        pl = p["placement"]
        yc = (pl["y1"] + pl["y2"]) / 2
        fixed = bool(p.get("fixed"))
        for edge_x, want in ((pl["x1"], "x2"), (pl["x2"], "x1")):
            # боковина должна содержать уровень полки по Y (AKD-235: composite
            # с origin.y — полка верхнего блока сверлилась в нижнюю боковину)
            v = min((v for v in verticals if abs(v["placement"][want] - edge_x) < 1.0
                     and v["placement"]["y1"] <= yc <= v["placement"]["y2"]),
                    key=lambda v: abs(v["placement"][want] - edge_x), default=None)
            if v is None:
                continue
            # шток сверлится В ТЕЛО стойки: у левой (её x2 = грань полки) — в −X
            into = -1 if want == "x2" else 1
            cup_x = edge_x + (10 if into < 0 else -10)     # чашка в теле полки
            if not fixed:
                for z in (pl["z1"] + 64, pl["z2"] - 64):
                    holes.append(_hole(p["name"], "эксцентрик полки (чашка Ø20)",
                                       cup_x, pl["y1"], z, 20, 13, "y", 1))
                    holes.append(_hole(v["name"], "эксцентрик полки (шток)",
                                       edge_x, yc, z, 5, 12, "x", into))
            else:
                mcup_x = edge_x + (35 if into < 0 else -35)
                dws, cams = _prod_pts(pl["z1"], pl["z2"])
                for z in cams:
                    holes.append(_hole(p["name"], "эксцентрик (канал Ø8)",
                                       edge_x, yc, z, 8, 34, "x", -into))
                    holes.append(_hole(p["name"], "эксцентрик (чашка Ø15)",
                                       mcup_x, pl["y1"], z, 15, 12.5, "y", 1))
                    holes.append(_hole(v["name"], "эксцентрик (шток)",
                                       edge_x, yc, z, 5, 12, "x", into))
                for z in dws:
                    holes.append(_hole(p["name"], "шкант 8×30 (торец)",
                                       edge_x, yc, z, 8, 21, "x", -into))
                    holes.append(_hole(v["name"], "шкант 8×30 (пласть)",
                                       edge_x, yc, z, 8, 11, "x", into))

    # --- Y-стыки: дно/крышка ↔ боковины/перегородки (реверс эталона AKD-287).
    #     ДНО: евровинт-конфирмат 7×50 СНИЗУ (голова в нижней пласти дна, не
    #     видна): проход Ø8 сквозь дно + тело Ø5×35 в торец стойки. КРЫШКА:
    #     стяжка MNFX — канал Ø8×34 вниз в торец стойки, чашка Ø15×12.5 во
    #     ВНУТРЕННЕЙ пласти стойки на 35 ниже стыка, шток Ø5×12 вверх в крышку.
    #     Позиции: 96 от торцов + шкант 8×30 на 32 внутрь (гнездо 21 в стойку,
    #     11 в пласть горизонта). ---
    construction = str((project.get("carcass_calculation") or {}).get("construction", ""))
    for p in panels:
        if p.get("type") not in ("bottom", "top"):
            continue
        pl = p["placement"]
        t_h = float(p.get("thickness") or (pl["y2"] - pl["y1"]))
        for v in verticals:
            vp = v["placement"]
            if not (vp["x1"] >= pl["x1"] - 1 and vp["x2"] <= pl["x2"] + 1):
                continue
            xc = (vp["x1"] + vp["x2"]) / 2
            cz1, cz2 = max(pl["z1"], vp["z1"]), min(pl["z2"], vp["z2"])
            dws, cams = _prod_pts(cz1, cz2)
            cup_x, cup_dir = ((vp["x1"], 1) if v.get("type") == "side_right"
                              else (vp["x2"], -1))    # чашка с внутренней пласти
            if p["type"] == "bottom" and abs(vp["y1"] - pl["y2"]) < 1:
                joint = pl["y2"]                       # плоскость стыка (верх дна)
                for z in cams:                         # конфирмат снизу дна
                    holes.append(_hole(p["name"], "евровинт (проход Ø8)", xc, pl["y1"], z, 8, t_h, "y", 1))
                    holes.append(_hole(v["name"], "конфирмат", xc, joint, z, 5, 35, "y", 1))
                for z in dws:                          # шкант: гнездо в стойке
                    holes.append(_hole(v["name"], "шкант 8×30 (торец)", xc, joint, z, 8, 21, "y", 1))
                    holes.append(_hole(p["name"], "шкант 8×30 (пласть)", xc, joint, z, 8, 11, "y", -1))
            elif p["type"] == "top" and abs(vp["y2"] - pl["y1"]) < 1:
                joint = pl["y1"]                       # плоскость стыка (низ крышки)
                for z in cams:                         # стяжка MNFX
                    holes.append(_hole(v["name"], "эксцентрик (канал Ø8)", xc, joint, z, 8, 34, "y", -1))
                    holes.append(_hole(v["name"], "эксцентрик (чашка Ø15)", cup_x, joint - 35, z, 15, 12.5, "x", cup_dir))
                    holes.append(_hole(p["name"], "эксцентрик (шток)", xc, joint, z, 5, 12, "y", 1))
                for z in dws:
                    holes.append(_hole(v["name"], "шкант 8×30 (торец)", xc, joint, z, 8, 21, "y", -1))
                    holes.append(_hole(p["name"], "шкант 8×30 (пласть)", xc, joint, z, 8, 11, "y", 1))

    # --- Стяжки X-стыков: торец царги/экрана/задника → пласть боковины.
    #     Реверс эталона (AKD-287): стяжка MNFX — канал Ø8×34 в торец панели,
    #     чашка Ø15×12.5 в пласти панели на 35 от торца, шток Ø5×12 в стойку;
    #     позиции 96 от торцов + шкант 8×30 на 32 внутрь (21/11). ---
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
                cup_x = pl["x1"] + 35
            elif abs(pl["x2"] - vp["x1"]) < 1:        # панель слева от вертикали
                t_face, t_dir = pl["x2"], -1
                b_face, b_dir = vp["x1"], 1
                cup_x = pl["x2"] - 35
            else:
                continue
            cy1, cy2 = max(pl["y1"], vp["y1"]), min(pl["y2"], vp["y2"])
            cz1, cz2 = max(pl["z1"], vp["z1"]), min(pl["z2"], vp["z2"])
            # у задника в проём (и крышки/дна между боковинами, sides_over_top)
            # контакт = толщина панели (16 < 20) — порог по короткой стороне
            # снижаем, иначе панель остаётся без крепежа
            thin = (min(20.0, float(p.get("thickness", 16)) - 2)
                    if p["type"] in ("back", "top", "bottom") else 20.0)
            if cy2 - cy1 < min(20.0, thin) or cz2 - cz1 < thin:   # нет полноценного контакта
                continue
            # раскладка вдоль длинной стороны зоны контакта; чашка — в пласти
            # панели (для front-панелей нормаль Z: вход с задней грани)
            along_z = (cz2 - cz1) >= (cy2 - cy1)
            if along_z:
                lvl = (cy1 + cy2) / 2
                dws, cams = _prod_pts(cz1, cz2)
            else:
                lvl = (cz1 + cz2) / 2
                dws, cams = _prod_pts(cy1, cy2)
            for t in dws:
                y, z = (lvl, t) if along_z else (t, lvl)
                holes.append(_hole(p["name"], "шкант 8×30 (торец)", t_face, y, z, 8, 21, "x", t_dir))
                holes.append(_hole(v["name"], "шкант 8×30 (пласть)", b_face, y, z, 8, 11, "x", b_dir))
            for t in cams:
                y, z = (lvl, t) if along_z else (t, lvl)
                holes.append(_hole(p["name"], "эксцентрик (канал Ø8)", t_face, y, z, 8, 34, "x", t_dir))
                holes.append(_hole(v["name"], "эксцентрик (шток)", b_face, y, z, 5, 12, "x", b_dir))
                if along_z:                           # горизонталь: чашка с нижней пласти
                    holes.append(_hole(p["name"], "эксцентрик (чашка Ø15)", cup_x, pl["y1"], z, 15, 12.5, "y", 1))
                else:                                 # front-панель: чашка с задней пласти
                    holes.append(_hole(p["name"], "эксцентрик (чашка Ø15)", cup_x, y, pl["z2"], 15, 12.5, "z", -1))

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
            dws, cams = _prod_pts(x1o, x2o)
            for x in dws:
                holes.append(_hole(p["name"], "шкант 8×30 (торец)", x, pl["y2"], zc, 8, 21, "y", -1))
                holes.append(_hole(q["name"], "шкант 8×30 (пласть)", x, qp["y1"], zc, 8, 11, "y", 1))
            for x in cams:
                holes.append(_hole(p["name"], "эксцентрик (канал Ø8)", x, pl["y2"], zc, 8, 34, "y", -1))
                holes.append(_hole(q["name"], "эксцентрик (шток)", x, qp["y1"], zc, 5, 12, "y", 1))
                holes.append(_hole(p["name"], "эксцентрик (чашка Ø15)", x, pl["y2"] - 35, pl["z2"], 15, 12.5, "z", -1))

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
                cup_y = bp["y1"] + 35
            elif p["type"] == "top" and abs(bp["y2"] - pl["y1"]) < 1:
                t_face, t_dir = bp["y2"], -1
                b_face, b_dir = pl["y1"], 1
                cup_y = bp["y2"] - 35
            else:
                continue
            dws, cams = _prod_pts(bp["x1"], bp["x2"])
            for x in dws:
                holes.append(_hole(b["name"], "шкант 8×30 (торец)", x, t_face, zc, 8, 21, "y", t_dir))
                holes.append(_hole(p["name"], "шкант 8×30 (пласть)", x, b_face, zc, 8, 11, "y", b_dir))
            for x in cams:
                holes.append(_hole(b["name"], "эксцентрик (канал Ø8)", x, t_face, zc, 8, 34, "y", t_dir))
                holes.append(_hole(p["name"], "эксцентрик (шток)", x, b_face, zc, 5, 12, "y", b_dir))
                holes.append(_hole(b["name"], "эксцентрик (чашка Ø15)", x, cup_y, bp["z2"], 15, 12.5, "z", -1))
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

    # --- Гвозди задника (реверс эталона AKD-287): гвоздь 1×16 бьётся в ЦЕНТРЫ
    #     торцов примыкающих панелей с шагом 96; прокол Ø3 сквозь ДВП/ХДФ +
    #     тело Ø1×12 в торец. Стойки — ряд по Y (центрированная сетка 96),
    #     дно/крышка/полки — ряд по X от 32 от края (хвост подтянут). ---
    def _row96_centered(a: float, b: float) -> list[float]:
        L = b - a
        if L < 64:
            return [(a + b) / 2]
        n = max(2, int(L // 96) + 1)
        off = (L - (n - 1) * 96) / 2
        while off < 32 and n > 2:                     # не ближе 32 к краю
            n -= 1
            off = (L - (n - 1) * 96) / 2
        return [round(a + off + 96 * k, 1) for k in range(n)]

    def _row96_edge(a: float, b: float) -> list[float]:
        pts = []
        x = a + 32
        while x <= b - 32 + 0.1:
            pts.append(round(x, 1))
            x += 96
        if pts and (b - 32) - pts[-1] > 64:           # хвост: добить у края
            pts.append(round(b - 48, 1))
        return pts or [(a + b) / 2]

    for p in panels:
        if p.get("type") != "back" or float(p.get("thickness", 16)) > 6:
            continue                                  # только тонкий ДВП/ХДФ-задник
        pl = p["placement"]
        t_b = float(p.get("thickness") or (pl["z2"] - pl["z1"]))
        # примыкающие сзади панели: их задний торец касается тела задника
        for q in panels:
            qp = q.get("placement")
            if not qp or q is p or q.get("type") == "back":
                continue
            if not (pl["z1"] - 1.5 <= qp["z2"] <= pl["z2"] + 1.5):
                continue                              # торец не под задником
            vert = q.get("type") in ("side_left", "side_right", "vertical_partition")
            if vert:
                cx = (qp["x1"] + qp["x2"]) / 2
                y1o, y2o = max(qp["y1"], pl["y1"]), min(qp["y2"], pl["y2"])
                if not (pl["x1"] - 1 <= cx <= pl["x2"] + 1) or y2o - y1o < 60:
                    continue
                pts = [(cx, y) for y in _row96_centered(y1o, y2o)]
            elif q.get("type") in ("bottom", "top", "shelf"):
                cy = (qp["y1"] + qp["y2"]) / 2
                x1o, x2o = max(qp["x1"], pl["x1"]), min(qp["x2"], pl["x2"])
                if not (pl["y1"] - 1 <= cy <= pl["y2"] + 1) or x2o - x1o < 60:
                    continue
                pts = [(x, cy) for x in _row96_edge(x1o, x2o)]
            else:
                continue
            for x, y in pts:
                holes.append(_hole(p["name"], "задник (прокол Ø3)", x, y, pl["z2"], 3, t_b, "z", -1))
                holes.append(_hole(q["name"], "задник (гвоздь)", x, y, qp["z2"], 1, 12, "z", -1))

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
            sdp = door_hinge_side(p, _mw, _door_siblings(p, panels))
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
            sdl = door_hinge_side(p, _mw, _door_siblings(p, panels))
            lx = pl["x1"] + 30 if sdl == "right" else pl["x2"] - 30
            ly = (pl["y1"] + pl["y2"]) / 2
            holes.append(_hole(p["name"], "замок (цилиндр Ø18)", lx, ly, pl["z2"],
                               18, pl["z2"] - pl["z1"], "z", -1))
        fronts = [p for p in panels if p.get("type") == "drawer_front"]
        if fronts:
            # явный target на фасад ящика («Фасад ящик 3», центральный замок
            # стека) работает и при наличии дверей; без targets — старое
            # поведение: верхний ящик, только если дверей нет
            d_targets = [t for t in targets
                         if t not in ("right_door", "left_door", "правая", "левая")]
            tgt = next((p for p in fronts
                        if any(t in str(p.get("name", "")).lower() for t in d_targets)),
                       None) if d_targets else None
            if tgt is None and d_targets:
                # generic-цель («drawers», «центральный») → верхний фасад стека
                tgt = max(fronts, key=lambda q: q["placement"]["y2"])
            if tgt is None and not doors and not targets:
                tgt = max(fronts, key=lambda q: q["placement"]["y2"])
            if tgt is not None:
                pl = tgt["placement"]
                holes.append(_hole(tgt["name"], "замок (цилиндр Ø18)",
                                   (pl["x1"] + pl["x2"]) / 2, pl["y2"] - 30, pl["z2"],
                                   18, pl["z2"] - pl["z1"], "z", -1))

    # --- Направляющие ящиков (реверс эталона AKD-287): каждый корпусный полоз
    #     крепится 3 саморезами US3.5×16 в ШТАТНЫЕ монтажные отверстия
    #     направляющей — 37, 101 и 389 мм от её переднего конца (полоз
    #     заподлицо с фронтом корпуса); высота осей = низ короба + 16.
    #     Присадка — накол Ø3×2 (саморез вкручивается по месту). ---
    for d in project.get("drawers", []):
        pos, dim = d.get("position") or {}, d.get("dimensions") or {}
        if not pos or not dim:
            continue
        box_l, box_r = float(pos["x"]), float(pos["x"]) + float(dim["width"])
        guide_y = float(pos["y"]) + 16
        left = min((v for v in verticals if v["placement"]["x2"] <= box_l + 1),
                   key=lambda v: box_l - v["placement"]["x2"], default=None)
        right = min((v for v in verticals if v["placement"]["x1"] >= box_r - 1),
                    key=lambda v: v["placement"]["x1"] - box_r, default=None)
        # накол входит с ВНУТРЕННЕЙ пласти опоры и сверлится В её тело:
        # левая опора — вход с её x2, сверло в −X; правая — с x1, в +X
        for v, inx in ((left, -1), (right, 1)):
            if v is None:
                continue
            vp = v["placement"]
            sx = vp["x2"] if inx < 0 else vp["x1"]
            front = vp["z1"]                          # наш фронт — min Z
            depth_av = vp["z2"] - vp["z1"]
            for off in (37.0, 101.0, 389.0):
                if off > depth_av - 10:               # мелкий корпус — точка не влезает
                    continue
                holes.append(_hole(v["name"], "направляющая (саморез)", sx, guide_y,
                                   front + off, 3, 2, "x", inx))
        # ящичный полоз (реверс эталона): 3 накола на боковину КОРОБА в его
        # монтажные точки 37/192/224 от переднего торца, вход с наружной пласти
        box_front = float(pos["z"])
        box_depth = float(dim["depth"])
        for q in panels:
            if q.get("type") not in ("drawer_side_left", "drawer_side_right"):
                continue
            qp = q["placement"]
            if not (qp["y1"] - 1 <= guide_y <= qp["y2"] + 1):
                continue
            if not (box_l - 20 <= qp["x1"] <= box_r + 20):    # боковина этого короба
                continue
            outer = qp["x1"] if q["type"] == "drawer_side_left" else qp["x2"]
            b_inx = 1 if q["type"] == "drawer_side_left" else -1
            for off in (37.0, 192.0, 224.0):
                if off > box_depth - 10:
                    continue
                holes.append(_hole(q["name"], "направляющая (саморез)", outer, guide_y,
                                   box_front + off, 3, 2, "x", b_inx))
    return holes


def compute_drilling(project: dict[str, Any]) -> list[dict[str, Any]]:
    """Compute deterministic drilling and expose only aggregate trace metadata."""

    from .telemetry import hash_payload, span

    with span("drilling.compute", {
        "project.hash": hash_payload({
            "project_name": project.get("project_name"),
            "panels": [panel.get("name") for panel in (project.get("panels") or [])],
        }),
        "panel.count": len(project.get("panels") or []),
    }) as trace_span:
        holes = _compute_drilling(project)
        trace_span.set_attributes({"hole.count": len(holes), "check.outcome": "pass"})
        return holes


# Присадка → позиция крепежа (имя для BOM, запрос в базу, шт на отверстие).
# У шканта 2 отверстия (торец+пласть) на 1 шкант; у эксцентрика чашка = 1 шт,
# отверстие штока — в комплекте (0).
_FASTENER_MAP = {
    "задник (гвоздь)": ("Гвоздь 1×16", "гвоздь 1х16", 1.0),
    "задник (прокол Ø3)": ("Гвоздь 1×16", "гвоздь 1х16", 0.0),   # прокол того же гвоздя
    "задник (саморез)": ("Саморез 3,5×30", "саморез потай 3,5 30", 1.0),
    "короб ящика (саморез)": ("Саморез 3,5×16", "саморез потай 3,5 16", 1.0),
    "направляющая (саморез)": ("Саморез US3,5×16 (направляющие)", "саморез потай 3,5 16", 1.0),
    "ручка (винт)": ("Винт М4×16", "винт м4 16", 1.0),
    "эксцентрик полки (чашка Ø20)": ("Эксцентрик Ø20 в пласт. корпусе (SE01PB)",
                                     "эксцентрик усиленный пластиковом корпусе", 1.0),
    "эксцентрик полки (шток)": ("Эксцентрик Ø20 в пласт. корпусе (SE01PB)",
                                "эксцентрик усиленный пластиковом корпусе", 0.0),
    "петля (чашка Ø35)": ("Петля накладная", "петля наклад", 1.0),
    "петля (накол чашки)": ("Петля накладная", "петля наклад", 0.0),
    "евровинт (проход Ø8)": ("Евровинт конфирмат 7×50", "конфирмат 7,0х50", 1.0),
    "конфирмат": ("Евровинт конфирмат 7×50", "конфирмат 7,0х50", 0.0),  # тело того же винта
    "шкант 8×30 (торец)": ("Шкант 8×30", "шкант 8", 0.5),
    "шкант 8×30 (пласть)": ("Шкант 8×30", "шкант 8", 0.5),
    "эксцентрик (чашка Ø15)": ("Стяжка эксцентриковая MNFX (комплект)", "стяжка эксцентриковая комплект", 1.0),
    "эксцентрик (шток)": ("Стяжка эксцентриковая MNFX (комплект)", "стяжка эксцентриковая комплект", 0.0),
    "эксцентрик (канал Ø8)": ("Стяжка эксцентриковая MNFX (комплект)", "стяжка эксцентриковая комплект", 0.0),
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
