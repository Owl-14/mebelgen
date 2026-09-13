"""Генератор cabinet: многоколоночный корпус (перегородки + содержимое колонок).

Колонки слева направо; в каждой — shelves | drawers | door | open. Покрывает
шкафы/тумбы/стеллажи/комоды с несколькими секциями. Через него же работает wardrobe.
"""

from __future__ import annotations

from typing import Any

from .base import read_carcass
from .corpus import carcass_calc
from .columns import column_bounds, door_in_column, drawer_stack, facade_x_span, partitions, rod_in_column, shelves_in_column
from .helpers import build_project, carcass, facade_band, panel, shelf_levels


def generate(spec: dict[str, Any]) -> dict[str, Any]:
    c = read_carcass(spec)
    sections = spec.get("sections") or [{"kind": "open"}]
    g = c.gap
    # зазор между фасадами ящиков по вертикали — gaps.default (эталон технолога:
    # 4 мм при боковом/дверном 2 мм); без него — как у фасадов
    gd = float((spec.get("gaps") or {}).get("default", g))
    yb, yt = c.Hleg + c.T, c.H - c.T_top
    # фронт полок/перегородок = фронт корпуса (дно/крышка), а не утоплен на T:
    # иначе полки посередине не доходят до переднего края изделия
    iz1 = spec.get("interior_z_front", spec.get("carcass_z_front", 0))
    iz2 = c.D - c.T_back
    # накладные фасады: полоса по высоте (перекрывает дно и крышку) и зазоры
    reveal = spec.get("facade_reveal", 2.0)
    fb_bottom, fb_top = facade_band(c.Hleg, c.H, c.T, has_overhang=bool(spec.get("top_overhang")),
                                    reveal=reveal)

    top_z = spec.get("top_overhang")
    panels = carcass(c.W, c.D, c.H, c.T, c.T_back, c.Hleg, c.mat, c.mat_back,
                     leg_as_panel=c.leg_as_panel, leg_type=c.leg_type,
                     z_front=spec.get("carcass_z_front", 0),
                     top_z=tuple(top_z) if top_z else None,
                     socle_full=spec.get("socle_full", False),
                     socle_recess=spec.get("socle_recess", 50),
                     sides_over_top=spec.get("sides_over_top", False),
                     t_top=c.T_top)
    bounds = column_bounds(c.W, c.T, sections)
    # перегородки — конструктив: всегда до фронта корпуса (AKD-192);
    # interior_z_front утапливает только наполнение (полки)
    panels += partitions(bounds, c.H, c.T, c.Hleg, c.mat,
                         spec.get("carcass_z_front", 0), iz2)

    drawers_meta: list[dict[str, Any]] = []
    sections_meta: list[dict[str, Any]] = []
    rods_meta: list[dict[str, Any]] = []

    for idx, (sec, (cx1, cx2)) in enumerate(zip(sections, bounds), start=1):
        sid = sec.get("id", f"col{idx}")
        kind = sec["kind"]
        names: list[str] = []
        levels = sec.get("shelf_levels")
        if levels is None and sec.get("shelves"):
            levels = shelf_levels(yb, yt, sec["shelves"], c.T)
        levels = levels or []

        fspan = facade_x_span(idx - 1, bounds, c.W, c.T, reveal, g)   # внешний пролёт фасада секции

        if kind == "drawers":
            _fb = sec.get("front_bottom")                       # Y-координата низа нижнего фасада
            fb = _fb if isinstance(_fb, (int, float)) and not isinstance(_fb, bool) else fb_bottom
            heights = sec.get("drawer_heights")
            n = sec["drawers"]
            if heights:
                # контракт (AKD-259): в спеке СВЕРХУ ВНИЗ, стек строится снизу
                heights = [float(h) for h in heights][::-1]
            else:
                top = sec.get("front_top", fb_top)
                h = (top - fb - (n - 1) * gd) / n
                heights = [round(h, 2)] * n
            # короб ящика не должен доходить до задника (передний край = D − T_back)
            sec_dr = {**sec, "back_limit": c.D - c.T_back}
            ps, dm, topy = drawer_stack(cx1, cx2, fb, heights, gd, sec_dr, c.T, c.mat, sid,
                                        sec.get("prefix", ""), facade_bounds=fspan)
            # нижний фасад перекрывает торец дна (как дверь): если фасад
            # начинается ровно с верха дна, открытый угол дна — брак
            bot_f = min((q for q in ps if q.get("type") == "drawer_front"),
                        key=lambda q: q["placement"]["y1"], default=None)
            if bot_f is not None and abs(bot_f["placement"]["y1"] - yb) <= 2 \
                    and fb_bottom < bot_f["placement"]["y1"]:
                dy = round(bot_f["placement"]["y1"] - fb_bottom, 2)
                bot_f["placement"]["y1"] = fb_bottom
                bot_f["position"]["y"] = fb_bottom
                bot_f["dimensions"]["height"] = round(bot_f["dimensions"]["height"] + dy, 2)
            panels += ps
            drawers_meta += dm
            names += [p["name"] for p in ps]
            # перекрытие стека (AKD-187): если ящики не доходят до крышки,
            # верхний ящик открыт сверху — полка над стеком строится всегда
            # (cover_top: false — отключить явно)
            if sec.get("cover_top", True) and topy + c.T <= yt - 40:
                # Полка садится в зону фасадов: её верх на зазор g выше верха
                # стека, верхний фасад заканчивается на g ниже верха полки и
                # перекрывает её торец (эталон технолога: фасады равные, полка
                # 680..696 при верхе фасада 694). Если короб верхнего ящика
                # мешает, полка поднимается до его верха, фасад — за ней.
                box_top = max((q["placement"]["y2"] for q in ps
                               if q.get("type") in ("drawer_side_left", "drawer_side_right",
                                                    "drawer_back")), default=topy)
                sy1 = round(max(topy + g - c.T, box_top), 2)
                sh = panel("Полка под нишей", "shelf", "horizont", (cx1, cx2), (sy1, sy1 + c.T),
                           (sec.get("niche_z_front", 0), iz2), thickness=c.T, material=c.mat,
                           section_id=sid, estimated=True)
                sh["fixed"] = True      # стационарная: стяжки, не съёмные эксцентрики (AKD-287)
                panels.append(sh)
                names.append(sh["name"])
                top_f = max((q for q in ps if q.get("type") == "drawer_front"),
                            key=lambda q: q["placement"]["y2"], default=None)
                if top_f is not None:
                    top_f["placement"]["y2"] = round(sy1 + c.T - g, 2)
                    top_f["dimensions"]["height"] = round(
                        top_f["placement"]["y2"] - top_f["placement"]["y1"], 2)
        else:
            if levels:
                base_label = sec.get("shelf_label", "Полка")
                # при >1 колонке имена полок уникализируем по секции (иначе дубли имён)
                lbl = f"{base_label} ({sid})" if len(sections) > 1 else base_label
                sp = shelves_in_column(cx1, cx2, levels, c.T, iz1, iz2, c.mat, sid, lbl)
                panels += sp
                names += [p["name"] for p in sp]
            nd = sec.get("door", 0)
            if nd:
                z_mode = sec.get("door_z", "overlay")
                # накладная дверь: перекрывает дно/крышку по высоте и корпус по ширине
                inset = z_mode == "inset"
                dy1 = (yb + g) if inset else fb_bottom
                dy2 = (min(levels) - g) if (levels and sec.get("door_below_shelf")) else (
                    (yt - g) if inset else fb_top)
                fx1, fx2 = (cx1 + g, cx2 - g) if inset else fspan
                # имена дверей: door_names/door_name из ТЗ, иначе дефолт с id секции (уникально)
                dn = sec.get("door_names") or sec.get("door_name")
                dn = dn if isinstance(dn, list) else None
                if nd == 2:
                    names = dn or [f"Дверь левая {sid}", f"Дверь правая {sid}"]
                    mid = (fx1 + fx2) / 2
                    panels.append(door_in_column(fx1, mid - g / 2, dy1, dy2, c.T, c.mat, sid, names[0], z_mode=z_mode))
                    panels.append(door_in_column(mid + g / 2, fx2, dy1, dy2, c.T, c.mat, sid, names[1], z_mode=z_mode))
                else:
                    one = sec.get("door_name") if isinstance(sec.get("door_name"), str) else f"Дверь {sid}"
                    dpanel = door_in_column(fx1, fx2, dy1, dy2, c.T, c.mat, sid, one, z_mode=z_mode)
                    if sec.get("door_swing"):          # направление открывания (AKD-224)
                        dpanel["swing"] = str(sec["door_swing"]).lower()
                    panels.append(dpanel)

        # штанга (AKD-177/186) — в любой секции, включая над стеком ящиков
        rod = rod_in_column(cx1, cx2, sec, yt, iz1, iz2, sid)
        if rod:
            rods_meta.append(rod)
            names.append(f"Штанга ({sid})" if len(sections) > 1 else "Штанга")
            # зона подвеса (~900 вниз от штанги) должна быть свободной
            hang_lo = rod["y1"] - 900
            busy = [p["name"] for p in panels
                    if p.get("section_id") == sid and p.get("type") == "shelf"
                    and p["placement"]["y2"] > hang_lo + 1
                    and p["placement"]["y1"] < rod["y1"] - 1]
            if busy:
                spec.setdefault("warnings", []).append(
                    f"Штанга ({sid}): зона подвеса занята ({', '.join(busy[:3])}) — "
                    "одежде на плечиках нужно ~900 мм свободной высоты")

        sections_meta.append({"id": sid, "type": kind,
                              "dimensions": {"width": round(cx2 - cx1, 2), "height": round(yt - yb, 2),
                                             "depth": round(iz2 - iz1, 2), "estimated": False},
                              "elements": names})

    # выравнивание полок между секциями (AKD-190): полка, отличающаяся от
    # структурного уровня (перекрытие стека) на ≤25 мм, приводится к нему —
    # перепад в пару сантиметров между соседними секциями бьёт по глазам
    anchors = [p["placement"]["y1"] for p in panels
               if p["type"] == "shelf" and p["name"] == "Полка под нишей"]
    if anchors:
        for p in panels:
            if p["type"] != "shelf" or p["name"] == "Полка под нишей":
                continue
            pl = p["placement"]
            near = next((a for a in anchors if 0 < abs(pl["y1"] - a) <= 25), None)
            if near is None:
                continue
            dy = round(near - pl["y1"], 2)
            pl["y1"], pl["y2"] = round(pl["y1"] + dy, 2), round(pl["y2"] + dy, 2)
            p["position"]["y"] = pl["y1"]
            spec.setdefault("warnings", []).append(
                f"«{p['name']}» выровнена с перекрытием стека соседней секции "
                f"({dy:+g} мм) — визуальная стыковка уровней")

    cc = carcass_calc(c)
    cc["columns"] = [{"x": b, "kind": s["kind"]} for s, b in zip(sections, bounds)]
    return build_project(spec, panels, sections=sections_meta, drawers=drawers_meta,
                         carcass_calc=cc, rods=rods_meta)
