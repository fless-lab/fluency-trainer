import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.protocols import CHECKPOINTS, topics_for_checkpoint


def make_settings(tmp_path: Path, provider: str = "mock", key: str = "") -> Settings:
    return Settings(
        data_dir=tmp_path / "data", stt_provider=provider, assemblyai_key=key, assemblyai_models=(),
        max_upload_bytes=1024 * 1024, host="127.0.0.1", port=0,
    )


@pytest.fixture()
def client(tmp_path):
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as c:
        c.app_ref = app
        yield c


def record(client, checkpoint="base0", topic_id="a-personal", seconds=120.0, payload=b"fake-audio"):
    r = client.post("/api/sessions", json={"kind": "baseline", "checkpoint": checkpoint, "topic_id": topic_id})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    up = client.post(
        f"/api/sessions/{sid}/audio",
        files={"file": ("a.webm", payload, "audio/webm")},
        data={"duration_s": str(seconds), "mime": "audio/webm;codecs=opus"},
    )
    assert up.status_code == 200, up.text
    return sid


def events(client):
    path = client.app_ref.state.events.path
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_full_flow_with_mock_provider(client):
    sid = record(client)
    s = client.get(f"/api/sessions/{sid}", params={"detail": True}).json()
    assert s["status"] == "done"
    assert s["provider"] == "mock"
    assert s["metrics"]["words_per_minute"] > 0
    assert s["words"] and s["pauses"] is not None
    assert s["topic_text"].startswith("Tell me about a time")
    assert s["has_audio"] is True
    assert client.get(f"/api/sessions/{sid}/audio").content == b"fake-audio"
    types = [e["type"] for e in events(client)]
    for expected in ("app.start", "session.created", "upload.saved", "process.start", "process.done"):
        assert expected in types


def test_audio_survives_when_provider_missing_and_can_be_retried(tmp_path):
    app = create_app(make_settings(tmp_path, provider="assemblyai", key=""))
    with TestClient(app) as c:
        assert c.get("/api/state").json()["provider_error"]
        sid = record(c)
        s = c.get(f"/api/sessions/{sid}").json()
        assert s["status"] == "error" and "ASSEMBLYAI_API_KEY" in s["error"]
        assert s["has_audio"] is True and s["metrics"] == {}


def test_upload_is_idempotent_and_rejects_bad_input(client):
    sid = record(client)
    again = client.post(
        f"/api/sessions/{sid}/audio",
        files={"file": ("a.webm", b"other", "audio/webm")}, data={"duration_s": "5"},
    )
    assert again.status_code == 200 and again.json()["already"] is True
    assert client.get(f"/api/sessions/{sid}/audio").content == b"fake-audio"

    r = client.post("/api/sessions", json={"kind": "free"})
    free = r.json()["id"]
    bad = client.post(f"/api/sessions/{free}/audio", files={"file": ("a.txt", b"x", "text/plain")}, data={"duration_s": "5"})
    assert bad.status_code == 415
    empty = client.post(f"/api/sessions/{free}/audio", files={"file": ("a.webm", b"", "audio/webm")}, data={"duration_s": "5"})
    assert empty.status_code == 422
    big = client.post(f"/api/sessions/{free}/audio", files={"file": ("a.webm", b"x" * (2 * 1024 * 1024), "audio/webm")}, data={"duration_s": "5"})
    assert big.status_code == 413
    assert client.get(f"/api/sessions/{free}").json()["has_audio"] is False  # nothing half written
    assert client.get("/api/sessions/nope").status_code == 404


def test_session_creation_validates_protocol(client):
    assert client.post("/api/sessions", json={"kind": "baseline", "checkpoint": "nope", "topic_id": "a-personal"}).status_code == 422
    assert client.post("/api/sessions", json={"kind": "baseline", "checkpoint": "base0", "topic_id": "b-opinion"}).status_code == 422
    ok = client.post("/api/sessions", json={"kind": "baseline", "checkpoint": "day15", "topic_id": "a-opinion"})
    assert ok.status_code == 201 and ok.json()["prep_seconds"] == 30 and ok.json()["planned_seconds"] == 120


