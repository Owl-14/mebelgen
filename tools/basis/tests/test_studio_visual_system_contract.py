"""Контракт первого среза единой визуальной системы MEB-094."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.studio import PAGE  # noqa: E402


def test_visual_system_is_documented_for_future_ui_slices() -> None:
    spec = ROOT / "ux" / "VISUAL_SYSTEM_SPEC.md"
    text = spec.read_text(encoding="utf-8")

    for token in ("`--ink` | `#1A1D21`", "`--accent` | `#3B82F6`", "локальные inline SVG", "3D"):
        assert token in text


def test_shared_tokens_keep_keyboard_focus_and_reduced_motion_available() -> None:
    assert "--warn:#c78a2b" in PAGE
    assert "button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible" in PAGE
    assert "@media (prefers-reduced-motion:reduce)" in PAGE


def test_restore_version_control_uses_an_accessible_outline_svg() -> None:
    start = PAGE.index('<button id="verRestore"')
    control = PAGE[start : PAGE.index("</button>", start) + len("</button>")]

    assert 'aria-label="Восстановить выбранную версию"' in control
    assert '<svg class="ui-icon"' in control
    assert "⤺" not in control
