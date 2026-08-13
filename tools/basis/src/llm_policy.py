"""Cost and rollout policy for opt-in paid Studio LLMs.

The module deliberately contains no API credentials and performs no network
calls.  Prices are pinned metadata used for request guards and comparable
telemetry; updating them requires checking the vendor pricing pages again.
"""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PaidModelPolicy:
    provider_id: str
    model: str
    vision_model: str
    input_usd_per_mtok: float
    output_usd_per_mtok: float
    vision_input_usd_per_mtok: float | None = None
    vision_output_usd_per_mtok: float | None = None
    max_completion_tokens: int = 4_096

    def estimated_cost_usd(self, input_tokens: int, output_tokens: int) -> float:
        return round(
            max(0, int(input_tokens)) * self.input_usd_per_mtok / 1_000_000
            + max(0, int(output_tokens)) * self.output_usd_per_mtok / 1_000_000,
            8,
        )


# Verified 2026-08-13 against the official Kimi and Z.AI pricing pages.
# Cache-miss input is used intentionally: a safety gate must not assume a hit.
PAID_MODELS: dict[str, PaidModelPolicy] = {
    "kimi-k3": PaidModelPolicy(
        provider_id="kimi-k3", model="kimi-k3", vision_model="kimi-k3",
        input_usd_per_mtok=3.0, output_usd_per_mtok=15.0,
    ),
    "glm-5.2": PaidModelPolicy(
        provider_id="glm-5.2", model="glm-5.2", vision_model="glm-5v-turbo",
        input_usd_per_mtok=1.4, output_usd_per_mtok=4.4,
    ),
}


def truthy_env(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().casefold() in {"1", "true", "yes", "on"}


def paid_calls_allowed(provider_id: str) -> None:
    """Allow an explicitly selected paid ID only behind the permission flag."""
    if provider_id not in PAID_MODELS:
        return
    if not truthy_env("SPEC_CHAT_PAID_ENABLED"):
        raise RuntimeError("Paid LLM rollout is disabled (SPEC_CHAT_PAID_ENABLED=0)")
    if os.environ.get("CI") and not truthy_env("SPEC_CHAT_ALLOW_PAID_IN_CI"):
        raise RuntimeError("Paid LLM calls are disabled in CI")


def request_budget(
    provider_id: str,
    request_payload: dict[str, Any],
    *,
    modality: str = "text",
) -> dict[str, Any]:
    """Price a conservative upper bound of the request that will be sent.

    For text, one token cannot contain less than one UTF-8 byte, so the UTF-8
    byte length of the complete serialized payload is a deliberately high
    upper bound.  It includes messages, schemas and request parameters rather
    than only prompt text.  Vision is rejected until a separately verified
    tariff and accounting rule is pinned for that exact model.
    """
    policy = PAID_MODELS[provider_id]
    if modality != "text":
        if (policy.vision_input_usd_per_mtok is None
                or policy.vision_output_usd_per_mtok is None):
            raise RuntimeError(
                f"Paid vision pricing is unknown for {provider_id}; request blocked"
            )
        input_rate = policy.vision_input_usd_per_mtok
        output_rate = policy.vision_output_usd_per_mtok
    else:
        input_rate = policy.input_usd_per_mtok
        output_rate = policy.output_usd_per_mtok
    raw_tokens = os.environ.get("SPEC_CHAT_MAX_COMPLETION_TOKENS", "")
    try:
        requested_tokens = int(raw_tokens) if raw_tokens else policy.max_completion_tokens
    except ValueError as error:
        raise ValueError("SPEC_CHAT_MAX_COMPLETION_TOKENS must be an integer") from error
    output_tokens = max(1, min(requested_tokens, policy.max_completion_tokens))
    try:
        ceiling = float(os.environ.get("SPEC_CHAT_MAX_REQUEST_USD", "0.15"))
    except ValueError as error:
        raise ValueError("SPEC_CHAT_MAX_REQUEST_USD must be a number") from error
    priced_payload = copy.deepcopy(request_payload)
    priced_payload["max_completion_tokens"] = output_tokens
    serialized = json.dumps(
        priced_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    input_token_upper_bound = len(serialized)
    estimate = round(
        input_token_upper_bound * input_rate / 1_000_000
        + output_tokens * output_rate / 1_000_000,
        8,
    )
    if ceiling <= 0 or estimate > ceiling:
        raise RuntimeError(
            f"Paid LLM request estimate ${estimate:.4f} exceeds ${max(0, ceiling):.4f} limit"
        )
    return {
        "max_completion_tokens": output_tokens,
        "input_token_upper_bound": input_token_upper_bound,
        "estimated_cost_usd": estimate,
    }


def usage_cost(provider_id: str, prompt_tokens: Any, completion_tokens: Any) -> float | None:
    policy = PAID_MODELS.get(provider_id)
    if policy is None:
        return None
    if prompt_tokens is None and completion_tokens is None:
        return None
    try:
        return policy.estimated_cost_usd(int(prompt_tokens or 0), int(completion_tokens or 0))
    except (TypeError, ValueError):
        return None


__all__ = [
    "PAID_MODELS", "PaidModelPolicy", "paid_calls_allowed", "request_budget",
    "truthy_env", "usage_cost",
]
