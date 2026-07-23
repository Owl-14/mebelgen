"""AKD-259/260: контракт ParamSpec (порядок ящиков, алиасы) и производственный
паритет материалов/кромки (фасады и артикулы доезжают до .cfrn)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.generators import generate_from_paramspec              # noqa: E402
from src.cfrn import project_to_cfrn_json                       # noqa: E402


def _spec(**over):
    s = {
        "schemaVersion": "paramspec-v1", "project_name": "т", "archetype": "drawer_unit",
        "dimensions": {"width": 450, "depth": 450, "height": 700},
        "materials": {"board_thickness": 16, "board_material": "ЛДСП", "color": "Белый"},
        "legs": {"type": "нет", "height": 0},
        "sections": [{"kind": "drawers", "drawers": 3}],
    }
    s.update(over)
    return s


# ---------------------------------------------------------------- AKD-259

def test_drawer_heights_top_down():
    """drawer_heights в спеке — СВЕРХУ ВНИЗ (первый элемент — верхний ящик)."""
    s = _spec()
    s["sections"][0]["drawer_heights"] = [100, 200, 300]
    p = generate_from_paramspec(s)
    fronts = sorted(((q["placement"]["y1"], q["placement"]["y2"]) for q in p["panels"]
                     if q["type"] == "drawer_front"))
    hs = [round(y2 - y1) for y1, y2 in fronts]          # снизу вверх по Y
    # низ 300, середина 200, верх 100 (может быть продлён на T перекрытием стека)
    assert hs[0] == 300 and hs[1] == 200 and 100 <= hs[2] <= 120, hs


def test_door_inset_alias():
    """door_inset: true → врезная дверь в проёме (z 0..T, внутри секции)."""
    s = _spec(archetype="door_unit",
              sections=[{"kind": "door", "door": 1, "door_inset": True}])
    p = generate_from_paramspec(s)
    d = next(q for q in p["panels"] if q["type"] == "door_front")
    pl = d["placement"]
    assert pl["z1"] >= 0, pl                             # врезная: в плоскости корпуса
    assert pl["x1"] >= 16, pl                            # в проёме, не перекрывает боковину


def test_dead_open_top_warns_not_silent():
    s = _spec()
    s["sections"][0]["open_top"] = 200
    p = generate_from_paramspec(s)
    assert any("устарело" in w for w in p.get("warnings", [])), p.get("warnings")


def test_composite_carries_block_warnings():
    inner = _spec()
    inner["warnings"] = ["цвет по согласованию — подтвердить"]
    s = {
        "schemaVersion": "paramspec-v1", "project_name": "к", "archetype": "composite",
        "dimensions": {"width": 900, "depth": 450, "height": 700},
        "materials": {"board_thickness": 16, "board_material": "ЛДСП", "color": "Белый"},
        "blocks": [{"name": "лев", "origin": {"x": 0}, "spec": inner},
                   {"name": "прав", "origin": {"x": 450}, "spec": json.loads(json.dumps(inner))}],
    }
    p = generate_from_paramspec(s)
    ws = p.get("warnings", [])
    assert any(w.startswith("лев:") for w in ws) and any(w.startswith("прав:") for w in ws), ws


# ---------------------------------------------------------------- AKD-260

def test_facade_material_reaches_cfrn():
    s = _spec()
    s["materials"]["facade_color"] = "Дуб Вотан"
    s["materials"]["facade_article"] = "H1387"
    proj = generate_from_paramspec(s)
    cf = project_to_cfrn_json(proj)
    mats = cf["table"]["materials"] if "table" in cf else cf["materials"]
    names = [m["name"] for m in mats]
    fi = next(i for i, n in enumerate(names) if "Вотан" in n)
    assert mats[fi].get("art") == "H1387"
    objs = cf["table"]["objects"] if "table" in cf else cf["objects"]
    by_type = {}
    for o in objs:
        if o.get("objType") == 2:
            nm = str(o.get("name", ""))
            by_type.setdefault("фасад" if "Фасад" in nm else "корпус", set()).add(o["materialIndex"])
    assert by_type["фасад"] == {fi}, by_type                 # фасады — декором фасадов
    assert fi not in by_type["корпус"], by_type              # корпус — не декором фасадов


def test_edge_policy_by_purpose():
    """Кромка по эталону технолога (AKD-287): фасад 2 по кругу, видимые
    не-фасадные торцы 0.5, скрытые — без кромки."""
    p = generate_from_paramspec(_spec())
    by = {q["type"]: q["edge_banding"] for q in p["panels"]}
    assert set(by["drawer_front"].values()) == {2}
    side = by["side_left"]
    assert side["left"] == 0.5 and side["right"] == 0        # перед 0.5, зад скрыт
    assert set(by["back"].values()) == {0}
    assert set(by["drawer_bottom"].values()) == {0}
    assert by["drawer_side_left"]["top"] == 0.5              # верхний торец короба


def test_edge_banding_reaches_cfrn_butts():
    """AKD-287 ф.2: кромка кодируется в .cfrn (butts по сторонам контура) —
    реверс round-trip файла технолога (task 13307)."""
    proj = generate_from_paramspec(_spec())
    cf = project_to_cfrn_json(proj)
    t = cf["table"]
    edge_mats = {i for i, m in enumerate(t["materials"])
                 if "ромка" in str(m.get("name", ""))}
    assert edge_mats, "нет материалов кромки"
    by = {o["name"]: o.get("butts", []) for o in t["objects"] if o.get("objType") == 2}
    # фасад: 4 стороны по 2 мм
    front = next(b for n, b in by.items() if "Фасад" in n)
    assert sorted(x["elemIndex"] for x in front) == [0, 1, 2, 3]
    assert all(x["thickness"] == 2.0 and x["materialIndex"] in edge_mats for x in front)
    # боковина: только передний торец (elem 3 нашего vertical-контура), 0.5
    side = by["Боковина левая"]
    assert [(x["elemIndex"], x["thickness"]) for x in side] == [(3, 0.5)]
    # задник и дно ящика — без кромки
    assert by.get("Задняя стенка", []) == []
    assert by.get("Ящик 1 дно", []) == []
    # формат записи — как в файле технолога
    assert {"elemIndex", "materialIndex", "thickness", "width", "clip",
            "overhung", "allowance", "cutIndex"} - set(front[0]) == {"overhung"}
    assert front[0]["width"] == 19 and front[0]["overhang"] == 30
