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
