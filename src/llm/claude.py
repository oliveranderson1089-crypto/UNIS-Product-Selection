"""
Claude provider.

Used today only for vision (image input). Text/reasoning interfaces are
implemented so when the budget allows you can switch by editing config.yaml:

  vision:
    provider: claude
    model: claude-3-5-haiku-20241022

  chat:           # ← change this block from deepseek to claude later
    provider: claude
    model: claude-sonnet-4
"""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any

from .base import LLMProvider, LLMResponse, Message
from .pricing import estimate_cost


class ClaudeProvider(LLMProvider):
    name = "claude"

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError(
                "Anthropic API key missing. Set ANTHROPIC_API_KEY in .env "
                "(leave blank to disable image input)."
            )
        self._api_key = api_key
        self._client: Any | None = None

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
        system, payload = self._split_system(messages)
        resp = self._lazy_client().messages.create(
            model=model,
            system=system or "",
            messages=[{"role": m.role, "content": m.content} for m in payload],
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        return self._wrap_response(resp, model)

    def vision(
        self,
        messages: list[Message],
        *,
        model: str,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> LLMResponse:
        """
        Send mixed text + image. The image goes on whichever user message
        carries it; if multiple messages have images, all are attached.
        """
        system, payload = self._split_system(messages)
        anthropic_messages = []
        for m in payload:
            blocks: list[dict[str, Any]] = []
            if m.image is not None:
                blocks.append(self._image_block(m.image))
            if m.content:
                blocks.append({"type": "text", "text": m.content})
            anthropic_messages.append({"role": m.role, "content": blocks})

        resp = self._lazy_client().messages.create(
            model=model,
            system=system or "",
            messages=anthropic_messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        return self._wrap_response(resp, model)

    def supports_vision(self) -> bool:
        return True

    # ---- internals ----------------------------------------------------------
    def _lazy_client(self) -> Any:
        if self._client is None:
            from anthropic import Anthropic

            self._client = Anthropic(api_key=self._api_key)
        return self._client

    @staticmethod
    def _split_system(messages: list[Message]) -> tuple[str | None, list[Message]]:
        """Anthropic API takes `system` as a top-level field, not a role."""
        system_parts = [m.content for m in messages if m.role == "system"]
        rest = [m for m in messages if m.role != "system"]
        system = "\n\n".join(system_parts) if system_parts else None
        return system, rest

    @staticmethod
    def _image_block(image: str | bytes) -> dict[str, Any]:
        """Build an Anthropic image content block from a path/URL/bytes."""
        if isinstance(image, bytes):
            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.standard_b64encode(image).decode("ascii"),
                },
            }
        if isinstance(image, str) and image.startswith(("http://", "https://")):
            return {"type": "image", "source": {"type": "url", "url": image}}

        path = Path(image)
        if not path.exists():
            raise FileNotFoundError(f"Image not found: {path}")
        media_type, _ = mimetypes.guess_type(path.name)
        media_type = media_type or "image/png"
        data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": data},
        }

    def _wrap_response(self, resp: Any, model: str) -> LLMResponse:
        text_parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
        usage = resp.usage
        return LLMResponse(
            text="\n".join(text_parts).strip(),
            model=model,
            provider=self.name,
            prompt_tokens=usage.input_tokens,
            completion_tokens=usage.output_tokens,
            cost_cny=estimate_cost(
                self.name,
                model,
                usage.input_tokens,
                usage.output_tokens,
                cached_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            ),
            raw=resp,
            meta={"stop_reason": resp.stop_reason},
        )


__all__ = ["ClaudeProvider"]
