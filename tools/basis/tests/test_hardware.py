"""Тест расчёта присадок (AKD-88): по геометрии считаются нужные типы отверстий."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.generators import generate_from_paramspec           # noqa: E402
from src.hardware import compute_drilling, drilling_summary, fastener_bom  # noqa: E402


def _project(name: str) -> dict:
    spec = json.loads((ROOT / "paramspecs" / f"{name}.json").read_text(encoding="utf-8"))
    return generate_from_paramspec(spec)


def _holes(name: str):
    holes = compute_drilling(_project(name))
    for h in holes:                      # структура полей
        assert {"panel", "purpose", "x", "y", "z", "diameter", "depth", "axis", "dir"} <= set(h)
        assert h["axis"] in ("x", "y", "z") and h["dir"] in (-1, 1)
    return drilling_summary(holes)


def test_shkaf_has_hinges_shelfpins_handles():
    s = _holes("komi_46_shkaf_dokumenty")
    assert s.get("ручка (винт)") == 4                      # 2 двери × 2
    assert s.get("петля (чашка Ø35)", 0) >= 4              # ≥2 петли на дверь
    assert s.get("полкодержатель", 0) == 32               # 8 полок × 4
    assert s.get("шкант 8×30 (торец)", 0) > 0              # система 32 (AKD-202)
    assert s.get("эксцентрик (канал Ø8)", 0) > 0


def test_tumba_drawers_has_guides_and_handles():
    s = _holes("komi_72_tumba_podkatnaya")
    assert s.get("ручка (винт)") == 6                      # 3 ящика × 2
    assert s.get("направляющая (винт)", 0) == 18           # 3 ящика × 2 боковины × 3
    assert "петля (чашка Ø35)" not in s                    # дверей нет


def test_all_komi_drill_without_error():
    for f in sorted((ROOT / "paramspecs").glob("komi_*.json")):
        holes = compute_drilling(_project(f.stem))
        assert isinstance(holes, list)


if __name__ == "__main__":
    for fn in (test_shkaf_has_hinges_shelfpins_handles,
               test_tumba_drawers_has_guides_and_handles,
               test_all_komi_drill_without_error):
        fn()
        print("OK", fn.__name__)


def test_fasteners_reverse_patterns():
    """Крепёж из реверса готовых изделий БАЗИС (docs/BASIS_FASTENERS_REVERSE.md)."""
    import json
    from src.generators import generate_from_paramspec
    from src.hardware import compute_drilling, drilling_summary, fastener_bom
    spec = json.loads((ROOT / "paramspecs" / "komi_72_tumba_podkatnaya.json").read_text(encoding="utf-8"))
    holes = compute_drilling(generate_from_paramspec(spec))
    s = drilling_summary(holes)
    # тонкий ДВП-задник по умолчанию НАКЛАДНОЙ (AKD-179): гвозди по периметру
    # в торцы корпуса — иначе врезному заподлицо держаться не за что
    assert s.get("задник (гвоздь)", 0) >= 8
    assert s["короб ящика (саморез)"] == 30     # 10 на ящик (дно 2×2 + ЗС к дну 2 + ЗС к боковинам 4)
    w = json.loads((ROOT / "paramspecs" / "wardrobe_demo.json").read_text(encoding="utf-8"))
    sw = drilling_summary(compute_drilling(generate_from_paramspec(w)))
    assert sw["задник (гвоздь)"] >= 8           # стойки/полки до задника
    bom = fastener_bom(holes, resolve=True)
    names = {b["name"]: b for b in bom}
    # система 32 (AKD-202): межпанельный крепёж — шкант Ø8 + эксцентрик,
    # конфирматов и заглушек в BOM больше нет
    assert "Конфирмат 7×50" not in names and "Заглушка самоклеящаяся D13" not in names
    assert names["Шкант 8×30"]["qty"] >= 2
    assert names["Эксцентрик Ø15 + шток"].get("article")   # позиция из базы


def test_desk_joints_covered():
    """AKD-146: стол получает крепёж — шкант+minifix столешницы, конфирматы царги."""
    import json
    from src.generators import generate_from_paramspec
    spec = json.loads((ROOT / "paramspecs" / "stol_ofisny_foto.json").read_text(encoding="utf-8"))
    holes = compute_drilling(generate_from_paramspec(spec))
    s = drilling_summary(holes)
    # столешница ↔ 2 опоры + царга ↔ 2 опоры: по 2 шканта и 2 стяжки на стык
    assert s.get("шкант 8×30 (торец)") == 8 and s.get("шкант 8×30 (пласть)") == 8
    assert s.get("эксцентрик (чашка Ø15)") == 8
    assert s.get("эксцентрик (шток)") == 8 and s.get("эксцентрик (канал Ø8)") == 8
    bom = {b["name"]: b["qty"] for b in fastener_bom(holes)}
    assert bom["Шкант 8×30"] == 8                    # 2 отверстия = 1 шкант
    assert bom["Эксцентрик Ø15 + шток"] == 8


def test_system32_grid():
    """Система 32 (AKD-202): шаги между стяжками стыка кратны 32, присадка Ø8."""
    import json
    from src.generators import generate_from_paramspec
    spec = json.loads((ROOT / "paramspecs" / "komi_72_tumba_podkatnaya.json").read_text(encoding="utf-8"))
    holes = compute_drilling(generate_from_paramspec(spec))
    joints = [h for h in holes if h["purpose"] in ("шкант 8×30 (торец)", "эксцентрик (канал Ø8)")]
    assert joints and all(h["diameter"] == 8 for h in joints)
    # кратность шага 32 проверяет drilling_check по каждому стыку
    from src.drilling_check import check_drilling_geometry
    spec2 = json.loads((ROOT / "paramspecs" / "komi_72_tumba_podkatnaya.json").read_text(encoding="utf-8"))
    r = check_drilling_geometry(generate_from_paramspec(spec2), holes)
    assert not [w for w in r["warnings"] if "не кратен 32" in w], r["warnings"]
    # конфирматов Ø7 и присадок Ø5 в стяжках больше нет
    assert not [h for h in holes if h["diameter"] in (5, 7)
                and "стяжк" in h["purpose"]]
    assert all(h["diameter"] == 8 for h in holes if h["purpose"] == "полкодержатель")
