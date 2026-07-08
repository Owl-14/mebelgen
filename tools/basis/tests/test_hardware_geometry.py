"""Видимая геометрия фурнитуры: направляющие и петли (rules/hardware.md).

Проверяем: полозья парами в зазоре guide_gap (не пересекают ни короб, ни корпус),
петли комплектом чашка+плечо+планка, всё в габаритах, на всех ParamSpec без ошибок.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.generators import generate_from_paramspec                     # noqa: E402
from src.hardware_geometry import (                                    # noqa: E402
    compute_hardware_geometry, hardware_geometry_summary)

PARAMSPECS = sorted(f for f in (ROOT / "paramspecs").glob("*.json")
                    if not f.name.endswith((".project.json", ".versions.json")))


def _project(name: str):
    s = json.loads((ROOT / "paramspecs" / name).read_text(encoding="utf-8"))
    return generate_from_paramspec(s)


def test_drawer_guides_pair_per_drawer_and_fit():
    pr = _project("komi_72_tumba_podkatnaya.json")
    parts = compute_hardware_geometry(pr)
    s = hardware_geometry_summary(parts)
    assert s["guide_corpus"] == 6 and s["guide_drawer"] == 6      # 3 ящика × 2 стороны
    z_back = min(p["placement"]["z1"] for p in pr["panels"] if p["type"] == "back")
    for g in parts:
        assert g["z2"] <= z_back + 0.6, "направляющая заходит в задник"
        # полоз не пересекает вертикали корпуса
        for v in (p for p in pr["panels"] if p["type"].startswith("side")):
            vp = v["placement"]
            ox = min(g["x2"], vp["x2"]) - max(g["x1"], vp["x1"])
            assert ox <= 0.01, f"полоз в теле боковины: {g['name']}"


def test_hinges_full_set_per_door():
    pr = _project("komi_46_shkaf_dokumenty.json")
    parts = compute_hardware_geometry(pr)
    s = hardware_geometry_summary(parts)
    # 2 двери ~1664 мм → 4 петли каждая; комплект = чашка+плечо+планка
    assert s["hinge_cup"] == 8 and s["hinge_arm"] == 8 and s["hinge_plate"] == 8
    doors = [p for p in pr["panels"] if p["type"] == "door_front"]
    d_in = max(p["placement"]["z2"] for p in doors)
    for c in (p for p in parts if p["kind"] == "hinge_cup"):
        assert abs(c["z2"] - d_in) < 0.01 and c["z2"] - c["z1"] == 12   # чашка в теле фасада
    for pl in (p for p in parts if p["kind"] == "hinge_plate"):
        assert pl["x2"] - pl["x1"] == 8                                  # планка на грани


def test_all_paramspecs_geometry_builds():
    for f in PARAMSPECS:
        s = json.loads(f.read_text(encoding="utf-8"))
        if s.get("schemaVersion") != "paramspec-v1":
            continue
        pr = generate_from_paramspec(s)
        parts = compute_hardware_geometry(pr)
        for g in parts:
            assert g["x2"] > g["x1"] and g["y2"] > g["y1"] and g["z2"] > g["z1"], (f.name, g["name"])


def test_wardrobe_rod_modeled():
    """AKD-177: штанга — фурнитура: геометрия, присадки держателей, BOM."""
    import json
    from src.generators import generate_from_paramspec
    from src.hardware import compute_drilling, drilling_summary
    from src.drilling_check import check_drilling_geometry
    from src.hardware_geometry import compute_hardware_geometry
    from src.delivery import _hardware_bom

    w = json.loads((ROOT / "paramspecs" / "wardrobe_demo.json").read_text(encoding="utf-8"))
    p = generate_from_paramspec(w)
    rods = p["hardware"].get("rods") or []
    assert rods and rods[0]["axis"] == "x"
    holes = compute_drilling(p)
    assert drilling_summary(holes).get("штангодержатель (саморез)") == 4
    assert not check_drilling_geometry(p, holes)["errors"]
    kinds = {g["kind"] for g in compute_hardware_geometry(p)}
    assert "rod" in kinds and "rod_bracket" in kinds
    assert any(b["slot"] == "Штанга" for b in _hardware_bom(p))

    g = json.loads((ROOT / "paramspecs" / "komi_47_shkaf_garderobny.json").read_text(encoding="utf-8"))
    pg = generate_from_paramspec(g)
    assert (pg["hardware"].get("rods") or [{}])[0].get("axis") == "z"   # выдвижная
    hg = compute_drilling(pg)
    assert drilling_summary(hg).get("штангодержатель (саморез)") == 3   # рельса вверх в полку
    assert not check_drilling_geometry(pg, hg)["errors"]


def test_legs_and_metal_frame_modeled():
    """AKD-178: регулируемые опоры и металлокаркас видимы + крепёж столешницы/экрана."""
    import json
    from src.generators import generate_from_paramspec
    from src.hardware import compute_drilling, drilling_summary, leg_positions
    from src.drilling_check import check_drilling_geometry
    from src.hardware_geometry import compute_hardware_geometry

    # шкаф на регулируемых опорах: 4 опоры под дном + саморезы подпятников
    g = json.loads((ROOT / "paramspecs" / "komi_46_shkaf_dokumenty.json").read_text(encoding="utf-8"))
    p = generate_from_paramspec(g)
    assert len(leg_positions(p)) == 4
    kinds = {q["kind"] for q in compute_hardware_geometry(p)}
    assert "leg" in kinds
    holes = compute_drilling(p)
    assert drilling_summary(holes).get("опора (саморез)") == 8
    assert not check_drilling_geometry(p, holes)["errors"]
    assert p["hardware"]["legs"]["count"] == 4          # дефолт из габарита

    # стол на металлокаркасе: стойки+царги видимы, столешница и экран с крепежом
    d = json.loads((ROOT / "paramspecs" / "komi_38_stol_direktora.json").read_text(encoding="utf-8"))
    pd = generate_from_paramspec(d)
    kd = {q["kind"] for q in compute_hardware_geometry(pd)}
    assert "frame_leg" in kd and "frame_rail" in kd
    hd = compute_drilling(pd)
    sd = drilling_summary(hd)
    assert sd.get("каркас (саморез)", 0) >= 8           # подстолье + экран
    assert not check_drilling_geometry(pd, hd)["errors"]


def test_handles_visible_and_encoded():
    """AKD-184: ручки видимы (Studio) и кодируются телами в .cfrn."""
    import json
    from src.generators import generate_from_paramspec
    from src.hardware_geometry import compute_hardware_geometry
    from src.cfrn import project_to_cfrn_json

    w = json.loads((ROOT / "paramspecs" / "wardrobe_demo.json").read_text(encoding="utf-8"))
    p = generate_from_paramspec(w)
    handles = [g for g in compute_hardware_geometry(p) if g["kind"] == "handle"]
    assert len(handles) == 12                     # 4 ручки × (2 стойки + скоба)
    tri = chr(10).join(project_to_cfrn_json(p)["table"].get("triangles", []))
    assert "Ручка-скоба" in tri
    # push-to-open (count=0) — ручек нет
    t = json.loads((ROOT / "paramspecs" / "tz_tumba_dokumenty.json").read_text(encoding="utf-8"))
    pt = generate_from_paramspec(t)
    assert not [g for g in compute_hardware_geometry(pt) if g["kind"] == "handle"]


def test_hinges_avoid_shelves():
    """AKD-185: планки петель не попадают на уровни полок."""
    import json
    from src.generators import generate_from_paramspec
    from src.hardware import compute_drilling
    from src.drilling_check import check_drilling_geometry

    for name in ("komi_46_shkaf_dokumenty", "wardrobe_demo", "komi_47_shkaf_garderobny"):
        spec = json.loads((ROOT / "paramspecs" / f"{name}.json").read_text(encoding="utf-8"))
        p = generate_from_paramspec(spec)
        holes = compute_drilling(p)
        shelves = [q["placement"] for q in p["panels"] if q.get("type") == "shelf"]
        for h in holes:
            if h["purpose"] != "петля (планка)":
                continue
            hit = [sp for sp in shelves if sp["y1"] - 0.5 <= h["y"] <= sp["y2"] + 0.5
                   and sp["x1"] - 30 <= h["x"] <= sp["x2"] + 30]
            assert not hit, f"{name}: планка y={h['y']} на полке {hit}"
        assert not check_drilling_geometry(p, holes)["errors"]


def test_rod_over_drawers_and_cover_shelf():
    """AKD-186/187: штанга над стеком ящиков; стек перекрыт полкой."""
    import json
    from src.generators import generate_from_paramspec

    w = json.loads((ROOT / "paramspecs" / "wardrobe_demo.json").read_text(encoding="utf-8"))
    p = generate_from_paramspec(w)
    rods = p["hardware"]["rods"]
    assert rods and rods[0]["section_id"] == "right"      # над ящиками
    cover = [q for q in p["panels"] if q["name"] == "Полка под нишей"]
    assert len(cover) == 1                                # стек перекрыт
    assert not any("зона подвеса" in str(x) for x in p["warnings"])
    # штанга над полками — предупреждение
    w["sections"][0]["rod"] = {"height": 1830}
    p2 = generate_from_paramspec(w)
    assert any("зона подвеса" in str(x) for x in p2["warnings"])


def test_cfrn_front_faces_viewer():
    """AKD-188: фасады в сырых матрицах .cfrn — на +Z-стороне сцены (к камере)."""
    import json
    from src.generators import generate_from_paramspec
    from src.cfrn import project_to_cfrn_json, check_cfrn_encoding, check_cfrn_holes

    w = json.loads((ROOT / "paramspecs" / "wardrobe_demo.json").read_text(encoding="utf-8"))
    p = generate_from_paramspec(w)
    d = project_to_cfrn_json(p)
    tobjs = d["table"]["objects"]
    tz = {}
    tx = {}
    for nd in d["model"]["objs"][0]["objs"]:
        def walk(n):
            if n.get("objs"):
                for q in n["objs"]:
                    walk(q)
            else:
                g = tobjs[n["tableIndex"]]
                if "contour" in g:
                    tz[g.get("name")] = n["matrix"][14]
                    tx[g.get("name")] = n["matrix"][12]
        walk(nd)
    back_z = tz["Задняя стенка"]
    front_z = max(v for k, v in tz.items() if "Дверь" in k or "Фасад" in k)
    assert front_z > back_z                               # фронт ближе к +Z
    # секции НЕ зеркалятся: «Дверь левая» остаётся на малых X (слева на
    # «виде спереди» просмотрщика), ящики правой секции — на больших X
    door_x = min(m for k, m in tx.items() if "Дверь" in k)
    drawer_x = min(m for k, m in tx.items() if "Фасад ящик" in k)
    assert door_x < drawer_x
    assert not check_cfrn_encoding(p)                     # инволюция чекеров цела
    assert not check_cfrn_holes(p)


def test_door_hinge_by_position_and_swing():
    """AKD-223/224: петли по положению секции; door_swing up — откидная."""
    import json
    from src.generators import generate_from_paramspec
    from src.hardware import compute_drilling
    from src.drilling_check import check_drilling_geometry

    # одиночная дверь в ПРАВОЙ секции: петли на правой кромке (наружу),
    # ручка и замок — на левой (к центру тумбы)
    spec = {"schemaVersion": "paramspec-v1", "project_name": "Т", "furniture_type": "тумба",
            "archetype": "cabinet", "dimensions": {"width": 800, "depth": 400, "height": 900},
            "materials": {"board_thickness": 16},
            "hardware": {"handles": {"type": "ручка", "material": "металл", "color": "—",
                                     "size": 128, "count": 1, "offset_from_top": 40},
                         "locks": [{"type": "замок", "target": "right_door"}]},
            "sections": [{"kind": "shelves", "shelves": 2}, {"kind": "door", "door": 1}]}
    p = generate_from_paramspec(spec)
    holes = compute_drilling(p)
    door = next(q for q in p["panels"] if q["type"] == "door_front")
    dx1, dx2 = door["placement"]["x1"], door["placement"]["x2"]
    cups = [h for h in holes if h["purpose"] == "петля (чашка Ø35)"]
    assert cups and all(abs(h["x"] - (dx2 - 22)) < 1 for h in cups),         f"чашки не на правой кромке: {[h['x'] for h in cups]} (дверь {dx1}..{dx2})"
    grips = [h for h in holes if h["purpose"] == "ручка (винт)"]
    assert grips and all(abs(h["x"] - (dx1 + 40)) < 1 for h in grips)   # ручка слева
    locks = [h for h in holes if h["purpose"].startswith("замок")]
    assert len(locks) == 1 and abs(locks[0]["x"] - (dx1 + 30)) < 1
    assert not check_drilling_geometry(p, holes)["errors"]

    # откидная вверх: чашки вдоль ВЕРХНЕЙ кромки, планки в крышку (ось Y),
    # ручка снизу по центру, газлифт в BOM
    spec2 = {"schemaVersion": "paramspec-v1", "project_name": "Бар", "furniture_type": "шкаф",
             "archetype": "door_unit", "dimensions": {"width": 800, "depth": 350, "height": 400},
             "materials": {"board_thickness": 16},
             "hardware": {"handles": {"type": "ручка", "material": "металл", "color": "—",
                                      "size": 128, "count": 1, "offset_from_top": 40}},
             "sections": [{"kind": "door", "door": 1, "door_swing": "up"}]}
    p2 = generate_from_paramspec(spec2)
    h2 = compute_drilling(p2)
    door2 = next(q for q in p2["panels"] if q["type"] == "door_front")
    cups2 = [h for h in h2 if h["purpose"] == "петля (чашка Ø35)"]
    assert cups2 and all(abs(h["y"] - (door2["placement"]["y2"] - 22)) < 1 for h in cups2)
    plates2 = [h for h in h2 if h["purpose"] == "петля (планка)"]
    assert plates2 and all(h["axis"] == "y" for h in plates2)
    grips2 = [h for h in h2 if h["purpose"] == "ручка (винт)"]
    assert grips2 and all(abs(h["y"] - (door2["placement"]["y1"] + 40)) < 1 for h in grips2)
    assert not check_drilling_geometry(p2, h2)["errors"]
    from src.delivery import _hardware_bom
    assert any(b["slot"] == "Газлифт" for b in _hardware_bom(p2))
