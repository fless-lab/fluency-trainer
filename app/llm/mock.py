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
        if "pronunciation teacher" in system:
            words = re.findall(r'^- "([^"]+)" scored', user, re.M)
            lesson = {"focus": [{"sound": "θ", "label": "the th in think", "explain": "Mock lesson: a placeholder, not real advice.",
                                 "how": "Put your tongue between your teeth.", "pairs": [["think", "sink"]], "your_words": words[:2],
                                 "sentences": ["I think so.", "Thank you.", "Three things."]}], "advice": "Mock: practise once a day."}
            return LLMResult("mock", "mock", json.dumps(lesson), {"input_tokens": 0, "output_tokens": 0})
        m = re.search(r"<transcript>\n(.*?)\n</transcript>", user, re.S)
        text = re.sub(r"\[(?:pause|unsure) [^\]]*\]", " ", m.group(1) if m else "")  # whole markers out, never cut in half
        words = text.split()
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

    async def stream(self, system: str, user: str, max_tokens: int, web: bool = False):
        r = await self.complete(system, user, max_tokens)
        words = r.text.split(" ")
        for i, w in enumerate(words):
            yield w + (" " if i < len(words) - 1 else "")

    async def research(self, topic: str, today: str, max_searches: int = 3) -> str:
        return f"Mock brief about {topic}. Placeholder, not real facts."
