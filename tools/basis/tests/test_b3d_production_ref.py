"""Реверс эталона производства (rules/b3d_production_reference.md).

Замок на факты, извлечённые из правленного технологом .b3d: если экстрактор
или парсер сломаются, эти проверки уронят CI раньше, чем логика уедет.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIX = ROOT / "qa" / "fixtures" / "wardrobe_demo_production.b3d"


def _extract():
    sys.path.insert(0, str(ROOT / "scripts"))
    from extract_b3d_hardware import extract
    return extract(str(FIX))


def test_desktop_b3d_parses_and_extracts():
    data = _extract()
    assert len(data["panels"]) == 27
    assert len(data["instances"]) == 269
    import collections
    by = collections.Counter(i["name"] for i in data["instances"])
    assert by["Гвоздь 1х16"] == 92                       # задник, шаг 96
    assert by["Шкант 8х30мм"] == 52
    assert by["Стяжка эксцентриковая (комплект эксцентрик+шток с пластиком саморез)"] == 40
    assert by["Евровинт конфирмат 7,0х50 стальной, оцинкованный"] == 12
    assert by["Эксцентрик усиленный в пластиковом корпусе 16 мм, белый"] == 16
    assert by["ПЕТЛЯ 110° DTC PIVOT-STAR SNAP-ON НАКЛАДНАЯ С ДОВОДЧИКОМ"] == 4


def test_production_templates_match_reference():
    data = _extract()
    lib = {v["name"].split("\r")[0]: v for v in data["library"].values()}
    st = lib["Стяжка эксцентриковая (комплект эксцентрик+шток с пластиком саморез)"]
    assert st["paramType"] == 2 and st["paramOffset"] == 16.0
    ds = {(h["d"], h["depth"]) for h in st["holes"]}
    assert ds == {(8.0, 34.0), (15.0, 12.5), (5.0, 12.0)}    # канал+чашка(35 от стыка)+шток
    assert all(h["drillMode"] == 2 for h in st["holes"])
    cup = next(h for h in st["holes"] if h["d"] == 15.0)
    assert cup["pos"][0] == 35.0                              # чашка на 35 от плоскости стыка
    conf = lib["Евровинт конфирмат 7,0х50 стальной, оцинкованный"]
    assert conf["paramType"] == 1
    assert {(round(h["d"], 1), round(h["depth"], 1)) for h in conf["holes"]} \
        == {(8.0, 16.0), (5.0, 35.0)}
    # сквозной проход — DrillMode 1, тело в ответной детали — 2
    modes = {round(h["d"], 1): h["drillMode"] for h in conf["holes"]}
    assert modes == {8.0: 1, 5.0: 2}
    nail = lib["Гвоздь 1х16"]
    assert nail["paramOffset"] == 4.0                         # толщина ХДФ задника
    # артикулы производственной базы прямо в имени (name\rарткул)
    assert "11154, MNFX" in st["name"] and "3851, CNFM" in conf["name"]


def test_edge_banding_rules_from_reference():
    data = _extract()
    by_name = {p["name"]: p for p in data["panels"]}
    door = by_name["Дверь левая"]
    assert sorted(b["thick"] for b in door["butts"]) == [2.0, 2.0, 2.0, 2.0]
    side = by_name["Боковина левая"]
    assert [b["thick"] for b in side["butts"]] == [0.5]       # только передний торец
    back = by_name["Задняя стенка"]
    assert back["butts"] == [] and back["thick"] == 4.0       # ХДФ без кромки
