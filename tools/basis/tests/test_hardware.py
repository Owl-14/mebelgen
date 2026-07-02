"""Тест расчёта присадок (AKD-88): по геометрии считаются нужные типы отверстий."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.generators import generate_from_paramspec           # noqa: E402
from src.hardware import compute_drilling, drilling_summary  # noqa: E402


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
    assert s.get("стяжка (конфирмат)", 0) > 0


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
    assert s["задник (гвоздь)"] >= 8            # сетка по периметру ДВП
    assert s["короб ящика (саморез)"] == 24     # 8 на ящик (дно 2×2 + ЗС 2×2)
    bom = fastener_bom(holes, resolve=True)
    names = {b["name"]: b for b in bom}
    assert names["Гвоздь 1.6×25"]["qty"] >= 8
    assert names["Гвоздь 1.6×25"].get("article")        # позиция из базы
    assert names["Конфирмат 7×50"].get("article")
    assert names["Заглушка самоклеящаяся D13"]["qty"] == names["Конфирмат 7×50"]["qty"]
