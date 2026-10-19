from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class LLMError(RuntimeError):
    """Raised when the language model provider fails. The message is logged and shown to the user."""


@dataclass
class LLMResult:
    provider: str
    model: str
    text: str
    usage: dict[str, Any] = field(default_factory=dict)


class LLMProvider(Protocol):
    name: str
    model: str

    async def complete(self, system: str, user: str, max_tokens: int) -> LLMResult:
        """Return the model's text answer to a single system plus user prompt."""
        ...
