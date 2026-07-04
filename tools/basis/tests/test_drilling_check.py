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


def test_completeness_all_reference_specs():
    """AKD-182: полнота — каждая деталь закреплена, заявленное построено."""
    import json
    from src.generators import generate_from_paramspec
    from src.completeness_check import check_completeness
    bad = {}
    for f in sorted((ROOT / "paramspecs").glob("*.json")):
        if f.name.endswith((".project.json", ".versions.json")):
            continue
        spec = json.loads(f.read_text(encoding="utf-8"))
        if "archetype" not in spec:
            continue
        errs = check_completeness(generate_from_paramspec(spec), spec)
        if errs:
            bad[f.stem] = errs[:3]
    assert not bad, json.dumps(bad, ensure_ascii=False, indent=2)


def test_completeness_catches_unfastened():
    """Полнота ловит незакреплённую деталь (регресс-защита самого валидатора)."""
    import json
    from src.generators import generate_from_paramspec
    from src.completeness_check import check_completeness
    spec = json.loads((ROOT / "paramspecs" / "komi_46_shkaf_dokumenty.json").read_text(encoding="utf-8"))
    p = generate_from_paramspec(spec)
    # «повесим» деталь: полка в воздухе, ни с чем не соприкасается
    p["panels"].append({
        "name": "Висячая полка", "type": "shelf", "basis_orientation": "horizont",
        "material": "ЛДСП", "thickness": 16,
        "dimensions": {"width": 100, "height": 100},
        "placement": {"x1": 5000, "x2": 5100, "y1": 5000, "y2": 5016, "z1": 0, "z2": 100},
        "position": {"x": 5000, "y": 5000, "z": 0},
        "rotation": {"x": 0, "y": 0, "z": 0},
        "edge_banding": {"top": 0.4, "bottom": 0.4, "left": 0.4, "right": 0.4},
        "estimated": True,
    })
    errs = check_completeness(p, spec)
    assert any("Висячая полка" in e for e in errs), errs
