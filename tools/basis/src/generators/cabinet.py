"""Генератор cabinet: многоколоночный корпус (перегородки + содержимое колонок).

Колонки слева направо; в каждой — shelves | drawers | door | open. Покрывает
шкафы/тумбы/стеллажи/комоды с несколькими секциями. Через него же работает wardrobe.
"""

from __future__ import annotations

from typing import Any

from .base import read_carcass
from .corpus import carcass_calc
from .columns import column_bounds, door_in_column, drawer_stack, partitions, shelves_in_column
from .helpers import build_project, carcass, panel, shelf_levels


def generate(spec: dict[str, Any]) -> dict[str, Any]:
    c = read_carcass(spec)
    sections = spec.get("sections") or [{"kind": "open"}]
    g = c.gap
    yb, yt = c.Hleg + c.T, c.H - c.T
    iz1 = spec.get("interior_z_front", c.T)      # фронт полок/перегородок
    iz2 = c.D - c.T_back

    top_z = spec.get("top_overhang")
    panels = carcass(c.W, c.D, c.H, c.T, c.T_back, c.Hleg, c.mat, c.mat_back,
                     leg_as_panel=c.leg_as_panel, leg_type=c.leg_type,
                     z_front=spec.get("carcass_z_front", 0),
                     top_z=tuple(top_z) if top_z else None,
                     socle_full=spec.get("socle_full", False))
    bounds = column_bounds(c.W, c.T, sections)
    panels += partitions(bounds, c.H, c.T, c.Hleg, c.mat, iz1, iz2)

    drawers_meta: list[dict[str, Any]] = []
    sections_meta: list[dict[str, Any]] = []

    for idx, (sec, (cx1, cx2)) in enumerate(zip(sections, bounds), start=1):
        sid = sec.get("id", f"col{idx}")
        kind = sec["kind"]
        names: list[str] = []
        levels = sec.get("shelf_levels")
        if levels is None and sec.get("shelves"):
            levels = shelf_levels(yb, yt, sec["shelves"], c.T)
        levels = levels or []

        if kind == "drawers":
            _fb = sec.get("front_bottom")                       # Y-координата низа нижнего фасада
            fb = _fb if isinstance(_fb, (int, float)) and not isinstance(_fb, bool) else yb + g
            heights = sec.get("drawer_heights")
            n = sec["drawers"]
            if not heights:
                top = sec.get("front_top", yt - g)
                h = (top - fb - (n - 1) * g) / n
                heights = [round(h, 2)] * n
            # короб ящика не должен доходить до задника (передний край = D − T_back)
            sec_dr = {**sec, "back_limit": c.D - c.T_back}
            ps, dm, topy = drawer_stack(cx1, cx2, fb, heights, g, sec_dr, c.T, c.mat, sid, sec.get("prefix", ""))
            panels += ps
            drawers_meta += dm
            names += [p["name"] for p in ps]
            if sec.get("open_top"):
                sh = panel("Полка под нишей", "shelf", "horizont", (cx1, cx2), (topy, topy + c.T),
                           (sec.get("niche_z_front", 0), iz2), thickness=c.T, material=c.mat,
                           section_id=sid, estimated=True)
                panels.append(sh)
                names.append(sh["name"])
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
                dy1 = yb + g
                dy2 = (min(levels) - g) if (levels and sec.get("door_below_shelf")) else (yt - g)
                # имена дверей: door_names/door_name из ТЗ, иначе дефолт с id секции (уникально)
                dn = sec.get("door_names") or sec.get("door_name")
                dn = dn if isinstance(dn, list) else None
                if nd == 2:
                    names = dn or [f"Дверь левая {sid}", f"Дверь правая {sid}"]
                    mid = (cx1 + cx2) / 2
                    panels.append(door_in_column(cx1 + g, mid - g / 2, dy1, dy2, c.T, c.mat, sid, names[0], z_mode=z_mode))
                    panels.append(door_in_column(mid + g / 2, cx2 - g, dy1, dy2, c.T, c.mat, sid, names[1], z_mode=z_mode))
                else:
                    one = sec.get("door_name") if isinstance(sec.get("door_name"), str) else f"Дверь {sid}"
                    panels.append(door_in_column(cx1 + g, cx2 - g, dy1, dy2, c.T, c.mat, sid, one, z_mode=z_mode))

        sections_meta.append({"id": sid, "type": kind,
                              "dimensions": {"width": round(cx2 - cx1, 2), "height": round(yt - yb, 2),
                                             "depth": round(iz2 - iz1, 2), "estimated": False},
                              "elements": names})

    cc = carcass_calc(c)
    cc["columns"] = [{"x": b, "kind": s["kind"]} for s, b in zip(sections, bounds)]
    return build_project(spec, panels, sections=sections_meta, drawers=drawers_meta, carcass_calc=cc)
