from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.llm_policy import (  # noqa: E402
    paid_calls_allowed,
    request_budget,
    usage_cost,
)


def test_paid_flag_only_grants_permission_and_never_selects_a_provider(monkeypatch):
    import src.spec_chat as spec_chat

    monkeypatch.delenv("SPEC_CHAT_PROVIDER", raising=False)
    monkeypatch.delenv("PARAMSPEC_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GIGACHAT_AUTH_KEY", raising=False)
    monkeypatch.setenv("SPEC_CHAT_PAID_ENABLED", "1")
    monkeypatch.setenv("SPEC_CHAT_PAID_PROVIDER", "glm-5.2")
    assert spec_chat.resolve_provider_name() == "mock"


def test_paid_provider_requires_both_explicit_selection_and_permission(monkeypatch):
    import src.spec_chat as spec_chat

    monkeypatch.delenv("SPEC_CHAT_PAID_ENABLED", raising=False)
    monkeypatch.setenv("SPEC_CHAT_PROVIDER", "glm-5.2")
    assert spec_chat.resolve_provider_name() == "glm-5.2"
    with pytest.raises(RuntimeError, match="rollout is disabled"):
        paid_calls_allowed("glm-5.2")

    monkeypatch.setenv("SPEC_CHAT_PAID_ENABLED", "1")
    monkeypatch.setenv("SPEC_CHAT_ALLOW_PAID_IN_CI", "1")
    paid_calls_allowed("glm-5.2")


def test_paid_calls_fail_closed_in_ci(monkeypatch):
    monkeypatch.setenv("SPEC_CHAT_PAID_ENABLED", "1")
    monkeypatch.setenv("CI", "true")
    monkeypatch.delenv("SPEC_CHAT_ALLOW_PAID_IN_CI", raising=False)
    with pytest.raises(RuntimeError, match="disabled in CI"):
        paid_calls_allowed("glm-5.2")


def test_request_budget_bounds_output_and_cost(monkeypatch):
    monkeypatch.setenv("SPEC_CHAT_MAX_COMPLETION_TOKENS", "999999")
    monkeypatch.setenv("SPEC_CHAT_MAX_REQUEST_USD", "0.15")
    payload = {"model": "kimi-k3", "messages": [{"role": "user", "content": "я" * 500}]}
    result = request_budget("kimi-k3", payload)
    assert result["max_completion_tokens"] == 4_096
    priced_payload = {**payload, "max_completion_tokens": 4_096}
    expected_upper = len(json.dumps(
        priced_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8"))
    assert result["input_token_upper_bound"] == expected_upper
    assert result["input_token_upper_bound"] > 500
    assert result["estimated_cost_usd"] == pytest.approx(
        expected_upper * 3.0 / 1_000_000 + 4_096 * 15.0 / 1_000_000
    )

    monkeypatch.setenv("SPEC_CHAT_MAX_REQUEST_USD", "0.01")
    with pytest.raises(RuntimeError, match="exceeds"):
        request_budget("kimi-k3", payload)


@pytest.mark.parametrize("provider_id", ["kimi-k3", "glm-5.2"])
def test_unknown_paid_vision_pricing_fails_closed(provider_id, monkeypatch):
    monkeypatch.setenv("SPEC_CHAT_MAX_REQUEST_USD", "100")
    with pytest.raises(RuntimeError, match="vision pricing is unknown"):
        request_budget(provider_id, {"model": provider_id, "messages": []}, modality="vision")


def test_usage_cost_uses_pinned_vendor_rates():
    assert usage_cost("kimi-k3", 1_000_000, 1_000_000) == 18.0
    assert usage_cost("glm-5.2", 1_000_000, 1_000_000) == 5.8
    assert usage_cost("mock", 10, 10) is None
    assert usage_cost("kimi-k3", None, None) is None
