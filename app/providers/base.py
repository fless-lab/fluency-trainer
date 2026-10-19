from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


class ProviderError(RuntimeError):
    """Raised when a speech provider fails. The message is stored with the session."""


@dataclass
class STTResult:
    provider: str
    job_id: str
    text: str
    words: list[dict[str, Any]]  # each: text, start (ms), end (ms), confidence
    audio_seconds: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class STTProvider(Protocol):
    name: str

    async def transcribe(self, audio_path: Path, mime: str, hint_seconds: float | None) -> STTResult:
        """Return a faithful transcript with word timestamps, keeping fillers and repetitions."""
        ...
