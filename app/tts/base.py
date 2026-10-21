from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class TTSError(RuntimeError):
    """Raised when the voice provider fails. The message is logged and shown to the user."""


@dataclass
class TTSResult:
    provider: str
    model: str
    audio: bytes
    mime: str
    characters: int


class TTSProvider(Protocol):
    name: str
    model: str

    async def speak(self, text: str) -> TTSResult:
        """Return spoken audio for the text."""
        ...
