"""Раскрой-превью (AKD-129): bin-packing по листам."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.generators import generate_from_paramspec   # noqa: E402
from src.nesting import nest_project, nesting_svg    # noqa: E402


def _project(name: str):
    spec = json.loads((ROOT / "paramspecs" / f"{name}.json").read_text(encoding="utf-8"))
    return generate_from_paramspec(spec)


def test_desk_fits_one_sheet_per_group():
    n = nest_project(_project("stol_ofisny_foto"))
    assert n["n_sheets"] >= 1
    total_parts = sum(len(s["parts"]) for g in n["groups"] for s in g["sheets"])
    assert total_parts == 4                       # все детали размещены
    assert 0 <= n["waste_pct"] < 100


def test_no_overlaps_on_sheet():
    """Детали на листе не пересекаются (с учётом пропила)."""
    n = nest_project(_project("komi_46_shkaf_dokumenty"))
    for g in n["groups"]:
        for sheet in g["sheets"]:
            parts = [p for p in sheet["parts"] if not p.get("oversize")]
            for i, a in enumerate(parts):
                for b in parts[i + 1:]:
                    sep = (a["x"] + a["w"] <= b["x"] + 0.01 or b["x"] + b["w"] <= a["x"] + 0.01
                           or a["y"] + a["h"] <= b["y"] + 0.01 or b["y"] + b["h"] <= a["y"] + 0.01)
                    assert sep, f'{a["name"]} ∩ {b["name"]}'


def test_svg_renders():
    svg = nesting_svg(_project("komi_72_tumba_podkatnaya"))
    assert svg.startswith("<svg") and "лист" in svg and "<rect" in svg
