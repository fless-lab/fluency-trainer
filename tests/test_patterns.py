from datetime import datetime, timezone

from app.patterns import build_patterns


def _item(sid, date, tags, focus=None):
    return {"session_id": sid, "date": date, "content": {
        "focus_next": focus,
        "patterns": [{"tag": t, "issue": f"issue {t}", "examples": [{"said": "a", "better": "b", "quote_found": True}]} for t in tags]}}


def test_counts_distinct_sessions_and_focus():
    now = datetime(2026, 10, 21, tzinfo=timezone.utc)
    items = [_item("s1", "2026-10-18", ["tense"]), _item("s2", "2026-10-19", ["tense", "linking"]), _item("s3", "2026-09-01", ["linking"])]
    out = build_patterns(items, now)
    by = {p["tag"]: p for p in out["patterns"]}
    assert by["tense"]["sessions"] == 2 and by["linking"]["sessions"] == 2
    assert by["tense"]["recent_sessions"] == 2 and by["linking"]["recent_sessions"] == 1
    assert out["focus"]["tag"] == "tense"
    assert "tense" in out["known_tags"]


def test_no_focus_with_single_recent_session():
    out = build_patterns([_item("s1", "2026-10-20", ["tense"])], datetime(2026, 10, 21, tzinfo=timezone.utc))
    assert out["focus"] is None
