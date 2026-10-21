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
CREATE TABLE IF NOT EXISTS coaching (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    content TEXT NOT NULL,
    usage TEXT,
    raw TEXT
);
CREATE INDEX IF NOT EXISTS idx_coaching_session ON coaching(session_id);
CREATE TABLE IF NOT EXISTS notebook (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    source TEXT NOT NULL,
    session_id TEXT REFERENCES sessions(id),
    word_index INTEGER,
    pause_ms INTEGER,
    intent TEXT NOT NULL,
    context TEXT,
    suggestions TEXT,
    suggested_at TEXT,
    suggest_provider TEXT,
    suggest_model TEXT,
    english TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    step INTEGER NOT NULL DEFAULT 0,
    due_at TEXT,
    reviews INTEGER NOT NULL DEFAULT 0,
    last_reviewed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_notebook_due ON notebook(status, due_at);
CREATE TABLE IF NOT EXISTS conv_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    idx INTEGER NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    words TEXT,
    audio_path TEXT,
    latency_ms INTEGER,
    speech_ms INTEGER,
    created_at TEXT NOT NULL,
    UNIQUE (session_id, idx)
);
CREATE TABLE IF NOT EXISTS tts_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    session_id TEXT,
    provider TEXT,
    model TEXT,
    characters INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    purpose TEXT NOT NULL,
    ref TEXT
);
CREATE TABLE IF NOT EXISTS session_targets (
    session_id TEXT NOT NULL REFERENCES sessions(id),
    item_id INTEGER NOT NULL REFERENCES notebook(id),
    source TEXT NOT NULL,
    added_at TEXT NOT NULL,
    PRIMARY KEY (session_id, item_id)
);
CREATE TABLE IF NOT EXISTS notebook_uses (
    session_id TEXT NOT NULL REFERENCES sessions(id),
    item_id INTEGER NOT NULL REFERENCES notebook(id),
    used INTEGER NOT NULL,
    matched TEXT,
    checked_at TEXT NOT NULL,
    PRIMARY KEY (session_id, item_id)
);
CREATE TABLE IF NOT EXISTS notebook_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL REFERENCES notebook(id),
    reviewed_at TEXT NOT NULL,
    result TEXT NOT NULL,
    step_before INTEGER NOT NULL,
    step_after INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pronunciation (
    session_id TEXT NOT NULL REFERENCES sessions(id),
    unit TEXT NOT NULL,
    created_at TEXT NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (session_id, unit)
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

    # coaching -------------------------------------------------------------
    def add_coaching(self, sid: str, version: str, provider: str, model: str, content: dict[str, Any],
                     usage: dict[str, Any], raw: str) -> int:
        with self.conn() as c:
            cur = c.execute(
                "INSERT INTO coaching (session_id, version, created_at, provider, model, content, usage, raw) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (sid, version, utcnow(), provider, model, json.dumps(content, ensure_ascii=False),
                 json.dumps(usage, ensure_ascii=False), raw),
            )
            return int(cur.lastrowid)

    def latest_coaching(self, sid: str, version: str | None = None) -> dict[str, Any] | None:
        q, args = "SELECT * FROM coaching WHERE session_id = ?", [sid]
        if version:
            q += " AND version = ?"; args.append(version)
        with self.conn() as c:
            r = c.execute(q + " ORDER BY id DESC LIMIT 1", args).fetchone()
        if not r:
            return None
        out = dict(r)
        out["content"] = json.loads(out["content"])
        out["usage"] = json.loads(out["usage"]) if out.get("usage") else {}
        out.pop("raw", None)
        return out

    def latest_coaching_per_session(self) -> list[dict[str, Any]]:
        """The newest feedback of every session, oldest session first, with the session date."""
        with self.conn() as c:
            rows = c.execute(
                "SELECT c.session_id, s.local_date AS date, c.content FROM coaching c "
                "JOIN sessions s ON s.id = c.session_id "
                "WHERE c.id = (SELECT MAX(id) FROM coaching WHERE session_id = c.session_id) "
                "ORDER BY s.created_at").fetchall()
        return [{"session_id": r["session_id"], "date": r["date"], "content": json.loads(r["content"])} for r in rows]

    def count_coaching_since(self, iso_prefix: str) -> int:
        with self.conn() as c:
            return int(c.execute("SELECT COUNT(*) FROM coaching WHERE created_at >= ?", (iso_prefix,)).fetchone()[0])

    # notebook -------------------------------------------------------------
    def _nb(self, r: sqlite3.Row | None) -> dict[str, Any] | None:
        if not r:
            return None
        out = dict(r)
        out["suggestions"] = json.loads(out["suggestions"]) if out.get("suggestions") else None
        return out

    def add_notebook(self, row: dict[str, Any]) -> int:
        cols = ("created_at", "kind", "source", "session_id", "word_index", "pause_ms", "intent", "context",
                "english", "status", "step", "due_at")
        with self.conn() as c:
            cur = c.execute(f"INSERT INTO notebook ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                            [row.get(k) for k in cols])
            return int(cur.lastrowid)

    def get_notebook(self, item_id: int) -> dict[str, Any] | None:
        with self.conn() as c:
            return self._nb(c.execute("SELECT * FROM notebook WHERE id = ?", (item_id,)).fetchone())

    def list_notebook(self, due_before: str | None = None, session_id: str | None = None) -> list[dict[str, Any]]:
        q, args = "SELECT * FROM notebook WHERE status != 'archived'", []
        if due_before:
            q += " AND status IN ('learning','new') AND english IS NOT NULL AND due_at <= ?"; args.append(due_before)
        if session_id:
            q += " AND session_id = ?"; args.append(session_id)
        with self.conn() as c:
            return [self._nb(r) for r in c.execute(q + " ORDER BY due_at, id", args)]

    def update_notebook(self, item_id: int, **fields: Any) -> None:
        allowed = {"english", "status", "step", "due_at", "reviews", "last_reviewed_at", "suggestions",
                   "suggested_at", "suggest_provider", "suggest_model"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"unknown notebook fields: {sorted(bad)}")
        if "suggestions" in fields and not isinstance(fields["suggestions"], str):
            fields["suggestions"] = json.dumps(fields["suggestions"], ensure_ascii=False)
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self.conn() as c:
            c.execute(f"UPDATE notebook SET {sets} WHERE id = ?", [*fields.values(), item_id])

    def add_notebook_review(self, item_id: int, result: str, before: int, after: int) -> None:
        with self.conn() as c:
            c.execute("INSERT INTO notebook_reviews (item_id, reviewed_at, result, step_before, step_after) VALUES (?,?,?,?,?)",
                      (item_id, utcnow(), result, before, after))

    def add_target(self, sid: str, item_id: int, source: str) -> None:
        with self.conn() as c:
            c.execute("INSERT OR IGNORE INTO session_targets (session_id, item_id, source, added_at) VALUES (?,?,?,?)",
                      (sid, item_id, source, utcnow()))

    def targets_for(self, sid: str) -> list[dict[str, Any]]:
        with self.conn() as c:
            rows = c.execute("SELECT t.item_id, t.source, n.intent, n.english FROM session_targets t "
                             "JOIN notebook n ON n.id = t.item_id WHERE t.session_id = ? ORDER BY t.added_at", (sid,)).fetchall()
        return [dict(r) for r in rows]

    def save_use(self, sid: str, item_id: int, used: bool, matched: str | None) -> None:
        with self.conn() as c:
            c.execute("INSERT OR REPLACE INTO notebook_uses (session_id, item_id, used, matched, checked_at) VALUES (?,?,?,?,?)",
                      (sid, item_id, int(used), matched, utcnow()))

    def log_llm_call(self, purpose: str, ref: str | None = None) -> None:
        with self.conn() as c:
            c.execute("INSERT INTO llm_calls (at, purpose, ref) VALUES (?,?,?)", (utcnow(), purpose, ref))

    def add_turn(self, sid: str, role: str, text: str, words: list | None = None, audio_path: str | None = None,
                 latency_ms: int | None = None, speech_ms: int | None = None) -> int:
        with self.conn() as c:
            idx = int(c.execute("SELECT COALESCE(MAX(idx), -1) + 1 FROM conv_turns WHERE session_id = ?", (sid,)).fetchone()[0])
            c.execute("INSERT INTO conv_turns (session_id, idx, role, text, words, audio_path, latency_ms, speech_ms, created_at) "
                      "VALUES (?,?,?,?,?,?,?,?,?)",
                      (sid, idx, role, text, json.dumps(words) if words is not None else None, audio_path,
                       latency_ms, speech_ms, utcnow()))
        return idx

    def set_turn_text(self, sid: str, idx: int, text: str) -> None:
        with self.conn() as c:
            c.execute("UPDATE conv_turns SET text = ? WHERE session_id = ? AND idx = ?", (text, sid, idx))

    def set_turn_words(self, sid: str, idx: int, words: list, text: str | None = None) -> None:
        with self.conn() as c:
            if text:
                c.execute("UPDATE conv_turns SET words = ?, text = ? WHERE session_id = ? AND idx = ?", (json.dumps(words), text, sid, idx))
            else:
                c.execute("UPDATE conv_turns SET words = ? WHERE session_id = ? AND idx = ?", (json.dumps(words), sid, idx))

    def get_turns(self, sid: str) -> list[dict[str, Any]]:
        with self.conn() as c:
            rows = c.execute("SELECT * FROM conv_turns WHERE session_id = ? ORDER BY idx", (sid,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["words"] = json.loads(d["words"]) if d["words"] else None
            out.append(d)
        return out

    def log_tts_call(self, sid: str | None, provider: str, model: str, characters: int) -> None:
        with self.conn() as c:
            c.execute("INSERT INTO tts_calls (at, session_id, provider, model, characters) VALUES (?,?,?,?,?)",
                      (utcnow(), sid, provider, model, characters))

    def tts_characters_since(self, iso: str) -> int:
        with self.conn() as c:
            return int(c.execute("SELECT COALESCE(SUM(characters), 0) FROM tts_calls WHERE at >= ?", (iso,)).fetchone()[0])

    def count_llm_calls_since(self, iso_prefix: str, purpose: str | None = None, exclude: str | None = None) -> int:
        q, args = "SELECT COUNT(*) FROM llm_calls WHERE at >= ?", [iso_prefix]
        if purpose:
            q += " AND purpose = ?"; args.append(purpose)
        if exclude:
            q += " AND purpose != ?"; args.append(exclude)
        with self.conn() as c:
            return int(c.execute(q, args).fetchone()[0])

    # pronunciation ----------------------------------------------------------
    def save_pronunciation(self, sid: str, unit: str, data: dict[str, Any]) -> None:
        with self.conn() as c:
            c.execute("INSERT OR REPLACE INTO pronunciation (session_id, unit, created_at, data) VALUES (?, ?, ?, ?)",
                      (sid, unit, utcnow(), json.dumps(data, ensure_ascii=False)))

    def get_pronunciation(self, sid: str) -> list[dict[str, Any]]:
        with self.conn() as c:
            rows = c.execute("SELECT unit, data FROM pronunciation WHERE session_id = ? ORDER BY rowid", (sid,)).fetchall()
        return [{**json.loads(r["data"]), "unit": r["unit"]} for r in rows]

    def clear_pronunciation(self, sid: str) -> None:
        with self.conn() as c:
            c.execute("DELETE FROM pronunciation WHERE session_id = ?", (sid,))

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
                for t in ("sessions", "metrics", "ratings", "settings", "coaching", "notebook", "notebook_reviews", "llm_calls", "session_targets", "notebook_uses", "conv_turns", "tts_calls")
            }
        for s in out["sessions"]:
            if s.get("client_info"):
                try:
                    s["client_info"] = json.loads(s["client_info"])
                except ValueError:
                    pass
        return out
