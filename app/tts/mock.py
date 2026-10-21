"""Offline stand-in: returns a short silent WAV so the flow can be tried without a key."""

from __future__ import annotations

import io
import wave

from .base import TTSResult


class MockTTS:
    name = "mock"
    model = "mock"

    async def speak(self, text: str) -> TTSResult:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(8000)
            w.writeframes(b"\x00\x00" * 4000)
        return TTSResult("mock", "mock", buf.getvalue(), "audio/wav", len(text))