def test_start_date_set_on_first_session_and_resettable(client):
    assert client.get("/api/state").json()["start_date"] is None
    client.post("/api/sessions", json={"kind": "free"})
    st = client.get("/api/state").json()
    assert st["day"] == 0 and st["start_date"]
    client.post("/api/start", json={"date": "2020-01-01"})
    assert client.get("/api/state").json()["start_date"] == "2020-01-01"


def test_feedback_ratings_and_client_events(client):
    sid = record(client)
    assert client.post(f"/api/sessions/{sid}/feedback", json={"self_rating": 4, "notes": "tired"}).status_code == 200
    assert client.post(f"/api/sessions/{sid}/feedback", json={"self_rating": 9}).status_code == 422
    assert client.post(f"/api/sessions/{sid}/ratings", json={"rater": "friend", "score": 3, "comment": "clear"}).status_code == 201
    s = client.get(f"/api/sessions/{sid}").json()
    assert s["self_rating"] == 4 and s["notes"] == "tired" and s["ratings"][0]["rater"] == "friend"
    assert client.post("/api/client-event", json={"type": "mic_denied", "session_id": sid, "payload": {"why": "test"}}).status_code == 202
    assert any(e["type"] == "client.mic_denied" for e in events(client))
    assert client.post("/api/client-event", json={"type": "Bad Type!"}).status_code == 422


def test_recompute_adds_metrics_without_touching_transcript(client):
    sid = record(client)
    before = client.get(f"/api/sessions/{sid}").json()["metrics"]
    r = client.post(f"/api/sessions/{sid}/recompute").json()
    assert r["values"]["words_per_minute"] == before["words_per_minute"]
    assert any(e["type"] == "metrics.recomputed" for e in events(client))


def test_progress_noise_and_verdicts(client):
    # base0 and base2 (two baselines), then day15, all through the mock provider
    for cp in ("base0", "base2", "day15"):
        for t in topics_for_checkpoint(cp):
            if not t["is_repeat"]:
                record(client, cp, t["topic_id"], seconds=100.0 + len(cp))
    record(client, "day15", "a-opinion", seconds=110.0)  # repeated topic
    p = client.get("/api/progress", params={"include_mock": True}).json()
    assert p["checkpoints"]["base0"]["n_topics"] == 3
    assert p["checkpoints"]["day15"]["n_topics"] == 3
    assert p["noise"]["words_per_minute"] is not None
    assert p["comparisons"]["day15"]["words_per_minute"]["verdict"] in {"within noise", "improved", "worse"}
    assert p["repeats"] and p["repeats"][0]["topic_id"] == "a-opinion"
    # mock sessions never leak into the default view
    default = client.get("/api/progress").json()
    assert default["checkpoints"]["base0"]["n_topics"] == 0


def test_export_and_event_log_downloads(client):
    record(client)
    exp = client.get("/api/export")
    assert exp.status_code == 200
    data = exp.json()
    assert len(data["sessions"]) == 1 and data["metrics"] and "events" not in data
    full = client.get("/api/export", params={"detail": True}).json()
    sess = full["sessions"][0]
    assert sess["words"] and sess["pauses"] is not None and "flags" in sess
    assert "words" not in data["sessions"][0]  # the light export stays light
    ev = client.get("/api/events.jsonl")
    assert ev.status_code == 200 and ev.text.count("\n") >= 5


