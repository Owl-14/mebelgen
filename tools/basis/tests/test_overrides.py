"""Оверрайды деталей (AKD-121): правки живут в спеке и переживают регенерацию."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.generators import generate_from_paramspec       # noqa: E402


def _spec(name: str = "stol_ofisny_foto"):
    return json.loads((ROOT / "paramspecs" / f"{name}.json").read_text(encoding="utf-8"))


def _panel(project, name):
    return next(p for p in project["panels"] if p["name"] == name)


def test_move_and_resize():
    spec = _spec()
    spec["overrides"] = [
        {"panel": "Царга задняя", "move": [0, -50, 0]},              # опустить царгу
        {"panel": "Столешница", "placement": {"x2": 1600}},          # расширить столешницу
    ]
    p = generate_from_paramspec(spec)
    tsar = _panel(p, "Царга задняя")
    assert tsar["placement"]["y2"] == 675 and tsar["placement"]["y1"] == 325
    top = _panel(p, "Столешница")
    assert top["placement"]["x2"] == 1600
    assert top["dimensions"]["width"] == 1600                         # производное пересчитано
    assert top.get("override") is True


def test_delete_and_add():
    spec = _spec()
    spec["overrides"] = [
        {"panel": "Царга задняя", "action": "delete"},
        {"panel": "Полка НСУ", "action": "add", "type": "shelf",
         "orientation": "horizont", "material": "ЛДСП",
         "placement": {"x1": 25, "x2": 1475, "y1": 300, "y2": 316, "z1": 100, "z2": 650}},
    ]
    p = generate_from_paramspec(spec)
    names = [x["name"] for x in p["panels"]]
    assert "Царга задняя" not in names and "Полка НСУ" in names
    shelf = _panel(p, "Полка НСУ")
    assert shelf["thickness"] == 16 and shelf["dimensions"]["width"] == 1450


def test_survives_regeneration():
    """Смена габарита изделия не стирает правку (переживает регенерацию)."""
    spec = _spec()
    spec["overrides"] = [{"panel": "Царга задняя", "move": [0, -80, 0]}]
    spec["dimensions"]["width"] = 1700                               # регенерация с новой шириной
    p = generate_from_paramspec(spec)
    tsar = _panel(p, "Царга задняя")
    assert tsar["placement"]["y2"] == 645                            # правка применилась
    assert tsar["placement"]["x2"] > 1600                            # и новая ширина тоже


def test_missing_panel_warns_not_breaks():
    spec = _spec()
    spec["overrides"] = [{"panel": "Несуществующая", "move": [0, 10, 0]}]
    p = generate_from_paramspec(spec)
    assert any("Несуществующая" in w for w in p.get("warnings", []))
    assert len(p["panels"]) == 4


def test_downstream_recomputes():
    """Присадки/чертёж считаются по итоговой (пост-оверрайд) геометрии."""
    from src.hardware import compute_drilling
    spec = _spec()
    base = {(h["purpose"], h["y"]) for h in compute_drilling(generate_from_paramspec(spec))}
    spec["overrides"] = [{"panel": "Царга задняя", "move": [0, -100, 0]}]
    moved = compute_drilling(generate_from_paramspec(spec))
    tsar_holes = [h for h in moved if h["purpose"] == "стяжка (конфирмат)"]
    assert tsar_holes and {(h["purpose"], h["y"]) for h in moved} != base
