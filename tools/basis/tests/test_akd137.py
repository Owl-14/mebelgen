"""Крепёж 2.0 остаток (AKD-137): накладной задник, замки, сборочные узлы."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.generators import generate_from_paramspec       # noqa: E402
from src.hardware import compute_drilling, drilling_summary   # noqa: E402
from src.drilling_check import check_drilling_geometry    # noqa: E402
from src.cfrn import project_to_cfrn_json, check_cfrn_encoding, check_cfrn_holes  # noqa: E402


def _spec(name: str):
    return json.loads((ROOT / "paramspecs" / f"{name}.json").read_text(encoding="utf-8"))


def test_overlay_back_gets_nails():
    """Накладной задник перекрывает торцы → гвозди по периметру, физика чистая."""
    spec = _spec("komi_72_tumba_podkatnaya")
    spec["back_mount"] = "overlay"
    p = generate_from_paramspec(spec)
    back = next(x for x in p["panels"] if x["type"] == "back")
    assert back["placement"]["z1"] == 450 and back["placement"]["x1"] == 0
    s = drilling_summary(compute_drilling(p))
    assert s.get("задник (гвоздь)", 0) >= 8
    assert not check_drilling_geometry(p)["errors"]


def test_locks_on_doors():
    spec = _spec("komi_46_shkaf_dokumenty")
    spec.setdefault("hardware", {})["locks"] = True
    p = generate_from_paramspec(spec)
    s = drilling_summary(compute_drilling(p))
    assert s.get("замок (цилиндр Ø18)") == 2          # по замку на дверь
    assert not check_drilling_geometry(p)["errors"]


def test_assembly_units_in_cfrn():
    """Ящики и двери — сборочные узлы objType 7 isAssemblyUnit; чекеры чисты."""
    p = generate_from_paramspec(_spec("komi_72_tumba_podkatnaya"))
    d = project_to_cfrn_json(p)
    units = [o for o in d["table"]["objects"]
             if o.get("objType") == 7 and o.get("isAssemblyUnit")]
    assert len(units) == 3, [u["name"] for u in units]          # 3 ящика
    top = d["model"]["objs"][0]["objs"]
    nested = [n for n in top if n.get("objs")]
    assert len(nested) == 3
    assert all(len(n["objs"]) == 5 for n in nested)             # короб 4 + фасад
    # мировые координаты и присадки не пострадали от группировки
    assert not check_cfrn_encoding(p)
    assert not check_cfrn_holes(p)
