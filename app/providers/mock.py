"""Offline stand-in for a speech provider.

It invents a plausible transcript from the recording duration, so the whole flow can be
exercised without any key. Sessions made with it are labelled "mock" and are excluded
from the progress view by default. Never use it to judge your own fluency.
"""

from __future__ import annotations

import random
from pathlib import Path

from .base import STTResult

VOCAB = (
    "I think that the problem was really about time and so we decided to move forward with the plan "
    "because it seemed like the best option for everyone involved in the project and maybe later "
    "we could change it if something goes wrong but honestly it worked quite well in the end"
).split()


class MockSTT:
    name = "mock"

    async def transcribe(self, audio_path: Path, mime: str, hint_seconds: float | None) -> STTResult:
        seconds = float(hint_seconds or 30.0)
        rng = random.Random(audio_path.stem)
        words = []
        t = int(rng.uniform(300, 1500))
        end_ms = int(seconds * 1000)
        i = 0
        while t < end_ms - 400:
            if rng.random() < 0.06:
                text = "um"
            else:
                text = VOCAB[i % len(VOCAB)]
                i += 1
            dur = int(rng.uniform(180, 420))
            if t + dur > end_ms:
                break
            if text != "um" and rng.random() < 0.12:
                text += ","
            words.append({"text": text, "start": t, "end": t + dur, "confidence": round(rng.uniform(0.8, 0.99), 2)})
            roll = rng.random()
            if roll < 0.70:
                gap = rng.randint(30, 200)
            elif roll < 0.92:
                gap = rng.randint(260, 700)
            else:
                gap = rng.randint(1000, 1800)
            t += dur + gap
        text = " ".join(w["text"] for w in words)
        return STTResult(
            provider=self.name,
            job_id=f"mock-{audio_path.stem}",
            text=text,
            words=words,
            audio_seconds=seconds,
            raw={"mock": True, "words": words},
        )
