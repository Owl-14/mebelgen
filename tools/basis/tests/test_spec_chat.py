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

    captured = {}

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
    parts = captured["payload"]["contents"][-1]["parts"]
    assert any("inline_data" in p for p in parts)


def test_prompt_keeps_geometry_rules():
    """Инварианты промпта: ориентация добавляемых деталей (8а) и запрет
    удалять пользовательские overrides при автопочинке (регресс на потерю)."""
    text = (ROOT / "prompts" / "spec_chat_prompt.txt").read_text(encoding="utf-8")
    for marker in ("vertical_partition", "тонкая по X", "ПРИМЫКАНИЕ ВСТЫК",
                   "context.panels", "ДЕТАЛИ ПОЛЬЗОВАТЕЛЯ НЕ УДАЛЯТЬ"):
        assert marker in text, f"в промпте потеряно правило: {marker}"


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
