"""Anthropic Messages API adapter.

Follows the public API shape: POST /v1/messages with x-api-key and anthropic-version headers, a model,
max_tokens, a system string and a list of messages; the answer is the concatenated text blocks of
content, and token counts are in usage.

NOT checked against the live service by me. If the provider rejects a request, its error body is
raised as an LLMError, shown in the session and written to the event log.
"""

from __future__ import annotations

import httpx

from .base import LLMError, LLMResult

DEFAULT_BASE = "https://api.anthropic.com"


class AnthropicLLM:
    name = "anthropic"

    def __init__(self, api_key: str, model: str, base_url: str = ""):
        self.api_key, self.model = api_key, model
        self.base = (base_url or DEFAULT_BASE).rstrip("/")

    async def complete(self, system: str, user: str, max_tokens: int) -> LLMResult:
        body = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                r = await client.post(f"{self.base}/v1/messages", json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise LLMError(f"anthropic: network error: {exc}") from exc
        if r.status_code >= 400:
            raise LLMError(f"anthropic: HTTP {r.status_code}: {r.text[:800]}")
        try:
            data = r.json()
            text = "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise LLMError(f"anthropic: unexpected response: {r.text[:800]}") from exc
        return LLMResult(self.name, self.model, text, data.get("usage") or {})
