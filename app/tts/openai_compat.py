"""Text-to-speech through an OpenAI compatible endpoint: OpenRouter or OpenAI.

POST {base}/audio/speech with a Bearer key and JSON {model, input, voice, response_format}. The answer is
the raw audio bytes. Request shape taken from the OpenRouter documentation; NOT checked against the live
service by me. A rejected request is raised as a TTSError with the provider's own message.
"""

from __future__ import annotations

import io
import re
import wave

import httpx

from .base import TTSError, TTSResult

BASES = {"openrouter": "https://openrouter.ai/api/v1", "openai": "https://api.openai.com/v1"}


class OpenAICompatTTS:
    def __init__(self, name: str, api_key: str, model: str, voice: str, base_url: str = ""):
        self.name, self.api_key, self.model, self.voice = name, api_key, model, voice
        self.base = (base_url or BASES[name]).rstrip("/")
        self.format = ""

    async def _post(self, text: str, fmt: str) -> httpx.Response:
        body = {"model": self.model, "input": text, "voice": self.voice, "response_format": fmt}
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                return await client.post(f"{self.base}/audio/speech", json=body,
                                         headers={"Authorization": f"Bearer {self.api_key}"})
        except httpx.HTTPError as exc:
            raise TTSError(f"{self.name} tts: network error: {exc}") from exc

    async def speak(self, text: str) -> TTSResult:
        fmt = self.format or ("pcm" if "gemini" in self.model.lower() else "mp3")
        r = await self._post(text, fmt)
        if r.status_code == 400:
            # some models name the only format they accept in the error; use it once, and remember it
            m = re.search(r'only supports response_format\s*=?\s*\\?"(\w+)', r.text)
            if m and m.group(1) != fmt:
                fmt = m.group(1)
                self.format = fmt
                r = await self._post(text, fmt)
        if r.status_code >= 400:
            raise TTSError(f"{self.name} tts: HTTP {r.status_code}: {r.text[:800]}")
        if not r.content:
            raise TTSError(f"{self.name} tts: empty audio in the response")
        mime = r.headers.get("content-type", "audio/mpeg").split(";")[0].strip().lower()
        audio = r.content
        if fmt == "pcm" or mime in ("audio/pcm", "audio/l16", "audio/x-pcm", "application/octet-stream"):
            # raw 16-bit mono samples (24 kHz is the usual rate of speech models): wrap them so a browser can play them
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(audio)
            audio, mime = buf.getvalue(), "audio/wav"
        return TTSResult(self.name, self.model, audio, mime, len(text))
