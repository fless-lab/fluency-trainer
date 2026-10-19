from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_STR = 4000


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _shrink(value: Any, depth: int = 0) -> Any:
    """Keep every event small enough to stay readable, without dropping keys."""
    if isinstance(value, str):
        return value if len(value) <= MAX_STR else value[:MAX_STR] + f"...[+{len(value) - MAX_STR} chars]"
    if isinstance(value, dict) and depth < 4:
        return {str(k): _shrink(v, depth + 1) for k, v in list(value.items())[:60]}
    if isinstance(value, (list, tuple)) and depth < 4:
        items = [_shrink(v, depth + 1) for v in value[:60]]
        if len(value) > 60:
            items.append(f"...[+{len(value) - 60} items]")
        return items
    return value


class EventLog:
    """Append-only JSON lines file. One line per event, never rewritten."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, type: str, session_id: str | None = None, **payload: Any) -> None:
        record = {"ts": utcnow(), "type": type, "session_id": session_id, "payload": _shrink(payload)}
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()
