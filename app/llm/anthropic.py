"""Anthropic Messages API adapter.

Follows the public API shape: POST /v1/messages with x-api-key and anthropic-version headers, a model,
max_tokens, a system string and a list of messages; the answer is the concatenated text blocks of
content, and token counts are in usage.

NOT checked against the live service by me. If the provider rejects a request, its error body is
raised as an LLMError, shown in the session and written to the event log.
"""

from __future__ import annotations

import json
from typing import AsyncIterator

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

    async def stream(self, system: str, user: str, max_tokens: int) -> AsyncIterator[str]:
        """Yield the answer piece by piece (server sent events: content_block_delta with delta.text)."""
        body = {"model": self.model, "max_tokens": max_tokens, "system": system, "stream": True,
                "messages": [{"role": "user", "content": user}]}
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream("POST", f"{self.base}/v1/messages", json=body, headers=headers) as r:
                    if r.status_code >= 400:
                        raise LLMError(f"anthropic: HTTP {r.status_code}: {(await r.aread()).decode('utf-8', 'replace')[:800]}")
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        try:
                            ev = json.loads(line[5:].strip())
                        except ValueError:
                            continue
                        if ev.get("type") == "content_block_delta" and ev.get("delta", {}).get("type") == "text_delta":
                            yield ev["delta"].get("text", "")
                        elif ev.get("type") == "error":
                            raise LLMError(f"anthropic: {ev.get('error')}")
        except httpx.HTTPError as exc:
            raise LLMError(f"anthropic: network error: {exc}") from exc
