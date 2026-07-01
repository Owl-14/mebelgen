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
from src.webviewer import project_to_viewer_html         # noqa: E402

PARAMSPECS = sorted((ROOT / "paramspecs").glob("*.json"))


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
                and "bb.z1 - z" in html and html.count('"x1"') == len(pr.get("panels", []))):
            bad.append(f.name)
    assert not bad, f"просмотр не собрался/неполон: {bad}"
