"""Генератор composite: сборка нескольких блоков со смещением (угловые/комби)."""

from __future__ import annotations

from typing import Any


def _shift(panel: dict[str, Any], ox: float, oy: float, oz: float) -> dict[str, Any]:
    pl = panel["placement"]
    pl["x1"] += ox; pl["x2"] += ox
    pl["y1"] += oy; pl["y2"] += oy
    pl["z1"] += oz; pl["z2"] += oz
    pos = panel.get("position")
    if isinstance(pos, dict):
        pos["x"] += ox; pos["y"] = pos.get("y", 0) + oy; pos["z"] += oz
    return panel


def generate(spec: dict[str, Any]) -> dict[str, Any]:
    from .registry import generate_from_paramspec  # поздний импорт: избегаем цикла

    blocks = spec.get("blocks") or []
    if not blocks:
        raise ValueError("composite требует blocks[]")

    panels: list[dict[str, Any]] = []
    drawers: list[dict[str, Any]] = []
    doors: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []
    rods: list[dict[str, Any]] = []
    locks: list[dict[str, Any]] = []
    handles_cfg: dict[str, Any] | None = None   # конфиг ручек из первого блока
    handles_count = 0                            # суммарно по блокам
    slots_first: dict[str, Any] = {}             # guides/hinges — первый непустой
    legs_cfg: dict[str, Any] | None = None
    legs_count = 0                               # опоры: сумма по блокам (не эвристика)
    block_warns: list[str] = []                  # допущения блоков (AKD-259)
    block_est: list[str] = []
    for b in blocks:
        sub_spec = b["spec"]
        if sub_spec.get("archetype") == "composite":
            raise ValueError("composite внутри composite не поддерживается")
        sub = generate_from_paramspec(sub_spec)
        ox = float(b.get("origin", {}).get("x", 0))
        oy = float(b.get("origin", {}).get("y", 0))
        oz = float(b.get("origin", {}).get("z", 0))
        prefix = b.get("name", "")
        for p in sub["panels"]:
            if prefix:
                p["name"] = f"{prefix}: {p['name']}"
            panels.append(_shift(p, ox, oy, oz))
        # ящики: позиция короба — в координатах блока, сдвигаем на origin,
        # id уникализируем (иначе направляющие/анимация двух блоков слипаются)
        for d in sub.get("drawers", []):
            if prefix:
                d["id"] = f"{prefix}_{d.get('id')}"
            pos = d.get("position")
            if isinstance(pos, dict):
                pos["x"] = pos.get("x", 0) + ox
                pos["y"] = pos.get("y", 0) + oy
                pos["z"] = pos.get("z", 0) + oz
            drawers.append(d)
        for do in sub.get("doors", []):
            if prefix:
                do["id"] = f"{prefix}_{do.get('id')}"
            pos = do.get("position")
            if isinstance(pos, dict):
                pos["x"] = pos.get("x", 0) + ox
                pos["y"] = pos.get("y", 0) + oy
                pos["z"] = pos.get("z", 0) + oz
            doors.append(do)
        for s in sub.get("sections", []):
            s = dict(s)
            s["id"] = f"{prefix}_{s['id']}" if prefix else s["id"]
            sections.append(s)
        # фурнитура блока: штанги сдвигаем на origin; ручки суммируем;
        # направляющие/петли — конфиг первого блока; замки — с префиксом цели
        shw = sub.get("hardware") or {}
        for r in shw.get("rods") or []:
            r = dict(r)
            if prefix:
                r["id"] = f"{prefix}_{r.get('id')}"
                r["section_id"] = f"{prefix}_{r.get('section_id')}"
            for k, o in (("x1", ox), ("x2", ox), ("y1", oy), ("y2", oy),
                         ("z1", oz), ("z2", oz)):
                if isinstance(r.get(k), (int, float)):
                    r[k] = r[k] + o
            rods.append(r)
        h = shw.get("handles") or {}
        if h.get("count"):
            handles_count += int(h["count"])
            if handles_cfg is None:
                handles_cfg = dict(h)
        for slot in ("drawer_guides", "hinges"):
            if shw.get(slot) and slot not in slots_first:
                slots_first[slot] = shw[slot]
        for lk in shw.get("locks") or []:
            lk = dict(lk)
            if prefix and lk.get("target"):
                lk["target"] = f"{prefix}: {lk['target']}"
            locks.append(lk)
        sl = sub_spec.get("legs") or {}
        if legs_cfg is None and sl.get("height"):
            legs_cfg = dict(sl)
        legs_count += int((sub.get("hardware", {}).get("legs") or {}).get("count") or 0)
        # допущения блоков не теряем (AKD-259): warnings/estimated_values → верх
        for w in (sub_spec.get("warnings") or []):
            block_warns.append(f"{prefix}: {w}" if prefix else str(w))
        for ev in (sub_spec.get("estimated_values") or []):
            block_est.append(f"{prefix}: {ev}" if prefix else str(ev))

    # верхнеуровневые материалы/габарит берём из spec; фурнитуру и опоры,
    # не заданные на верхнем уровне (чат отдаёт композит без hardware/legs),
    # наследуем из блоков — иначе ручки/направляющие/опоры пропадают
    spec = dict(spec)
    hw = dict(spec.get("hardware") or {})
    if handles_cfg and not hw.get("handles"):
        handles_cfg["count"] = handles_count
        hw["handles"] = handles_cfg
    for slot, v in slots_first.items():
        hw.setdefault(slot, v)
    if locks and not hw.get("locks"):
        hw["locks"] = locks
    if hw:
        spec["hardware"] = hw
    if legs_cfg and not (spec.get("legs") or {}).get("height"):
        if legs_count:
            legs_cfg["count"] = legs_count
        spec["legs"] = legs_cfg
    for key, extra in (("warnings", block_warns), ("estimated_values", block_est)):
        if extra:
            merged = list(spec.get(key) or [])
            merged += [w for w in extra if w not in merged]
            spec[key] = merged

    from .helpers import build_project
    return build_project(spec, panels, sections=sections, drawers=drawers,
                         doors=doors, rods=rods or None,
                         carcass_calc={"blocks": [b.get("name", "") for b in blocks],
                                       "construction": "composite"})
