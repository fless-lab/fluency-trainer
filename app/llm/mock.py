"""Offline stand-in for a language model: returns a fixed, valid answer so the flow can be tried without a key.

It quotes the first words of the transcript, so the "said" check passes, but its advice is generic and
means nothing. Never use it to judge your own English.
"""

from __future__ import annotations

import json
import re

from .base import LLMResult


class MockLLM:
    name = "mock"
    model = "mock"

    async def complete(self, system: str, user: str, max_tokens: int) -> LLMResult:
        if "Write the Partner's next line" in user or "Start the conversation with" in user:
            return LLMResult("mock", "mock", "Mock partner: tell me more about that.", {"input_tokens": 0, "output_tokens": 0})
        if "<intent>" in user:
            im = re.search(r"<intent>(.*?)</intent>", user, re.S)
            answer = {"options": [{"english": "Mock: " + (im.group(1).strip() if im else ""), "note": "Placeholder, not real advice."}],
                      "tip": ""}
            return LLMResult("mock", "mock", json.dumps(answer), {"input_tokens": 0, "output_tokens": 0})
        m = re.search(r"<transcript>\n(.*?)\n</transcript>", user, re.S)
        words = (m.group(1) if m else "").split()
        quote = " ".join(words[:6]) or "no transcript"
        answer = {
            "summary": "Mock feedback: this is a placeholder, not a real analysis.",
            "strengths": ["Mock strength."],
            "patterns": [{
                "tag": "mock-pattern", "issue": "Mock issue.", "why": "Mock reason.",
                "examples": [{"said": quote, "better": quote}],
            }],
            "phrases": [{"phrase": "What I mean is", "use": "to restate an idea more clearly"}],
            "focus_next": "Mock focus.",
        }
        return LLMResult("mock", "mock", json.dumps(answer), {"input_tokens": 0, "output_tokens": 0})

    async def stream(self, system: str, user: str, max_tokens: int):
        r = await self.complete(system, user, max_tokens)
        words = r.text.split(" ")
        for i, w in enumerate(words):
            yield w + (" " if i < len(words) - 1 else "")
