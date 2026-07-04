"""Studio (AKD-94…97): payload для редактора — модель, проверки, BOM.

Сокеты не поднимаем — тестируем чистые функции (build_payload / techview_svg).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.studio import build_payload, techview_svg     # noqa: E402

SPEC = json.loads((ROOT / "paramspecs" / "komi_72_tumba_podkatnaya.json").read_text(encoding="utf-8"))


def test_payload_ok_model():
    p = build_payload(SPEC)
    assert p["ok"], p["issues"]
    assert all(not v for v in p["issues"].values())
    v = p["viewer"]
    assert len(v["panels"]) == 20
    assert len(v["holes"]) == 93                    # komi_72: + фасадные стяжки и крепёж стенки короба (AKD-181)
    assert any(h["kind"] == "guide_corpus" for h in v["hardware"])
    assert p["stats"]["dims"]["w"] == 400 and p["stats"]["n_panels"] == 20
    assert any("аправляющ" in b["slot"] or "аправляющ" in b["name"] for b in p["bom"])


def test_payload_schema_error_keeps_shape():
    bad = json.loads(json.dumps(SPEC))
    del bad["dimensions"]
    p = build_payload(bad)
    assert not p["ok"] and p["issues"]["schema"]
    assert "viewer" not in p                        # модель не строится на битой схеме


def test_payload_reacts_to_edit():
    edited = json.loads(json.dumps(SPEC))
    edited["dimensions"]["width"] = 600             # правка как в редакторе
    edited["sections"][0]["drawers"] = 4
    p = build_payload(edited)
    assert p["ok"], p["issues"]
    xs = max(pl["x2"] for pl in p["viewer"]["panels"])
    assert abs(xs - 600) < 0.01                     # ширина применилась
    fronts = [pl for pl in p["viewer"]["panels"] if pl["type"] == "drawer_front"]
    assert len(fronts) == 4                         # ящиков стало 4


def test_techview_svg_clean():
    r = techview_svg(SPEC)
    assert r["svg"].startswith("<svg") and r["issues"] == []