def test_startup_recovers_sessions_stuck_in_processing(tmp_path):
    settings = make_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as c:
        sid = record(c)
        # simulate a crash in the middle of processing
        app.state.db.update_session(sid, status="processing", transcript_text=None)
    app2 = create_app(settings)
    with TestClient(app2) as c2:
        import time
        for _ in range(50):
            if c2.get(f"/api/sessions/{sid}").json()["status"] == "done":
                break
            time.sleep(0.05)
        assert c2.get(f"/api/sessions/{sid}").json()["status"] == "done"
    log = [json.loads(l) for l in (settings.data_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(e["type"] == "process.recover" and e["session_id"] == sid for e in log)


def test_every_checkpoint_has_valid_topics():
    for cp in CHECKPOINTS:
        assert len(cp["topics"]) == 3
        assert topics_for_checkpoint(cp["id"])


def test_event_log_download_is_a_consistent_snapshot(client):
    record(client)
    r = client.get("/api/events.jsonl")
    assert r.status_code == 200
    assert int(r.headers["content-length"]) == len(r.content)
    assert all(json.loads(line) for line in r.text.splitlines())


def test_page_and_scripts_are_always_revalidated(client):
    for path in ("/", "/static/app.js", "/static/style.css"):
        assert client.get(path).headers["cache-control"] == "no-cache"


GID = "a" * 32


def record_drill(client, round_, gid=GID, topic="Tell me a story.", seconds=100.0):
    body = {"kind": "drill", "group_id": gid, "drill_round": round_}
    if round_ == 1:
        body["topic_text"] = topic
    r = client.post("/api/sessions", json=body)
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    up = client.post(f"/api/sessions/{sid}/audio", files={"file": ("a.webm", b"x", "audio/webm")},
                     data={"duration_s": str(seconds), "mime": "audio/webm"})
    assert up.status_code == 200, up.text
    return r.json()


def test_drill_rounds_follow_the_432_timings_and_keep_the_same_topic(client):
    r1 = record_drill(client, 1)
    r2 = record_drill(client, 2, seconds=90.0)
    r3 = record_drill(client, 3, seconds=60.0)
    assert (r1["planned_seconds"], r2["planned_seconds"], r3["planned_seconds"]) == (240, 180, 120)
    assert (r1["prep_seconds"], r2["prep_seconds"], r3["prep_seconds"]) == (30, 10, 10)
    assert r2["topic_text"] == r3["topic_text"] == "Tell me a story."
    d = client.get(f"/api/drills/{GID}").json()
    assert [r["round"] for r in d["rounds"]] == [1, 2, 3] and d["complete"] is True
    assert d["change"]["words_per_minute"]["from_round"] == 1 and d["change"]["words_per_minute"]["to_round"] == 3
    assert client.get("/api/drills").json() == []  # mock transcripts stay out of the default list
    assert len(client.get("/api/drills", params={"include_mock": True}).json()) == 1


def test_drill_rejects_out_of_order_and_incomplete_requests(client):
    assert client.post("/api/sessions", json={"kind": "drill"}).status_code == 422
    assert client.post("/api/sessions", json={"kind": "drill", "group_id": GID, "drill_round": 1}).status_code == 422
    assert client.post("/api/sessions", json={"kind": "drill", "group_id": GID, "drill_round": 2}).status_code == 409
    assert client.post("/api/sessions", json={"kind": "drill", "group_id": "nope", "drill_round": 1, "topic_text": "x"}).status_code == 422
    record_drill(client, 1)
    assert client.post("/api/sessions", json={"kind": "drill", "group_id": GID, "drill_round": 1, "topic_text": "x"}).status_code == 409
    assert client.post("/api/sessions", json={"kind": "drill", "group_id": GID, "drill_round": 3}).status_code == 409
    assert client.get("/api/drills/" + "b" * 32).status_code == 404


def test_drill_sessions_never_enter_the_checkpoint_progress(client):
    for n in (1, 2, 3):
        record_drill(client, n)
    p = client.get("/api/progress", params={"include_mock": True}).json()
    assert all(cp["n_topics"] == 0 for cp in p["checkpoints"].values())


def test_database_from_an_older_version_is_upgraded_in_place(tmp_path):
    import sqlite3
    from app.db import DB
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, local_date TEXT NOT NULL, kind TEXT NOT NULL, "
                "checkpoint TEXT, topic_id TEXT, topic_text TEXT, is_repeat INTEGER NOT NULL DEFAULT 0, planned_seconds INTEGER, "
                "prep_seconds INTEGER, audio_path TEXT, audio_mime TEXT, audio_bytes INTEGER, client_seconds REAL, audio_seconds REAL, "
                "status TEXT NOT NULL, provider TEXT, provider_job_id TEXT, transcript_path TEXT, transcript_text TEXT, error TEXT, "
                "attempts INTEGER NOT NULL DEFAULT 0, self_rating INTEGER, notes TEXT, client_info TEXT)")
    con.execute("INSERT INTO sessions (id, created_at, local_date, kind, status) VALUES ('old1', 't', 'd', 'free', 'done')")
    con.commit(); con.close()
    db = DB(path)
    s = db.get_session("old1")
    assert s["id"] == "old1" and s["group_id"] is None and s["drill_round"] is None
