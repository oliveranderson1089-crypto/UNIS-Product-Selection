"""
Task-based LLM router.

Business code calls `router.call("chat", messages=...)` — never names a
provider directly. Switching providers is a one-line edit in config.yaml.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import TYPE_CHECKING

from ..config import AppConfig, get_config
from .base import LLMProvider, LLMResponse, Message

if TYPE_CHECKING:
    from .claude import ClaudeProvider
    from .deepseek import DeepSeekProvider

logger = logging.getLogger(__name__)


class ProviderNotConfigured(RuntimeError):
    """Raised when a task is routed to a provider whose creds are missing."""


class LLMRouter:
    """
    Holds provider instances and dispatches calls by task name.

    Providers are constructed lazily — a missing Anthropic key will not break
    startup, only the first vision call.
    """

    def __init__(self, config: AppConfig):
        self._config = config
        self._providers: dict[str, LLMProvider] = {}

    # ---- public API ---------------------------------------------------------
    def call(self, task: str, messages: list[Message], **kwargs) -> LLMResponse:
        """
        Route a request to the provider+model configured for `task`.

        Falls back to the task named in config.llm.fallback[task] if the
        primary call raises (network error, missing key, etc.).
        """
        task_cfg = self._config.task(task)
        try:
            provider = self._provider(task_cfg.provider)
        except ProviderNotConfigured as exc:
            return self._try_fallback(task, exc, messages, **kwargs)

        try:
            if task == "vision":
                return provider.vision(
                    messages,
                    model=task_cfg.model,
                    temperature=task_cfg.temperature,
                    max_tokens=task_cfg.max_tokens,
                    **kwargs,
                )
            return provider.chat(
                messages,
                model=task_cfg.model,
                temperature=task_cfg.temperature,
                max_tokens=task_cfg.max_tokens,
                **kwargs,
            )
        except Exception as exc:                               # noqa: BLE001
            logger.warning(
                "LLM call failed for task=%s provider=%s model=%s: %s",
                task, task_cfg.provider, task_cfg.model, exc,
            )
            return self._try_fallback(task, exc, messages, **kwargs)

    # ---- internals ----------------------------------------------------------
    def _provider(self, name: str) -> LLMProvider:
        if name not in self._providers:
            self._providers[name] = self._build(name)
        return self._providers[name]

    def _build(self, name: str) -> LLMProvider:
        secrets = self._config.secrets
        if name == "deepseek":
            if not secrets.deepseek_api_key:
                raise ProviderNotConfigured(
                    "DEEPSEEK_API_KEY missing in .env — DeepSeek calls disabled."
                )
            from .deepseek import DeepSeekProvider
            return DeepSeekProvider(
                api_key=secrets.deepseek_api_key,
                base_url=secrets.deepseek_base_url,
            )
        if name == "claude":
            if not secrets.anthropic_api_key:
                raise ProviderNotConfigured(
                    "ANTHROPIC_API_KEY missing in .env — Claude calls disabled. "
                    "Set the key to enable image input."
                )
            from .claude import ClaudeProvider
            return ClaudeProvider(api_key=secrets.anthropic_api_key)
        raise ValueError(f"Unknown LLM provider: {name!r}")

    def _try_fallback(
        self,
        task: str,
        exc: Exception,
        messages: list[Message],
        **kwargs,
    ) -> LLMResponse:
        fallback_task = self._config.llm.fallback.get(task)
        if not fallback_task or fallback_task == task:
            raise exc
        logger.info("Falling back: task=%s -> task=%s", task, fallback_task)
        return self.call(fallback_task, messages, **kwargs)


@lru_cache(maxsize=1)
def get_router() -> LLMRouter:
    """Process-wide singleton router."""
    return LLMRouter(get_config())


__all__ = ["LLMRouter", "ProviderNotConfigured", "get_router"]
