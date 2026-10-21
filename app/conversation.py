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
    "You are a friendly person having a real spoken conversation with someone who is practising their English aloud. "
    "They already know English and want to speak it more fluently. Be a real conversation partner, not an interviewer. "
    "In each turn: first react to what they just said with something of your own, such as an opinion, a fact, a short "
    "example or a personal-style remark, and take a position when asked what you think. Ask a question only some of "
    "the time, never as your only content. If they ask you a question, answer it directly and honestly first, with your "
    "own view, then you may continue; never hand their question back. "
    "You have no live news feed unless a search tool is offered: on recent events say what you generally know, say plainly "
    "when you may be out of date, and never invent facts. If a search tool is offered, use it only for facts you are not "
    "sure of, never announce that you are searching, and keep the answer short anyway. On political topics give balanced, factual points and say that people disagree. "
    "Keep each reply short, natural spoken English: two to four sentences, under 55 words, no lists, no markdown, no emojis. "
    "Do not correct their English and do not comment on their mistakes: feedback comes later. "
    "Use everyday words, a little simpler than your own natural level, but never baby talk. "
    "Reply with the next thing the partner says and nothing else."
)


def brief_block(brief: str) -> str:
    return (f"Background you looked up before the conversation (use it naturally, do not read it out, it may be incomplete):\n{brief}\n\n"
            if brief else "")


def opening_prompt(scenario: str, brief: str = "") -> str:
    return (f"{brief_block(brief)}Scenario: {scenario}\n\nStart the conversation with one short, friendly line that begins the scenario "
            "and invites the person to speak. Reply with that line only.")




def turn_guidance(scenario: str, turns: list[dict[str, Any]]) -> str:
    """Rules that depend on the conversation so far, so the partner cannot fall into asking a question every turn."""
    notes = []
    last_user = next((t for t in reversed(turns) if t["role"] == "user"), None)
    last_partner = next((t for t in reversed(turns) if t["role"] == "partner"), None)
    if last_user and "?" in last_user["text"]:
        notes.append("The person just asked you something. Answer it directly with your own view or what you know, "
                     "before anything else, and do not turn it back on them.")
    interview = "interview" in scenario.lower()
    if not interview and last_partner and last_partner["text"].rstrip().endswith("?"):
        notes.append("Your previous line ended with a question, so this time do not ask one: share an opinion, a fact or a "
                     "short example that moves the topic forward, and end on a statement.")
    return " ".join(notes)


def reply_prompt(scenario: str, turns: list[dict[str, Any]], brief: str = "") -> str:
    lines = [brief_block(brief) + f"Scenario: {scenario}", "", "Conversation so far:"]
    for t in turns:
        who = "Partner" if t["role"] == "partner" else "Person"
        lines.append(f"{who}: {t['text']}")
    g = turn_guidance(scenario, turns)
    if g:
        lines += ["", g]
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


RESEARCH_SYSTEM = (
    "You prepare a conversation partner. Search the web for the current facts about the topic, then write a neutral "
    "brief of 120 to 200 words: the main facts, who the main actors are, dates, and the points on which people disagree. "
    "Say what is uncertain or changing. Plain sentences, no markdown, no links. Today's date is {today}."
)
