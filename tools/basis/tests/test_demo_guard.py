"""AKD-271: защита публичного демо — rate-limit чата, бюджет токенов, демо-образцы."""

from __future__ import annotations

import json
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


def test_token_journal_keeps_daily_history(tmp_path):
    """Смена суток не затирает вчерашний расход, но журнал не растёт бесконечно."""
    journal = tmp_path / "chat_tokens.json"
    journal.write_text(json.dumps({f"2026-05-{i:02d}": i for i in range(1, 32)}
                                  | {f"2026-06-{i:02d}": i for i in range(1, 31)}
                                  | {f"2026-07-{i:02d}": i for i in range(1, 32)}),
                       encoding="utf-8")
    g = studio._ChatGuard(tmp_path)
    g._day = lambda: "2026-09-12"
    g.add_tokens(300)
    g._day = lambda: "2026-09-13"
    g.add_tokens(50)
    data = json.loads(journal.read_text(encoding="utf-8"))
    assert data["2026-09-12"] == 300 and data["2026-09-13"] == 50
    assert len(data) == 90 and "2026-05-01" not in data


def test_public_mode_protects_originals(tmp_path, monkeypatch):
    (tmp_path / "demo.json").write_text('{"schemaVersion":"paramspec-v1","draft":true}',
                                        encoding="utf-8")
    monkeypatch.setenv("STUDIO_PUBLIC", "1")
    st = studio._Studio(tmp_path / "demo.json", tmp_path)
    assert st.public and "demo.json" in st.protected
    monkeypatch.delenv("STUDIO_PUBLIC")
    st2 = studio._Studio(tmp_path / "demo.json", tmp_path)
    assert not st2.public and not st2.protected                  # локально — без ограничений
