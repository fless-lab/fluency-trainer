"""Patterns that come back across sessions, built from the stored feedback. No model call is needed.

Each feedback names up to three patterns with a short kebab-case tag. Counting how many different
sessions raised the same tag shows what keeps coming back. The tags come from a language model, so the
prompt asks it to reuse existing tags, but two tags for the same idea can still exist: treat the counts
as a guide, not a measurement.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

RECENT_DAYS = 14


def build_patterns(items: list[dict[str, Any]], now: datetime | None = None) -> dict[str, Any]:
    """items: one dict per session with feedback, oldest first: {session_id, date, content}."""
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=RECENT_DAYS)).date().isoformat()
    tags: dict[str, dict[str, Any]] = {}
    for it in items:
        for p in it["content"].get("patterns", []):
            t = tags.setdefault(p["tag"], {"tag": p["tag"], "sessions": set(), "recent": set(), "last_seen": "",
                                           "issue": "", "examples": []})
            t["sessions"].add(it["session_id"])
            if it["date"] >= cutoff:
                t["recent"].add(it["session_id"])
            t["last_seen"] = max(t["last_seen"], it["date"])
            t["issue"] = p.get("issue") or t["issue"]
            for ex in p.get("examples", []):
                if ex.get("quote_found", True):
                    t["examples"].append({"said": ex["said"], "better": ex["better"], "session_id": it["session_id"], "date": it["date"]})
    out = []
    for t in tags.values():
        out.append({"tag": t["tag"], "sessions": len(t["sessions"]), "recent_sessions": len(t["recent"]),
                    "last_seen": t["last_seen"], "issue": t["issue"], "examples": t["examples"][-3:]})
    out.sort(key=lambda t: (-t["sessions"], t["last_seen"]), reverse=False)
    out.sort(key=lambda t: (t["sessions"], t["last_seen"]), reverse=True)
    focus = next((t for t in out if t["recent_sessions"] >= 2), None)
    last_focus = [it["content"].get("focus_next") for it in items if it["content"].get("focus_next")][-3:]
    return {"patterns": out, "focus": focus, "recent_focus_lines": last_focus, "known_tags": [t["tag"] for t in out[:12]],
            "sessions_with_feedback": len(items)}
