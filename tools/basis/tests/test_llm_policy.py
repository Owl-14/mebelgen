from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.llm_policy import (  # noqa: E402
    paid_calls_allowed,
    request_budget,
    rollout_provider,
    usage_cost,
)


def test_paid_rollout_is_off_by_default_and_explicit_when_enabled(monkeypatch):
    monkeypatch.delenv("SPEC_CHAT_PAID_ENABLED", raising=False)
    assert rollout_provider() is None
    with pytest.raises(RuntimeError, match="rollout is disabled"):
        paid_calls_allowed("glm-5.2")

    monkeypatch.setenv("SPEC_CHAT_PAID_ENABLED", "1")
    monkeypatch.setenv("SPEC_CHAT_PAID_PROVIDER", "kimi-k3")
    assert rollout_provider() == "kimi-k3"


def test_paid_calls_fail_closed_in_ci(monkeypatch):
    monkeypatch.setenv("SPEC_CHAT_PAID_ENABLED", "1")
    monkeypatch.setenv("CI", "true")
    monkeypatch.delenv("SPEC_CHAT_ALLOW_PAID_IN_CI", raising=False)
    with pytest.raises(RuntimeError, match="disabled in CI"):
        paid_calls_allowed("glm-5.2")


def test_request_budget_bounds_output_and_cost(monkeypatch):
    monkeypatch.setenv("SPEC_CHAT_MAX_COMPLETION_TOKENS", "999999")
    monkeypatch.setenv("SPEC_CHAT_MAX_REQUEST_USD", "0.15")
    result = request_budget("kimi-k3", 1_000)
    assert result["max_completion_tokens"] == 4_096
    assert result["estimated_cost_usd"] == pytest.approx(0.06444)

    monkeypatch.setenv("SPEC_CHAT_MAX_REQUEST_USD", "0.01")
    with pytest.raises(RuntimeError, match="exceeds"):
        request_budget("kimi-k3", 1_000)


def test_usage_cost_uses_pinned_vendor_rates():
    assert usage_cost("kimi-k3", 1_000_000, 1_000_000) == 18.0
    assert usage_cost("glm-5.2", 1_000_000, 1_000_000) == 5.8
    assert usage_cost("mock", 10, 10) is None
    assert usage_cost("kimi-k3", None, None) is None
