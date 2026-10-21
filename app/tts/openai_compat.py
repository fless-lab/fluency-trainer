"""Text-to-speech through an OpenAI compatible endpoint: OpenRouter or OpenAI.

POST {base}/audio/speech with a Bearer key and JSON {model, input, voice, response_format}. The answer is
the raw audio bytes. Request shape taken from the OpenRouter documentation; NOT checked against the live
service by me. A rejected request is raised as a TTSError with the provider's own message.
"""

from __future__ import annotations

import httpx

from .base import TTSError, TTSResult

BASES = {"openrouter": "https://openrouter.ai/api/v1", "openai": "https://api.openai.com/v1"}


class OpenAICompatTTS:
    def __init__(self, name: str, api_key: str, model: str, voice: str, base_url: str = ""):
        self.name, self.api_key, self.model, self.voice = name, api_key, model, voice
        self.base = (base_url or BASES[name]).rstrip("/")

    async def speak(self, text: str) -> TTSResult:
        body = {"model": self.model, "input": text, "voice": self.voice, "response_format": "mp3"}
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                r = await client.post(f"{self.base}/audio/speech", json=body,
                                      headers={"Authorization": f"Bearer {self.api_key}"})
        except httpx.HTTPError as exc:
            raise TTSError(f"{self.name} tts: network error: {exc}") from exc
        if r.status_code >= 400:
            raise TTSError(f"{self.name} tts: HTTP {r.status_code}: {r.text[:800]}")
        if not r.content:
            raise TTSError(f"{self.name} tts: empty audio in the response")
        return TTSResult(self.name, self.model, r.content, r.headers.get("content-type", "audio/mpeg").split(";")[0], len(text))
