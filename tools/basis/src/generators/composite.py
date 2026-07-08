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
    sections: list[dict[str, Any]] = []
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
        for d in sub.get("drawers", []):
            drawers.append(d)
        for s in sub.get("sections", []):
            s = dict(s)
            s["id"] = f"{prefix}_{s['id']}" if prefix else s["id"]
            sections.append(s)

    # верхнеуровневые материалы/габарит берём из spec
    from .helpers import build_project
    return build_project(spec, panels, sections=sections, drawers=drawers,
                         carcass_calc={"blocks": [b.get("name", "") for b in blocks],
                                       "construction": "composite"})
