"""Проверка КОДИРОВАНИЯ .cfrn (а не только placement).

placement-чек (consistency_check) видит только наши координаты. Но панель
кодируется в .cfrn матрицей 4×4, и толщина выдавливается в сторону, зависящую
от ориентации. Если трансляцию поставить не по той грани — деталь уезжает на
толщину, и .b3d собирается с нахлёстами, хотя placement был чист.

Этот класс багов ловится реконструкцией мировых AABB из матриц .cfrn и сверкой
с placement (совпадает с обратной выгрузкой b3d→cfrn из облака до миллиметра).

Запуск:  python -m pytest tests/test_cfrn_encoding.py   (из tools/basis)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.cfrn import check_cfrn_encoding, check_cfrn_holes     # noqa: E402
from src.generators import generate_from_paramspec             # noqa: E402
from src.materials import resolve_project_materials            # noqa: E402
from src.oldspec import old_to_paramspec                       # noqa: E402

PARAMSPECS = sorted(f for f in (ROOT / "paramspecs").glob("*.json")
                    if not f.name.endswith((".project.json", ".versions.json")))
OLDSPECS = sorted((ROOT / "fixtures" / "oldspecs").glob("*.json"))


def _paramspec_projects() -> list[tuple[str, dict]]:
    out = []
    for f in PARAMSPECS:
        s = json.loads(f.read_text(encoding="utf-8"))
        if s.get("schemaVersion") != "paramspec-v1":
            continue
        out.append((f.name, generate_from_paramspec(s)))
    return out


def test_paramspec_cfrn_encoding_matches_placement():
    bad = {}
    for name, pr in _paramspec_projects():
        issues = check_cfrn_encoding(pr)
        if issues:
            bad[name] = issues[:3]
    assert not bad, f"кодирование .cfrn ≠ placement:\n" + json.dumps(bad, ensure_ascii=False, indent=2)


def test_oldspec_cfrn_encoding_matches_placement():
    bad = {}
    for f in OLDSPECS:
        spec = old_to_paramspec(json.loads(f.read_text(encoding="utf-8")))
        pr = generate_from_paramspec(spec)
        issues = check_cfrn_encoding(pr)
        if issues:
            bad[f.name] = issues[:3]
    assert not bad, f"кодирование .cfrn ≠ placement:\n" + json.dumps(bad, ensure_ascii=False, indent=2)


def test_paramspec_cfrn_holes_match_drilling():
    """Присадки, закодированные в .cfrn, должны совпадать с compute_drilling
    по координатам/диаметру/глубине (кодирование objType 5 + table.holes)."""
    bad = {}
    for name, pr in _paramspec_projects():
        pr["material_refs"] = resolve_project_materials(pr)   # чтобы фурнитура резолвилась
        issues = check_cfrn_holes(pr)
        if issues:
            bad[name] = issues[:3]
    assert not bad, "присадки .cfrn ≠ compute_drilling:\n" + json.dumps(bad, ensure_ascii=False, indent=2)


def test_hardware_bodies_in_cfrn():
    """AKD-183: штанга/держатели/опоры/каркас кодируются в .cfrn телами objType 5."""
    import io as _io
    import zipfile
    from src.cfrn import project_to_cfrn_bytes, project_to_cfrn_json

    spec = json.loads((ROOT / "paramspecs" / "wardrobe_demo.json").read_text(encoding="utf-8"))
    pr = generate_from_paramspec(spec)
    d = project_to_cfrn_json(pr)
    tobjs = d["table"]["objects"]
    tri = d["table"].get("triangles", [])
    counts = {}
    for n in d["model"]["objs"][0]["objs"]:
        o = tobjs[n["tableIndex"]]
        if o.get("objType") == 5 and o.get("triangleData") is not None:
            counts[tri[o["triangleData"]]] = counts.get(tri[o["triangleData"]], 0) + 1
    assert sum(v for k, v in counts.items() if "Штанга-вешало" in k) == 1
    assert sum(v for k, v in counts.items() if "Штангодержатель" in k) == 2
    assert sum(v for k, v in counts.items() if "Опора" in k) == 4
    # OBJ тел лежат в zip .cfrn
    z = zipfile.ZipFile(_io.BytesIO(project_to_cfrn_bytes(pr)))
    assert any("Штанга" in n for n in z.namelist())
    assert any("Опора" in n for n in z.namelist())
    # кодирование панелей и присадок не пострадало
    assert not check_cfrn_encoding(pr)
    assert not check_cfrn_holes(pr)

    # металлокаркас стола
    dspec = json.loads((ROOT / "paramspecs" / "komi_38_stol_direktora.json").read_text(encoding="utf-8"))
    dd = project_to_cfrn_json(generate_from_paramspec(dspec))
    names = chr(10).join(dd["table"].get("triangles", []))
    assert "стойка 40×40" in names and "царга 40×40" in names


def test_back_wall_material_inherits_board_decor():
    """AKD-287: задник из корпусной плиты (ЛДСП) наследует декор корпуса;
    тонкий ДВП/ХДФ-задник остаётся отдельной записью материала."""
    from src.cfrn import project_to_cfrn_json

    def _back_mats(spec_name: str) -> set[str]:
        spec = json.loads((ROOT / "paramspecs" / f"{spec_name}.json").read_text(encoding="utf-8"))
        d = project_to_cfrn_json(generate_from_paramspec(spec))
        mats = d["table"]["materials"]
        return {mats[o["materialIndex"]]["name"] for o in d["table"]["objects"]
                if o.get("objType") == 2 and "задн" in str(o.get("name", "")).lower()
                and "ящик" not in str(o.get("name", "")).lower()}

    tm = _back_mats("tumba_moderatora")            # задник ЛДСП 16
    assert tm and all("ЛДСП" not in n for n in tm), tm   # декор, не генерик
    dv = _back_mats("tumba_404x490x674_drawers")   # задник ДВП
    assert "ДВП" in dv, dv
