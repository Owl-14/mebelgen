"""Контракт первого среза единой визуальной системы MEB-094."""

from __future__ import annotations

import sys
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.studio import PAGE  # noqa: E402


def _relative_luminance(hex_color: str) -> float:
    channels = [int(hex_color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(first: str, second: str) -> float:
    light, dark = sorted((_relative_luminance(first), _relative_luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


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


def test_review_directions_are_isolated_and_wait_for_user_selection() -> None:
    review = ROOT / "ux" / "style-directions"
    readme = (review / "README.md").read_text(encoding="utf-8")
    decision_log = (ROOT / "ux" / "DECISION_LOG.md").read_text(encoding="utf-8")

    assert "USER SELECTION REQUIRED" in readme
    assert "UX-042 | OPEN" in decision_log
    for name, marker in (
        ("direction-a-precision-light.css", 'data-meb094-direction="a"'),
        ("direction-b-warm-workshop.css", 'data-meb094-direction="b"'),
        ("direction-c-blueprint.css", 'data-meb094-direction="c"'),
    ):
        css = (review / name).read_text(encoding="utf-8")
        assert marker in css
        assert "animation:" not in css
        assert "transition:" not in css
        tokens = dict(re.findall(r"--(ink|mut|bg|card|accent|ok|warn|bad):(#(?:[0-9a-f]{6}))", css))
        assert set(tokens) == {"ink", "mut", "bg", "card", "accent", "ok", "warn", "bad"}
        for role in ("ink", "mut", "accent", "ok", "warn", "bad"):
            assert _contrast(tokens[role], tokens["card"]) >= 4.5
            assert _contrast(tokens[role], tokens["bg"]) >= 4.5


def test_review_evidence_covers_components_contrast_and_motion() -> None:
    review = ROOT / "ux" / "style-directions"
    states = (review / "COMPONENT_STATE_MATRIX.md").read_text(encoding="utf-8")
    contrast = (review / "CONTRAST_AUDIT.md").read_text(encoding="utf-8")
    motion = (review / "REDUCED_MOTION_EVIDENCE.md").read_text(encoding="utf-8")

    for component in ("Input", "Button", "Tab", "Select", "Toggle/checkbox", "Menu/popover", "Tooltip"):
        assert f"| {component} |" in states
    for state in ("Default", "Hover", "Focus", "Selected", "Disabled", "Busy", "Warning", "Error"):
        assert state in states
    assert contrast.count("| PASS |") == 18
    assert "prefers-reduced-motion" in motion

    browser = (review / "BROWSER_EVIDENCE.md").read_text(encoding="utf-8")
    for asset in (
        "origin-master-1440x900.jpg",
        "direction-a-1440x900.jpg",
        "direction-b-1440x900.jpg",
        "direction-c-1440x900.jpg",
    ):
        assert (review / asset).is_file()
        assert asset in browser
