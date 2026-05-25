"""
Provider-agnostic LLM interface.

The router talks to providers through this contract. Adding a new provider
(e.g. Qwen, GLM, local Ollama) means implementing this ABC — no business code
needs to change.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant"]


@dataclass
class Message:
    """A single chat message. `image` is a path/URL/bytes, provider-handled."""

    role: Role
    content: str
    image: str | bytes | None = None        # only honored by vision providers


@dataclass
class LLMResponse:
    text: str
    model: str
    provider: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_cny: float = 0.0
    raw: Any = None                          # original SDK response, for debugging
    meta: dict[str, Any] = field(default_factory=dict)


class LLMProvider(ABC):
    """
    Minimal contract every provider must implement.

    Providers should be cheap to instantiate (no network) — connection setup
    is lazy on first call.
    """

    name: str = "base"

    @abstractmethod
    def chat(
        self,
        messages: list[Message],
        *,
        model: str,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> LLMResponse: ...

    def vision(
        self,
        messages: list[Message],
        *,
        model: str,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> LLMResponse:
        """Default: providers without vision should raise NotImplementedError."""
        raise NotImplementedError(
            f"Provider {self.name!r} does not support vision input. "
            f"Configure a vision-capable provider in config.yaml -> llm.vision."
        )

    @abstractmethod
    def supports_vision(self) -> bool: ...

    # ---- introspection ------------------------------------------------------
    def health_check(self) -> bool:
        """
        Quick liveness check. Subclasses may override with an actual API ping;
        default returns True so missing creds are reported on first real call,
        not at import time.
        """
        return True


__all__ = ["LLMProvider", "LLMResponse", "Message", "Role"]
