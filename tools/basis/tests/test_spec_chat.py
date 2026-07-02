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
