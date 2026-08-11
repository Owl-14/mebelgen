"""Чат-правка ParamSpec (AKD-107/109/110): mock-провайдер, diff, защита схемы.

Сеть не нужна — mock разбирает типовые русские команды правилами.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ["SPEC_CHAT_PROVIDER"] = "mock"   # тесты всегда офлайн, даже при наличии ключа

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.spec_chat import chat_edit, spec_diff          # noqa: E402

SPEC = json.loads((ROOT / "paramspecs" / "stol_ofisny_foto.json").read_text(encoding="utf-8"))


def test_change_depth():
    r = chat_edit(SPEC, "сделай глубину 600")
    assert r["spec"] and r["spec"]["dimensions"]["depth"] == 600
    assert any("dimensions.depth" in c for c in r["changes"])
    assert SPEC["dimensions"]["depth"] == 700           # исходная спека не мутирует


def test_change_color():
    r = chat_edit(SPEC, "замени цвет на дуб вотан")
    assert r["spec"] and r["spec"]["materials"]["color"] == "Дуб вотан"


def test_add_legs_and_apron():
    r = chat_edit(SPEC, "добавь ножки 100 мм и царгу 250")
    assert r["spec"]["legs"]["height"] == 100
    assert r["spec"]["apron_height"] == 250


def test_remove_legs():
    spec = dict(SPEC, legs={"type": "боковины", "height": 100})
    r = chat_edit(spec, "убери ножки")
    assert r["spec"]["legs"]["height"] == 0


def test_facade_color():
    r = chat_edit(SPEC, "сделай фасады цвет дуб вотан")
    assert r["spec"]["materials"]["facade_color"] == "Дуб вотан"
    assert "color" not in [c.split(":")[0] for c in r["changes"]
                           if c.startswith("materials.color:")], "корпус не трогаем"


def test_metal_frame():
    r = chat_edit(SPEC, "сделай стол на металлокаркасе")
    assert r["spec"]["frame"] == "metal"


def test_unknown_command_no_spec():
    r = chat_edit(SPEC, "как дела?")
    assert r["spec"] is None and r["changes"] == []


def test_provider_failure_is_machine_readable(monkeypatch):
    import src.spec_chat as sc

    class BrokenProvider:
        def chat(self, *args, **kwargs):
            raise RuntimeError("offline")

    monkeypatch.setattr(sc, "get_chat_provider", lambda name=None: BrokenProvider())
    r = sc.chat_edit(SPEC, "сделай глубину 600")
    assert r["spec"] is None
    assert r["error"] == r["reply"]
    assert "offline" in r["error"]


def test_provider_operations_are_applied_and_validated_without_api_change(monkeypatch):
    import src.spec_chat as sc

    class OperationProvider:
        def chat(self, spec, message, history=None, context=None, images=None):
            request = sc.build_chat_prompt_request(spec, message, history, context)
            return {
                "reply": "Глубина изменена.",
                "operations": [{"op": "set", "path": "/dimensions/depth", "value": 600}],
                "trace": {"prompts": [request.trace], "router": {
                    "kind": "deterministic", "node": request.node,
                }},
            }

    monkeypatch.setattr(sc, "get_chat_provider", lambda name=None: OperationProvider())
    result = sc.chat_edit(SPEC, "сделай глубину 600")
    assert result["spec"]["dimensions"]["depth"] == 600
    assert any("dimensions.depth" in change for change in result["changes"])
    assert result["trace"]["prompts"][0]["prompt_id"] == "furniture.edit-operations"


def test_read_only_node_cannot_smuggle_a_spec_mutation(monkeypatch):
    import src.spec_chat as sc

    class MisbehavingProvider:
        def chat(self, spec, message, history=None, context=None, images=None):
            request = sc.build_chat_prompt_request(spec, message, history, context)
            changed = {**spec, "dimensions": {**spec["dimensions"], "depth": 999}}
            return {
                "reply": "Ответ на вопрос.", "spec": changed,
                "trace": {"prompts": [request.trace], "router": {
                    "kind": "deterministic", "node": request.node,
                }},
            }

    monkeypatch.setattr(sc, "get_chat_provider", lambda name=None: MisbehavingProvider())
    result = sc.chat_edit(SPEC, "сколько стоит?", context={"estimate_total": 100})
    assert result["spec"] is None and result["changes"] == []
    assert result["trace"]["router"]["node"] == "answer_query"


def test_openai_compat_provider_has_bounded_timeout_without_hidden_retries(monkeypatch):
    import types
    import src.spec_chat as sc

    captured = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("SPEC_CHAT_TIMEOUT_S", "45")
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeOpenAI))
    sc.OpenAICompatProvider("openai")

    assert captured["timeout"] == 45.0
    assert captured["max_retries"] == 0


def test_result_regenerates():
    """Правка чатом даёт спеку, из которой конвейер собирает валидную модель."""
    from src.studio import build_payload
    r = chat_edit(SPEC, "глубина 600, ножки 80")
    p = build_payload(r["spec"])
    assert p["ok"], p["issues"]
    assert p["stats"]["dims"]["d"] == 600


def test_diff_readable():
    d = spec_diff({"a": 1, "b": {"c": 2}}, {"a": 1, "b": {"c": 3}, "d": 4})
    assert d == ["b.c: 2 → 3", "d: (нет) → 4"]


def test_create_from_scratch():
    """D3: «сделай тумбу WxDxH с N ящиками» — новое изделие с нуля."""
    r = chat_edit(SPEC, "сделай тумбу 600х450х550 с 3 ящиками")
    assert r.get("created") is True
    s = r["spec"]
    assert s["archetype"] == "drawer_unit"
    assert s["dimensions"] == {"width": 600, "depth": 450, "height": 550, "tolerance": 5}
    assert s["sections"][0] == {"kind": "drawers", "drawers": 3}
    from src.studio import build_payload
    assert build_payload(s)["ok"]                    # собирается и проходит проверки


def test_question_about_model():
    """D3: вопрос о модели — ответ из контекста, спека не трогается."""
    r = chat_edit(SPEC, "сколько стоит?", context={"estimate_total": 2181.0})
    assert r["spec"] is None and "2 181" in r["reply"]
    r2 = chat_edit(SPEC, "сколько деталей в изделии?",
                   context={"n_panels": 4, "n_holes": 20})
    assert r2["spec"] is None and "4" in r2["reply"] and "20" in r2["reply"]


def test_gemini_provider_parses_response(monkeypatch):
    """AKD-203: GeminiChatProvider формирует запрос (с фото) и парсит JSON-ответ."""
    import src.spec_chat as sc

    captured = {"payloads": []}

    class _Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {"candidates": [{"content": {"parts": [
                {"text": json.dumps({"reply": "Глубина 600.",
                                     "spec": {**SPEC, "dimensions": {**SPEC["dimensions"], "depth": 600}}})}]}}]}

    def _post(url, params=None, json=None, timeout=None):
        captured["url"] = url; captured["key"] = (params or {}).get("key")
        captured["payload"] = json
        captured["payloads"].append(json)
        return _Resp()

    monkeypatch.setattr(sc, "get_chat_provider", lambda name=None: sc.GeminiChatProvider())
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    import requests
    monkeypatch.setattr(requests, "post", _post)

    r = chat_edit(SPEC, "сделай глубину 600",
                  images=[{"mime": "image/png", "data": "QUJD"}])
    assert r["spec"] and r["spec"]["dimensions"]["depth"] == 600
    # фото ушло в inline_data, ключ — в query
    assert captured["key"] == "test-key"
    assert any(
        "inline_data" in part
        for payload in captured["payloads"]
        for part in payload["contents"][-1]["parts"]
    )
    assert [item["prompt_id"] for item in r["trace"]["prompts"]] == [
        "furniture.vision-facts", "furniture.create-paramspec",
    ]


def test_mock_result_exposes_versioned_prompt_trace():
    result = chat_edit(SPEC, "сделай глубину 600")
    trace = result["trace"]
    assert trace["router"] == {"kind": "deterministic", "node": "edit_operations"}
    assert trace["prompts"][0]["prompt_id"] == "furniture.edit-operations"
    assert trace["prompts"][0]["prompt_version"] == "1.0.0"


def test_gigachat_provider(monkeypatch):
    """AKD-203: GigaChat — обмен ключа на токен + JSON-ответ (сеть замокана)."""
    import src.spec_chat as sc

    class _R:
        def __init__(self, j): self._j = j
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return self._j

    def _post(url, **kw):
        if "oauth" in url:
            assert kw["headers"]["Authorization"].startswith("Basic ")
            return _R({"access_token": "tok123", "expires_at": 9999999999000})
        assert kw["headers"]["Authorization"] == "Bearer tok123"
        return _R({"choices": [{"message": {"content":
            'Готово. {"reply":"Ширина 900.","spec":'
            + json.dumps({**SPEC, "dimensions": {**SPEC["dimensions"], "width": 900}})
            + '}'}}]})

    monkeypatch.setenv("GIGACHAT_AUTH_KEY", "YXBwOnNlY3JldA==")
    monkeypatch.setattr(sc, "get_chat_provider", lambda name=None: sc.GigaChatProvider())
    import requests
    monkeypatch.setattr(requests, "post", _post)

    r = chat_edit(SPEC, "сделай ширину 900")
    assert r["spec"] and r["spec"]["dimensions"]["width"] == 900
