"""Voice conversation with a partner. Pure logic: the prompt, and the stitching of the user's turns.

Only the user's own speech goes into the transcript used for metrics and feedback. The turns are placed
one after the other with a small constant gap, so that the time the partner spent talking and the time
the user waited for a reply are not counted as pauses in the user's speech. The wait before each turn is
kept separately as the response delay (see delays()).
"""

from __future__ import annotations

from typing import Any

CONV_VERSION = "1"
JOIN_GAP_MS = 200  # below the 250 ms pause threshold, so joining two turns never creates a pause
MAX_PARTNER_WORDS = 60

SYSTEM = (
    "You are a friendly conversation partner helping someone practise speaking English aloud. "
    "The person already knows English and wants to speak it more fluently. "
    "Keep each reply short: one to three sentences, under 45 words, natural spoken English, no lists, no markdown, "
    "no emojis. Ask at most one question at a time and let the person do most of the talking. "
    "Do not correct their English and do not comment on their mistakes during the conversation: feedback comes later. "
    "Use everyday words, a little simpler than your own natural level, but never baby talk. "
    "If their last turn was short or unclear, ask about it or invite them to say more. "
    "Reply with the next thing the partner says and nothing else."
)


def opening_prompt(scenario: str) -> str:
    return (f"Scenario: {scenario}\n\nStart the conversation with one short, friendly line that begins the scenario "
            "and invites the person to speak. Reply with that line only.")


def reply_prompt(scenario: str, turns: list[dict[str, Any]]) -> str:
    lines = [f"Scenario: {scenario}", "", "Conversation so far:"]
    for t in turns:
        who = "Partner" if t["role"] == "partner" else "Person"
        lines.append(f"{who}: {t['text']}")
    lines += ["", "Write the Partner's next line."]
    return "\n".join(lines)


def clean_reply(text: str) -> str:
    t = " ".join((text or "").replace("*", "").split()).strip()
    if t.lower().startswith("partner:"):
        t = t[8:].strip()
    words = t.split()
    if len(words) > MAX_PARTNER_WORDS:
        t = " ".join(words[:MAX_PARTNER_WORDS]).rstrip(",;") + "."
    return t


def stitch(user_turns: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str, float]:
    """user_turns: dicts with 'words' (ms relative to the start of that recording). Returns words, text, seconds."""
    out: list[dict[str, Any]] = []
    cursor = 0.0
    for t in user_turns:
        ws = t.get("words") or []
        if not ws:
            continue
        shift = cursor - ws[0]["start"]
        for w in ws:
            out.append({**w, "start": w["start"] + shift, "end": w["end"] + shift})
        cursor = out[-1]["end"] + JOIN_GAP_MS
    text = " ".join(w["text"] for w in out)
    seconds = (out[-1]["end"] / 1000.0) if out else 0.0
    return out, text, seconds


def delays(turns: list[dict[str, Any]]) -> list[int]:
    """Response delay of each user turn after the partner finished, in ms (None values skipped)."""
    return [t["latency_ms"] for t in turns if t["role"] == "user" and t.get("latency_ms") is not None]


MIN_SENTENCE_CHARS = 14


def split_ready(buffer: str, final: bool = False) -> tuple[list[str], str]:
    """Cut complete sentences off the front of a growing buffer.

    A sentence ends at . ! or ? followed by a space (or the end of the text when final). Pieces shorter than
    MIN_SENTENCE_CHARS are kept and joined to the next one, so the voice never speaks a lone "Oh." The first
    sentence is released as early as possible, since that is what starts the voice.
    """
    out: list[str] = []
    start, i = 0, 0
    while i < len(buffer):
        if buffer[i] in ".!?" and (i + 1 == len(buffer) and final or (i + 1 < len(buffer) and buffer[i + 1] in " \n")):
            piece = buffer[start:i + 1].strip()
            if len(piece) >= MIN_SENTENCE_CHARS:
                out.append(piece)
                start = i + 1
        i += 1
    rest = buffer[start:]
    if final and rest.strip():
        if out and len(rest.strip()) < MIN_SENTENCE_CHARS:
            out[-1] = out[-1] + " " + rest.strip()
        else:
            out.append(rest.strip())
        rest = ""
    return out, rest.lstrip()
