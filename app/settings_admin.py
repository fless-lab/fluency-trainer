"""The settings screen: which options exist, reading and changing them in the .env file, and checking a key works.

The .env file stays the single place where the configuration lives. Saving rewrites only the lines that changed and keeps the
comments and the order. Secret values are never sent to the browser: it only learns whether one is set and its last four
characters. A change takes effect when the app is restarted.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    group: str
    kind: str = "text"  # text | secret | select | number
    help: str = ""
    options: tuple[str, ...] = ()
    low: int | None = None
    high: int | None = None
    placeholder: str = ""


GROUPS = [
    ("stt", "Speech to text", "Turns your recordings into words with timings. AssemblyAI is used for real transcripts."),
    ("llm", "Language model", "Writes the feedback and the lessons, and plays the conversation partner."),
    ("voice", "Voice of the partner", "What you hear: the partner's replies and every click-to-hear word."),
    ("pron", "Pronunciation", "Scores your sounds from the audio, and an audio model that listens to your weakest words."),
    ("limits", "Daily limits", "Safety caps so a bug or a loop cannot spend your credits."),
]

FIELDS: list[Field] = [
    Field("STT_PROVIDER", "Provider", "stt", "select", "mock gives invented transcripts, to try the app without a key.", ("assemblyai", "mock")),
    Field("ASSEMBLYAI_API_KEY", "AssemblyAI key", "stt", "secret"),
    Field("ASSEMBLYAI_SPEECH_MODELS", "Speech models", "stt", "text", "Optional, comma separated. Empty uses the provider default."),
    Field("LLM_PROVIDER", "Provider", "llm", "select", "none turns the feedback off. mock gives placeholder answers.", ("none", "anthropic", "openrouter", "mock")),
    Field("LLM_API_KEY", "API key", "llm", "secret"),
    Field("LLM_MODEL", "Model for feedback and lessons", "llm", "text", "The exact model name from your provider, for example claude-sonnet-5-5."),
    Field("LLM_CHAT_MODEL", "Model for the conversation", "llm", "text", "A fast, cheap model answers sooner. Empty uses the model above."),
    Field("LLM_BASE_URL", "Other endpoint", "llm", "text", "Optional: only to point at another compatible service."),
    Field("TTS_PROVIDER", "Provider", "voice", "select", "none uses the browser's own voice.", ("none", "openrouter", "openai", "mock")),
    Field("TTS_API_KEY", "API key", "voice", "secret", "Empty reuses the language model key when the provider is openrouter."),
    Field("TTS_MODEL", "Voice model", "voice", "text", "For example google/gemini-3.8-flash-lite-tts."),
    Field("TTS_VOICE", "Voice name", "voice", "text", "For example Kore or alloy."),
    Field("AZURE_SPEECH_KEY", "Azure Speech key", "pron", "secret", "Leave empty to turn the pronunciation scores off."),
    Field("AZURE_SPEECH_REGION", "Azure region", "pron", "text", "Lower case, no space, for example norwayeast.", placeholder="norwayeast"),
    Field("PRONUNCIATION_CONCURRENCY", "Requests at once", "pron", "number", "Default 1, right for the free Azure tier. 5 to 10 on a paid one.", low=1, high=20, placeholder="1"),
    Field("AUDIO_COACH_MODEL", "Audio model", "pron", "text", "Listens to your weakest words. Empty turns it off. Default google/gemini-3.1-flash-lite."),
    Field("AUDIO_COACH_KEY", "Audio model key", "pron", "secret", "Empty reuses the voice or language model key."),
    Field("NATIVE_LANGUAGE", "Your first language", "pron", "text", "In English, for example French. The lessons compare English sounds with it."),
    Field("COACH_MAX_PER_DAY", "Feedback and lessons per day", "limits", "number", "Default 30.", low=1, high=1000, placeholder="30"),
    Field("CHAT_MAX_PER_DAY", "Partner replies per day", "limits", "number", "Default 400.", low=1, high=10000, placeholder="400"),
    Field("TTS_MAX_CHARS_PER_DAY", "Spoken characters per day", "limits", "number", "Default 60000. A word you already heard again is free.", low=100, high=2000000, placeholder="60000"),
]
BY_KEY = {f.key: f for f in FIELDS}
LINE = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*=(.*)$")
COMMENTED = re.compile(r"^\s*#\s*([A-Z][A-Z0-9_]*)\s*=(.*)$")


def read_env(path: Path) -> dict[str, str]:
    """Values written in the file (not the process environment)."""
    out: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = LINE.match(line)
            if m:
                out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


def write_env(path: Path, updates: dict[str, str]) -> None:
    """Change the given keys in place, keeping every other line. A key that is only present as a comment is switched on
    where the comment is; a new key goes at the end. The write is atomic and keeps the file's permissions."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    pending = dict(updates)
    for i, line in enumerate(lines):
        m = LINE.match(line)
        if m and m.group(1) in pending:
            lines[i] = f"{m.group(1)}={pending.pop(m.group(1))}"
    for i, line in enumerate(lines):
        m = COMMENTED.match(line)
        if m and m.group(1) in pending:
            lines[i] = f"{m.group(1)}={pending.pop(m.group(1))}"
    if pending:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("# Added from the settings screen")
        lines += [f"{k}={v}" for k, v in pending.items()]
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, path.stat().st_mode & 0o777 if path.is_file() else 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def mask(value: str) -> str:
    return "" if not value else ("…" + value[-4:] if len(value) > 8 else "set")


def describe(file_values: dict[str, str], running: dict[str, str]) -> list[dict[str, Any]]:
    """What the screen shows. A secret's value is never included; `pending` means the file differs from what is running."""
    out = []
    for key, title, blurb in GROUPS:
        fields = []
        for f in (x for x in FIELDS if x.group == key):
            value = file_values.get(f.key, "")
            item: dict[str, Any] = {"key": f.key, "label": f.label, "kind": f.kind, "help": f.help, "options": list(f.options),
                                    "low": f.low, "high": f.high, "placeholder": f.placeholder,
                                    "pending": value != (running.get(f.key) or "")}
            if f.kind == "secret":
                item.update(set=bool(value), hint=mask(value), value="")
            else:
                item["value"] = value
            fields.append(item)
        out.append({"id": key, "title": title, "blurb": blurb, "fields": fields})
    return out


def clean_updates(values: dict[str, Any], clear: list[str]) -> dict[str, str]:
    """Check what the browser sent. An empty secret means "keep the one I have"; only `clear` removes it."""
    out: dict[str, str] = {}
    for key, raw in values.items():
        f = BY_KEY.get(key)
        if f is None:
            raise ValueError(f"unknown setting {key}")
        v = str(raw if raw is not None else "").strip()
        if "\n" in v or "\r" in v:
            raise ValueError(f"{f.label}: one line only")
        if f.kind == "secret" and not v:
            continue
        if f.kind == "select" and v not in f.options:
            raise ValueError(f"{f.label}: choose one of {', '.join(f.options)}")
        if f.kind == "number" and v:
            if not v.isdigit() or not (f.low or 0) <= int(v) <= (f.high or 10**9):
                raise ValueError(f"{f.label}: a whole number from {f.low} to {f.high}")
        out[key] = v
    for key in clear:
        f = BY_KEY.get(key)
        if f is None or f.kind != "secret":
            raise ValueError(f"cannot clear {key}")
        out[key] = ""
    return out
