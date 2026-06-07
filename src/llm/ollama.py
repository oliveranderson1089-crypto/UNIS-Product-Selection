"""
Ollama provider — local open-source models via Ollama.

Ollama exposes an **OpenAI-compatible** API at ``http://localhost:11434/v1``,
so this provider reuses the exact same ``openai`` SDK the DeepSeek provider
uses — just pointed at the local server. No new dependency, no API key, and
no cost: the model runs on your own GPU/CPU, so ``cost_cny`` is always 0.

Prerequisites (operator side, not code):
  1. The Ollama app/server is running (it listens on :11434 by default).
  2. The model named in ``config.yaml`` is already pulled, e.g.
     ``ollama pull qwen2.5`` (stored as ``qwen2.5:latest``). The model name
     in config must match the pulled tag exactly, or the first call 404s.

To make the selection engine use a local model, point a task at this provider
in ``config.yaml`` (business code never names the provider directly):

    llm:
      chat:
        provider: ollama
        model: qwen2.5
"""

from __future__ import annotations

from typing import Any

from .base import LLMProvider, LLMResponse, Message


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, base_url: str = "http://localhost:11434/v1"):
        # Ollama ignores the API key, but the OpenAI SDK requires a non-empty
        # string, so the client is created with a placeholder in _lazy_client.
        self._base_url = base_url
        self._client: Any | None = None    # lazy — no network at construction

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
        usage = resp.usage     # Ollama returns an OpenAI-style usage block
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0

        return LLMResponse(
            text=(choice.message.content or "").strip(),
            model=model,
            provider=self.name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_cny=0.0,                       # local model — runs on your box
            raw=resp,
            meta={"finish_reason": choice.finish_reason},
        )

    def supports_vision(self) -> bool:
        # Some Ollama models (llava, qwen2.5-vl, llama3.2-vision) accept images,
        # but the vision path needs its own message-encoding work + testing.
        # Keep False until that's wired so we don't claim a capability we
        # haven't verified.
        return False

    def embed(
        self,
        texts: list[str],
        *,
        model: str,
        **kwargs: Any,
    ) -> list[list[float]]:
        """Embed a batch of texts via Ollama's OpenAI-compatible endpoint.

        Uses an embedding model (e.g. ``bge-m3``), which must be pulled
        separately from the chat model: ``ollama pull bge-m3``. Returns one
        vector per input, preserving order. Local model → no cost.
        """
        if not texts:
            return []
        client = self._lazy_client()
        resp = client.embeddings.create(model=model, input=texts, **kwargs)
        # OpenAI SDK guarantees data is returned in input order, but sort by
        # index defensively in case a future server build reorders.
        ordered = sorted(resp.data, key=lambda d: d.index)
        return [list(d.embedding) for d in ordered]

    # ---- introspection ------------------------------------------------------
    def health_check(self) -> bool:
        """Cheap liveness ping: list local models.

        Returns False (instead of raising) when the server is down or
        unreachable, so callers can detect a misconfigured/offline Ollama
        before issuing a real chat call.
        """
        try:
            self._lazy_client().models.list()
            return True
        except Exception:                       # noqa: BLE001
            return False

    # ---- internals ----------------------------------------------------------
    def _lazy_client(self) -> Any:
        if self._client is None:
            # Local import keeps `openai` out of the hard import path for users
            # who only run the rule-based (no-LLM) mode.
            from openai import OpenAI

            self._client = OpenAI(api_key="ollama", base_url=self._base_url)
        return self._client


__all__ = ["OllamaProvider"]
