"""
Per-model pricing tables in CNY per 1M tokens.

Used by providers to attach a `cost_cny` estimate to every response.
Update these numbers when providers change pricing — single source of truth.
"""

from __future__ import annotations

USD_TO_CNY = 7.2

# ---- DeepSeek (official CNY pricing) ---------------------------------------
DEEPSEEK_PRICES: dict[str, dict[str, float]] = {
    # https://api-docs.deepseek.com/zh-cn/quick_start/pricing
    "deepseek-chat":     {"input": 2.0,  "input_cached": 0.5, "output": 8.0},
    "deepseek-reasoner": {"input": 4.0,  "input_cached": 1.0, "output": 16.0},
}

# ---- Claude (USD list price → CNY via USD_TO_CNY) ---------------------------
# https://www.anthropic.com/pricing#anthropic-api
# Cached input ≈ 10% of base input.
_CLAUDE_USD: dict[str, dict[str, float]] = {
    "claude-3-5-haiku-20241022":  {"input": 0.80, "output": 4.0},
    "claude-3-5-sonnet-20241022": {"input": 3.0,  "output": 15.0},
    "claude-sonnet-4":            {"input": 3.0,  "output": 15.0},
    "claude-opus-4":              {"input": 15.0, "output": 75.0},
}

CLAUDE_PRICES: dict[str, dict[str, float]] = {
    model: {
        "input":        usd["input"]  * USD_TO_CNY,
        "input_cached": usd["input"]  * USD_TO_CNY * 0.10,
        "output":       usd["output"] * USD_TO_CNY,
    }
    for model, usd in _CLAUDE_USD.items()
}


def estimate_cost(
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int = 0,
) -> float:
    """
    Best-effort cost estimate. Returns 0.0 for unknown models so a missing
    price entry doesn't break the call path.
    """
    table = {"deepseek": DEEPSEEK_PRICES, "claude": CLAUDE_PRICES}.get(provider)
    if not table or model not in table:
        return 0.0

    prices = table[model]
    fresh_input = max(0, prompt_tokens - cached_tokens)
    cost = (
        fresh_input    * prices["input"]
        + cached_tokens * prices.get("input_cached", prices["input"])
        + completion_tokens * prices["output"]
    )
    return cost / 1_000_000.0


__all__ = ["estimate_cost", "DEEPSEEK_PRICES", "CLAUDE_PRICES", "USD_TO_CNY"]
