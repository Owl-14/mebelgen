"""AKD-271: защита публичного демо — rate-limit чата, бюджет токенов, демо-образцы."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import src.studio as studio                                     # noqa: E402


def test_rate_limit_per_minute(tmp_path, monkeypatch):
    monkeypatch.setattr(studio, "_CHAT_RPM", 3)
    g = studio._ChatGuard(tmp_path)
    assert [g.check("1.2.3.4") for _ in range(3)] == [None, None, None]
    assert "часто" in g.check("1.2.3.4")                        # 4-й в минуту — отказ
    assert g.check("5.6.7.8") is None                           # другой IP не задет


def test_daily_token_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(studio, "_TOKENS_PER_DAY", 1000)
    g = studio._ChatGuard(tmp_path)
    assert g.tokens_left() == 1000
    g.add_tokens(700)
    g.add_tokens(400)                                           # суммируется в файле
    assert g.tokens_left() == -100                              # бюджет исчерпан


def test_public_mode_protects_originals(tmp_path, monkeypatch):
    (tmp_path / "demo.json").write_text('{"schemaVersion":"paramspec-v1","draft":true}',
                                        encoding="utf-8")
    monkeypatch.setenv("STUDIO_PUBLIC", "1")
    st = studio._Studio(tmp_path / "demo.json", tmp_path)
    assert st.public and "demo.json" in st.protected
    monkeypatch.delenv("STUDIO_PUBLIC")
    st2 = studio._Studio(tmp_path / "demo.json", tmp_path)
    assert not st2.public and not st2.protected                  # локально — без ограничений
