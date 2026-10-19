from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .eventlog import utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    local_date TEXT NOT NULL,
    kind TEXT NOT NULL,
    checkpoint TEXT,
    topic_id TEXT,
    topic_text TEXT,
    is_repeat INTEGER NOT NULL DEFAULT 0,
    planned_seconds INTEGER,
    prep_seconds INTEGER,
    audio_path TEXT,
    audio_mime TEXT,
    audio_bytes INTEGER,
    client_seconds REAL,
    audio_seconds REAL,
    status TEXT NOT NULL,
    provider TEXT,
    provider_job_id TEXT,
    transcript_path TEXT,
    transcript_text TEXT,
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    self_rating INTEGER,
    notes TEXT,
    client_info TEXT,
    group_id TEXT,
    drill_round INTEGER
);
CREATE TABLE IF NOT EXISTS metrics (
    session_id TEXT NOT NULL REFERENCES sessions(id),
    name TEXT NOT NULL,
    value REAL NOT NULL,
    version TEXT NOT NULL,
    computed_at TEXT NOT NULL,
    PRIMARY KEY (session_id, name, version)
);
CREATE TABLE IF NOT EXISTS ratings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    rater TEXT NOT NULL,
    score INTEGER NOT NULL,
    comment TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_checkpoint ON sessions(checkpoint);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
"""

SESSION_COLUMNS = {
    "audio_path", "audio_mime", "audio_bytes", "client_seconds", "audio_seconds", "status",
    "provider", "provider_job_id", "transcript_path", "transcript_text", "error", "attempts",
    "self_rating", "notes",
}


class DB:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self.conn() as c:
            c.executescript(SCHEMA)
            # databases created by earlier versions lack the drill columns: add them in place
            have = {r["name"] for r in c.execute("PRAGMA table_info(sessions)").fetchall()}
            for name, ddl in (("group_id", "TEXT"), ("drill_round", "INTEGER")):
                if name not in have:
                    c.execute(f"ALTER TABLE sessions ADD COLUMN {name} {ddl}")
            c.execute("CREATE INDEX IF NOT EXISTS idx_sessions_group ON sessions(group_id)")

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            c = sqlite3.connect(self.path, timeout=30)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA foreign_keys=ON")
            try:
                yield c
                c.commit()
            except Exception:
                c.rollback()
                raise
            finally:
                c.close()

    # sessions -------------------------------------------------------------
    def create_session(self, row: dict[str, Any]) -> None:
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        with self.conn() as c:
            c.execute(f"INSERT INTO sessions ({cols}) VALUES ({marks})", list(row.values()))

    def get_session(self, sid: str) -> dict[str, Any] | None:
        with self.conn() as c:
            r = c.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
        return dict(r) if r else None

    def update_session(self, sid: str, **fields: Any) -> None:
        bad = set(fields) - SESSION_COLUMNS
        if bad:
            raise ValueError(f"unknown session columns: {sorted(bad)}")
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self.conn() as c:
            c.execute(f"UPDATE sessions SET {sets} WHERE id = ?", [*fields.values(), sid])

    def list_sessions(self, status: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        q = "SELECT * FROM sessions"
        args: list[Any] = []
        if status:
            q += " WHERE status IN (%s)" % ", ".join("?" for _ in status)
            args = list(status)
        q += " ORDER BY created_at DESC"
        with self.conn() as c:
            return [dict(r) for r in c.execute(q, args).fetchall()]

    # metrics --------------------------------------------------------------
    def save_metrics(self, sid: str, version: str, values: dict[str, float]) -> None:
        now = utcnow()
        with self.conn() as c:
            c.executemany(
                "INSERT OR REPLACE INTO metrics (session_id, name, value, version, computed_at) VALUES (?,?,?,?,?)",
                [(sid, k, float(v), version, now) for k, v in values.items()],
            )

    def metrics_for(self, sid: str, version: str) -> dict[str, float]:
        with self.conn() as c:
            rows = c.execute(
                "SELECT name, value FROM metrics WHERE session_id = ? AND version = ?", (sid, version)
            ).fetchall()
        return {r["name"]: r["value"] for r in rows}

    def metrics_versions(self, sid: str) -> list[str]:
        with self.conn() as c:
            rows = c.execute("SELECT DISTINCT version FROM metrics WHERE session_id = ?", (sid,)).fetchall()
        return sorted(r["version"] for r in rows)

    # ratings --------------------------------------------------------------
    def add_rating(self, sid: str, rater: str, score: int, comment: str | None) -> int:
        with self.conn() as c:
            cur = c.execute(
                "INSERT INTO ratings (session_id, rater, score, comment, created_at) VALUES (?,?,?,?,?)",
                (sid, rater, score, comment, utcnow()),
            )
            return int(cur.lastrowid)

    def ratings_for(self, sid: str) -> list[dict[str, Any]]:
        with self.conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM ratings WHERE session_id = ? ORDER BY id", (sid,))]

    # settings -------------------------------------------------------------
    def get_setting(self, key: str) -> str | None:
        with self.conn() as c:
            r = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return r["value"] if r else None

    def set_setting(self, key: str, value: str) -> None:
        with self.conn() as c:
            c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))

    # export ---------------------------------------------------------------
    def export_all(self) -> dict[str, Any]:
        with self.conn() as c:
            out = {
                t: [dict(r) for r in c.execute(f"SELECT * FROM {t}").fetchall()]
                for t in ("sessions", "metrics", "ratings", "settings")
            }
        for s in out["sessions"]:
            if s.get("client_info"):
                try:
                    s["client_info"] = json.loads(s["client_info"])
                except ValueError:
                    pass
        return out
