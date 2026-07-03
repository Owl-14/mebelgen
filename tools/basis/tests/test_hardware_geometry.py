"""Видимая геометрия фурнитуры: направляющие и петли (rules/hardware.md).

Проверяем: полозья парами в зазоре guide_gap (не пересекают ни короб, ни корпус),
петли комплектом чашка+плечо+планка, всё в габаритах, на всех ParamSpec без ошибок.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.generators import generate_from_paramspec                     # noqa: E402
from src.hardware_geometry import (                                    # noqa: E402
    compute_hardware_geometry, hardware_geometry_summary)

PARAMSPECS = sorted(f for f in (ROOT / "paramspecs").glob("*.json")
                    if not f.name.endswith((".project.json", ".versions.json")))


def _project(name: str):
    s = json.loads((ROOT / "paramspecs" / name).read_text(encoding="utf-8"))
    return generate_from_paramspec(s)


def test_drawer_guides_pair_per_drawer_and_fit():
    pr = _project("komi_72_tumba_podkatnaya.json")
    parts = compute_hardware_geometry(pr)
    s = hardware_geometry_summary(parts)
    assert s["guide_corpus"] == 6 and s["guide_drawer"] == 6      # 3 ящика × 2 стороны
    z_back = min(p["placement"]["z1"] for p in pr["panels"] if p["type"] == "back")
    for g in parts:
        assert g["z2"] <= z_back + 0.6, "направляющая заходит в задник"
        # полоз не пересекает вертикали корпуса
        for v in (p for p in pr["panels"] if p["type"].startswith("side")):
            vp = v["placement"]
            ox = min(g["x2"], vp["x2"]) - max(g["x1"], vp["x1"])
            assert ox <= 0.01, f"полоз в теле боковины: {g['name']}"


def test_hinges_full_set_per_door():
    pr = _project("komi_46_shkaf_dokumenty.json")
    parts = compute_hardware_geometry(pr)
    s = hardware_geometry_summary(parts)
    # 2 двери ~1664 мм → 4 петли каждая; комплект = чашка+плечо+планка
    assert s["hinge_cup"] == 8 and s["hinge_arm"] == 8 and s["hinge_plate"] == 8
    doors = [p for p in pr["panels"] if p["type"] == "door_front"]
    d_in = max(p["placement"]["z2"] for p in doors)
    for c in (p for p in parts if p["kind"] == "hinge_cup"):
        assert abs(c["z2"] - d_in) < 0.01 and c["z2"] - c["z1"] == 12   # чашка в теле фасада
    for pl in (p for p in parts if p["kind"] == "hinge_plate"):
        assert pl["x2"] - pl["x1"] == 8                                  # планка на грани


def test_all_paramspecs_geometry_builds():
    for f in PARAMSPECS:
        s = json.loads(f.read_text(encoding="utf-8"))
        if s.get("schemaVersion") != "paramspec-v1":
            continue
        pr = generate_from_paramspec(s)
        parts = compute_hardware_geometry(pr)
        for g in parts:
            assert g["x2"] > g["x1"] and g["y2"] > g["y1"] and g["z2"] > g["z1"], (f.name, g["name"])
