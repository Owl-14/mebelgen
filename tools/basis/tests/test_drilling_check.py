"""Валидатор геометрии присадок (AKD-171): физика сверления."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.generators import generate_from_paramspec       # noqa: E402
from src.drilling_check import check_drilling_geometry   # noqa: E402


def _check(name: str):
    spec = json.loads((ROOT / "paramspecs" / f"{name}.json").read_text(encoding="utf-8"))
    return check_drilling_geometry(generate_from_paramspec(spec))


def test_all_reference_specs_clean():
    """После фиксов AKD-171 все эталонные спеки без ошибок сверловки."""
    for f in sorted((ROOT / "paramspecs").glob("*.json")):
        if f.name.endswith((".project.json", ".versions.json")):
            continue
        spec = json.loads(f.read_text(encoding="utf-8"))
        if spec.get("schemaVersion") != "paramspec-v1":
            continue
        r = check_drilling_geometry(generate_from_paramspec(spec))
        assert not r["errors"], f"{f.stem}: {r['errors'][:3]}"


def test_catches_wrong_direction():
    """Инверсия направления (дырка в воздух) ловится валидатором."""
    spec = json.loads((ROOT / "paramspecs" / "komi_46_shkaf_dokumenty.json").read_text(encoding="utf-8"))
    project = generate_from_paramspec(spec)
    from src.hardware import compute_drilling
    holes = compute_drilling(project)
    bad = [dict(h) for h in holes]
    flipped = False
    for h in bad:
        if h["purpose"] == "полкодержатель":
            h["dir"] = -h["dir"]                     # сверлим в полку, а не в боковину
            flipped = True
    assert flipped
    r = check_drilling_geometry(project, bad)
    assert r["errors"], "инверсия направления должна давать ошибки"
