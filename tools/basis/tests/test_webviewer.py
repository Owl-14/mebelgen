"""3D-просмотр (webviewer): HTML генерится для любой модели, правосторонний слой.

Проверяем, что просмотр строится по всем ParamSpec и содержит данные модели.
Это только слой ПОКАЗА — производственный .cfrn остаётся в родной конвенции БАЗИСа.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.generators import generate_from_paramspec       # noqa: E402
from src.webviewer import project_to_viewer_html, viewer_payload   # noqa: E402

PARAMSPECS = sorted(f for f in (ROOT / "paramspecs").glob("*.json")
                    if not f.name.endswith((".project.json", ".versions.json")))


def test_viewer_html_for_all_paramspecs():
    bad = []
    for f in PARAMSPECS:
        s = json.loads(f.read_text(encoding="utf-8"))
        if s.get("schemaVersion") != "paramspec-v1":
            continue
        pr = generate_from_paramspec(s)
        html = project_to_viewer_html(pr)
        # HTML валиден по-минимуму и несёт данные всех панелей + правостороннее преобразование
        if not (html.startswith("<!DOCTYPE html>") and "OrbitControls" in html
                and "bb.z1 - z" in html and "MebelScene" in html
                and html.count('"type"') == len(pr.get("panels", []))):
            bad.append(f.name)
    assert not bad, f"просмотр не собрался/неполон: {bad}"


def test_openables_groups():
    """Анимируемые узлы: ящик = фасад+короб+полоз ящика; дверь = фасад+чашки+ось петель."""
    s = json.loads((ROOT / "paramspecs" / "komi_72_tumba_podkatnaya.json").read_text(encoding="utf-8"))
    v = viewer_payload(generate_from_paramspec(s))
    drawers = [o for o in v["openables"] if o["kind"] == "drawer"]
    assert len(drawers) == 3
    for o in drawers:
        assert len(o["panels"]) == 5 and len(o["hardware"]) == 2   # фасад+4 короба; 2 полоза
        assert 0 < o["travel"] <= 380
        kinds = {v["hardware"][j]["kind"] for j in o["hardware"]}
        assert kinds == {"guide_drawer"}                            # корпусный полоз не едет

    s = json.loads((ROOT / "paramspecs" / "komi_46_shkaf_dokumenty.json").read_text(encoding="utf-8"))
    v = viewer_payload(generate_from_paramspec(s))
    doors = [o for o in v["openables"] if o["kind"] == "door"]
    assert len(doors) == 2
    hinges = {o["hinge"] for o in doors}
    assert hinges == {"left", "right"}                              # левая/правая навеска
    for o in doors:
        assert len(o["hardware"]) == 4                              # 4 чашки на дверь ~1664 мм
        assert {v["hardware"][j]["kind"] for j in o["hardware"]} == {"hinge_cup"}
