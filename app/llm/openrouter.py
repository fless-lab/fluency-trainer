"""OpenRouter adapter (OpenAI compatible chat completions).

Follows the public API shape: POST {base}/chat/completions with a Bearer key, a model name and a
list of messages; the answer is in choices[0].message.content and token counts in usage.

NOT checked against the live service by me. If the provider rejects a request, its error body is
raised as an LLMError, shown in the session and written to the event log.
"""

from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from .base import LLMError, LLMResult

DEFAULT_BASE = "https://openrouter.ai/api/v1"


class OpenRouterLLM:
    name = "openrouter"

    def __init__(self, api_key: str, model: str, base_url: str = ""):
        self.api_key, self.model = api_key, model
        self.base = (base_url or DEFAULT_BASE).rstrip("/")

    async def complete(self, system: str, user: str, max_tokens: int) -> LLMResult:
        body = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                r = await client.post(f"{self.base}/chat/completions", json=body,
                                      headers={"Authorization": f"Bearer {self.api_key}"})
        except httpx.HTTPError as exc:
            raise LLMError(f"openrouter: network error: {exc}") from exc
        if r.status_code >= 400:
            raise LLMError(f"openrouter: HTTP {r.status_code}: {r.text[:800]}")
        try:
            data = r.json()
            text = data["choices"][0]["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"openrouter: unexpected response: {r.text[:800]}") from exc
        return LLMResult(self.name, self.model, text, data.get("usage") or {})

    async def stream(self, system: str, user: str, max_tokens: int, web: bool = False) -> AsyncIterator[str]:
        """Yield the answer piece by piece (server sent events with choices[0].delta.content, ending with [DONE])."""
        body = {"model": self.model, "max_tokens": max_tokens, "stream": True,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream("POST", f"{self.base}/chat/completions", json=body,
                                         headers={"Authorization": f"Bearer {self.api_key}"}) as r:
                    if r.status_code >= 400:
                        raise LLMError(f"openrouter: HTTP {r.status_code}: {(await r.aread()).decode('utf-8', 'replace')[:800]}")
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            break
                        try:
                            ev = json.loads(payload)
                            piece = (ev.get("choices") or [{}])[0].get("delta", {}).get("content") or ""
                        except (ValueError, AttributeError):
                            continue
                        if piece:
                            yield piece
        except httpx.HTTPError as exc:
            raise LLMError(f"openrouter: network error: {exc}") from exc
