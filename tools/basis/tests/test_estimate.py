"""Смета материалов (AKD-128): площади×цены базы, кромка, фурнитура, крепёж."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.generators import generate_from_paramspec        # noqa: E402
from src.materials import resolve_project_materials       # noqa: E402
from src.estimate import estimate_project                 # noqa: E402


def _est(name: str):
    spec = json.loads((ROOT / "paramspecs" / f"{name}.json").read_text(encoding="utf-8"))
    p = generate_from_paramspec(spec)
    p["material_refs"] = resolve_project_materials(p)
    return estimate_project(p)


def test_drawer_unit_estimate():
    e = _est("komi_72_tumba_podkatnaya")
    assert e["total"] > 1000, e["total"]                 # тумба с направляющими не бесплатная
    groups = {r["group"] for r in e["rows"]}
    assert groups >= {"Материалы", "Фурнитура", "Крепёж"}
    board = next(r for r in e["rows"] if r["name"].startswith("Плита"))
    assert board["cost"] and board["qty"] > 1            # >1 м² с коэффициентом


def test_desk_estimate_no_hardware_rows():
    e = _est("stol_ofisny_foto")
    assert e["total"] > 0
    assert not [r for r in e["rows"] if r["group"] == "Фурнитура" and "Ручки" in r["name"]]
