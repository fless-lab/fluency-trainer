"""Fluency metrics computed from word level timestamps.

Everything here is pure: a list of words in, numbers out. The raw transcript is
always stored, so these definitions can change and sessions can be recomputed.

Definitions follow the research notes behind this project:
- silent pause threshold 250 ms (the usual convention in recent utterance fluency work)
- "long" pause: more than 1 second (a practical marker chosen for this project, not a literature standard)
- mid-clause vs clause-boundary pauses are classified with a HEURISTIC (punctuation in the
  transcript plus a short list of conjunctions). It is an approximation, not a syntactic parse.
- syllables are estimated with a vowel-group heuristic, so articulation rate is approximate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

METRICS_VERSION = "1"
PAUSE_MS = 250
LONG_PAUSE_MS = 1000

FILLERS = {"um", "umm", "uh", "uhh", "er", "erm", "uhm", "ehm", "hmm", "hm", "mm", "mmm"}

CLAUSE_STARTERS = {
    "and", "but", "or", "so", "because", "although", "though", "while", "whereas",
    "which", "who", "whom", "whose", "that", "when", "if", "unless", "until",
    "however", "therefore",
}

BOUNDARY_PUNCT = (".", ",", ";", ":", "?", "!", "…")

_NORM_RE = re.compile(r"[^a-z0-9']+")


@dataclass(frozen=True)
class Word:
    text: str
    start: int  # milliseconds
    end: int  # milliseconds
    confidence: float | None = None


def norm(token: str) -> str:
    return _NORM_RE.sub("", token.lower().replace("’", "'"))


def is_filler(token: str) -> bool:
    return norm(token) in FILLERS


def count_syllables(token: str) -> int:
    raw = token.lower()
    letters = re.sub(r"[^a-z]", "", raw)
    if not letters:
        return 2 if re.search(r"\d", raw) else 0
    n = len(re.findall(r"[aeiouy]+", letters))
    if letters.endswith("e") and not letters.endswith(("le", "ee", "ye")) and n > 1:
        n -= 1
    if letters.endswith("ed") and n > 1 and not letters.endswith(("ted", "ded")):
        n -= 1
    return max(1, n)


def _coerce(words: Iterable[dict[str, Any]]) -> list[Word]:
    out: list[Word] = []
    for w in words:
        try:
            start, end = int(w["start"]), int(w["end"])
            text = str(w["text"])
        except (KeyError, TypeError, ValueError):
            continue
        if end < start or not text.strip():
            continue
        conf = w.get("confidence")
        out.append(Word(text=text, start=start, end=end, confidence=float(conf) if conf is not None else None))
    out.sort(key=lambda x: (x.start, x.end))
    return out


def _round(value: float, digits: int = 3) -> float:
    return round(float(value), digits)


def compute_metrics(words: Iterable[dict[str, Any]]) -> dict[str, Any]:
    ws = _coerce(words)
    result: dict[str, Any] = {
        "version": METRICS_VERSION,
        "definitions": {
            "pause_ms": PAUSE_MS,
            "long_pause_ms": LONG_PAUSE_MS,
            "classification": "heuristic",
            "syllables": "heuristic",
        },
        "n_tokens": len(ws),
        "flags": [],
        "values": {},
        "pauses": [],
    }
    if len(ws) < 2:
        result["flags"].append("too_short")
        return result

    content = [w for w in ws if not is_filler(w.text)]
    fillers = [w for w in ws if is_filler(w.text)]
    n_content = len(content)
    if n_content < 2:
        result["flags"].append("too_short")
        return result

    span_ms = ws[-1].end - ws[0].start
    if span_ms <= 0:
        result["flags"].append("bad_timestamps")
        return result

    pauses: list[dict[str, Any]] = []
    run_words: list[int] = []
    current_run = 0
    for i, w in enumerate(ws):
        if i > 0:
            prev = ws[i - 1]
            gap = w.start - prev.end
            if gap >= PAUSE_MS:
                after_punct = prev.text.rstrip().endswith(BOUNDARY_PUNCT)
                before_conj = norm(w.text) in CLAUSE_STARTERS
                kind = "boundary" if (after_punct or before_conj) else "mid_clause"
                pauses.append(
                    {
                        "start_ms": prev.end,
                        "end_ms": w.start,
                        "duration_ms": gap,
                        "type": kind,
                        "long": gap > LONG_PAUSE_MS,
                        "adjacent_filler": is_filler(prev.text) or is_filler(w.text),
                        "before_word_index": i,
                    }
                )
                if current_run:
                    run_words.append(current_run)
                current_run = 0
        if not is_filler(w.text):
            current_run += 1
    if current_run:
        run_words.append(current_run)

    pause_total_ms = sum(p["duration_ms"] for p in pauses)
    phonation_ms = max(span_ms - pause_total_ms, 1)
    n_mid = sum(1 for p in pauses if p["type"] == "mid_clause")
    n_boundary = len(pauses) - n_mid
    n_long = sum(1 for p in pauses if p["long"])

    syllables = sum(count_syllables(w.text) for w in content)

    # repetitions: immediate word repeats and immediate two word repeats
    seq = [norm(w.text) for w in content]
    repetitions = 0
    for i in range(1, len(seq)):
        if seq[i] and seq[i] == seq[i - 1]:
            repetitions += 1
    for i in range(3, len(seq)):
        if seq[i] and seq[i - 1] and seq[i - 1:i + 1] == seq[i - 3:i - 1] and seq[i] != seq[i - 1]:
            repetitions += 1

    per100 = 100.0 / n_content
    confidences = [w.confidence for w in ws if w.confidence is not None]
    mean_pause = (pause_total_ms / len(pauses)) if pauses else 0.0

    values = {
        "words_per_minute": _round(n_content / (span_ms / 60000.0), 1),
        "articulation_rate_syll_per_s": _round(syllables / (phonation_ms / 1000.0), 2),
        "phonation_ratio": _round(phonation_ms / span_ms),
        "pauses_per_100_words": _round(len(pauses) * per100, 2),
        "mid_clause_pauses_per_100_words": _round(n_mid * per100, 2),
        "boundary_pauses_per_100_words": _round(n_boundary * per100, 2),
        "long_pauses_per_100_words": _round(n_long * per100, 2),
        "mean_pause_ms": _round(mean_pause, 0),
        "max_pause_ms": float(max((p["duration_ms"] for p in pauses), default=0)),
        "mean_length_of_run_words": _round(sum(run_words) / len(run_words), 2) if run_words else 0.0,
        "fillers_per_100_words": _round(len(fillers) * per100, 2),
        "repetitions_per_100_words": _round(repetitions * per100, 2),
        "onset_latency_s": _round(ws[0].start / 1000.0, 2),
        "speech_span_s": _round(span_ms / 1000.0, 1),
        "content_words": float(n_content),
        "fillers": float(len(fillers)),
        "pauses": float(len(pauses)),
        "mid_clause_pauses": float(n_mid),
        "long_pauses": float(n_long),
        "mean_word_confidence": _round(sum(confidences) / len(confidences), 3) if confidences else 0.0,
    }
    if span_ms < 30_000:
        result["flags"].append("short_sample")
    if not fillers:
        result["flags"].append("no_fillers_found")  # may mean the provider removed them
    result["values"] = values
    result["pauses"] = pauses
    return result
