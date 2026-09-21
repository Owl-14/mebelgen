"""MEB-166: высота короба ящика — кламп в генераторе и факты для AI-правки.

Сценарий из Studio: «сделай стенки ящиков выше, должно быть примерно 2/3 фасада».
GLM прислал box_height=500 — короба налезли друг на друга, гейт отклонил правку.
"""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

os.environ["SPEC_CHAT_PROVIDER"] = "mock"   # тесты всегда офлайн

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import src.spec_chat as sc                                   # noqa: E402
from src.production_gate import evaluate_production_gate     # noqa: E402
from src.prompt_registry import build_prompt_request         # noqa: E402

SPEC = json.loads((ROOT / "paramspecs" / "komi_39_tumba_pristavnaya.json").read_text(encoding="utf-8"))
MESSAGE = "сделай стенки ящиков выше, сейчас они на половине от фасада, должно быть примерно 2/3"


def _drawer_index(spec: dict) -> int:
    return next(i for i, s in enumerate(spec["sections"]) if s.get("kind") == "drawers")


def _heights(project: dict, kind: str) -> list[float]:
    return [p["dimensions"]["height"] for p in project["panels"] if p.get("type") == kind]


def test_oversized_box_height_is_clamped_inside_the_front():
    spec = copy.deepcopy(SPEC)
    spec["sections"][_drawer_index(spec)]["box_height"] = 500
    decision = evaluate_production_gate(spec)
    assert decision.report.ok, [issue.detail for issue in decision.report.errors]
    project = decision.project
    assert max(_heights(project, "drawer_side_left")) < min(_heights(project, "drawer_front"))


def test_edit_prompt_forwards_only_drawer_facts():
    facts = [{"target_id": "sections.0", "front_heights_mm": [214]}]
    request = build_prompt_request("edit_operations", message="x", spec=SPEC,
                                   context={"drawer_facts": facts, "panels": [{"name": "Полка"}]})
    assert json.loads(request.user)["context"] == {"drawer_facts": facts}
    assert "drawer_facts" in request.system and "max_box_height_mm" in request.system


def test_drawer_facts_reach_provider_and_relative_edit_passes_gate(monkeypatch):
    seen: dict = {}

    class Provider:
        def chat(self, spec, message, history=None, context=None, images=None):
            request = sc.build_chat_prompt_request(spec, message, history, context)
            fact = json.loads(request.user)["context"]["drawer_facts"][0]
            seen["fact"] = fact
            return {"reply": "Поднял стенки ящиков.", "operations": [{
                "op": "UpdateSection", "target_id": fact["target_id"],
                "changes": {"box_height": round(min(fact["front_heights_mm"]) * 2 / 3)},
            }]}

    monkeypatch.setattr(sc, "get_chat_provider", lambda name=None: Provider())
    result = sc.chat_edit(SPEC, MESSAGE)

    fact = seen["fact"]
    assert fact["box_height_mm"] < fact["max_box_height_mm"] < min(fact["front_heights_mm"])
    assert result.get("code") is None and result["spec"], result.get("reply")
    section = result["spec"]["sections"][_drawer_index(result["spec"])]
    assert section["box_height"] == round(min(fact["front_heights_mm"]) * 2 / 3)


def test_studio_shows_gate_reasons_instead_of_generic_error():
    source = (ROOT / "src" / "studio.py").read_text(encoding="utf-8")
    assert "if(p.error&&!p.spec) throw new Error(String(p.error));" not in source
    assert source.count("p.code==='production_gate_rejected'&&p.reply?p.reply:p.error") == 2
