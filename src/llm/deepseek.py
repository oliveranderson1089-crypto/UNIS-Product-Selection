"""
DeepSeek provider.

DeepSeek exposes an OpenAI-compatible API, so we reuse the official `openai`
SDK pointed at DeepSeek's base URL. This keeps the dependency surface small
and lets us swap to other OpenAI-compatible endpoints (Qwen, Moonshot…)
trivially in the future.
"""

from __future__ import annotations

from typing import Any

from .base import LLMProvider, LLMResponse, Message
from .pricing import estimate_cost


class DeepSeekProvider(LLMProvider):
    name = "deepseek"

    def __init__(self, api_key: str, base_url: str = "https://api.deepseek.com"):
        if not api_key:
            raise ValueError(
                "DeepSeek API key missing. Set DEEPSEEK_API_KEY in .env"
            )
        self._api_key = api_key
        self._base_url = base_url
        self._client: Any | None = None    # lazy

    # ---- public API ---------------------------------------------------------
    def chat(
        self,
        messages: list[Message],
        *,
        model: str,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> LLMResponse:
        client = self._lazy_client()
        payload = [{"role": m.role, "content": m.content} for m in messages]

        resp = client.chat.completions.create(
            model=model,
            messages=payload,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )

        choice = resp.choices[0]
        usage = resp.usage
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        cached = getattr(usage, "prompt_cache_hit_tokens", 0) or 0  # DeepSeek-specific

        return LLMResponse(
            text=(choice.message.content or "").strip(),
            model=model,
            provider=self.name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_cny=estimate_cost(
                self.name, model, prompt_tokens, completion_tokens, cached
            ),
            raw=resp,
            meta={"cached_tokens": cached, "finish_reason": choice.finish_reason},
        )

    def supports_vision(self) -> bool:
        return False  # DeepSeek-V3/R1 are text-only as of writing

    # ---- internals ----------------------------------------------------------
    def _lazy_client(self) -> Any:
        if self._client is None:
            # Local import keeps `openai` from being a hard dep for users who
            # only use Claude.
            from openai import OpenAI

            self._client = OpenAI(api_key=self._api_key, base_url=self._base_url)
        return self._client


__all__ = ["DeepSeekProvider"]
