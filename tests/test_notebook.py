from datetime import datetime, timedelta, timezone

import pytest

from app.notebook import INTERVAL_DAYS, context_around, parse_suggestions, schedule_after
from tests.test_api import record
from tests.test_coach import make, run

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def test_schedule_widens_resets_and_ends():
    r = schedule_after(0, "fluent", NOW)
    assert r["step"] == 1 and r["status"] == "learning" and r["due_at"].startswith("2026-10-21")
    r = schedule_after(2, "hesitated", NOW)
    assert r["step"] == 2 and r["due_at"].startswith("2026-10-23")  # same interval again
    assert schedule_after(0, "hesitated", NOW)["due_at"].startswith("2026-10-21")  # never sooner than tomorrow
    r = schedule_after(4, "failed", NOW)
    assert r["step"] == 0 and r["due_at"].startswith("2026-10-21")
    last = len(INTERVAL_DAYS) - 1
    done = schedule_after(last, "fluent", NOW)
    assert done["status"] == "known" and done["due_at"].startswith("2026-11-19")
    with pytest.raises(ValueError):
        schedule_after(0, "great", NOW)


def test_parse_suggestions_strict():
    ok = parse_suggestions('noise {"options":[{"english":"I ran out of time","note":"casual"}],"tip":""} end')
    assert ok["options"][0]["english"] == "I ran out of time"
    for bad in ("", "{}", '{"options": []}', '{"options":[{"note":"x"}]}', "[1]"):
        with pytest.raises(ValueError):
            parse_suggestions(bad)


def test_context_around_clamps():
    words = [{"text": f"w{i}"} for i in range(30)]
    assert context_around(words, 0).startswith("w0")
    assert context_around(words, 99).endswith("w29")
    assert context_around([], 3) == ""


def test_gap_to_review_flow(tmp_path):
    def go(c):
        sid = record(c)
        g = c.post(f"/api/sessions/{sid}/gaps", json={"intent": "je voulais dire: j'ai du mal à joindre les deux bouts", "word_index": 5})
        assert g.status_code == 201
        item = g.json()["item"]
        assert item["status"] == "new" and item["context"]
        # no english yet: not reviewable, review refused
        assert c.get("/api/notebook?due=true").json()["items"] == []
        assert c.post(f"/api/notebook/{item['id']}/review", json={"result": "fluent"}).status_code == 409
        s = c.post(f"/api/notebook/{item['id']}/suggest")
        assert s.status_code == 200 and s.json()["item"]["suggestions"]["options"]
        pick = s.json()["item"]["suggestions"]["options"][0]["english"]
        assert c.post(f"/api/notebook/{item['id']}/choose", json={"english": pick}).json()["item"]["status"] == "learning"
        due = c.get("/api/notebook?due=true").json()
        assert [i["id"] for i in due["items"]] == [item["id"]] and due["due"] == 1
        assert c.get("/api/state").json()["notebook_due"] == 1
        rv = c.post(f"/api/notebook/{item['id']}/review", json={"result": "fluent"}).json()["item"]
        assert rv["step"] == 1 and rv["reviews"] == 1
        assert c.get("/api/notebook?due=true").json()["items"] == []  # pushed to tomorrow
        assert c.post(f"/api/notebook/{item['id']}/review", json={"result": "bad"}).status_code == 422
        exp = c.get("/api/export").json()
        assert len(exp["notebook"]) == 1 and len(exp["notebook_reviews"]) == 1 and exp["llm_calls"]
        c.post(f"/api/notebook/{item['id']}/archive")
        assert c.get("/api/notebook").json()["items"] == []
        assert c.post("/api/notebook/999/choose", json={"english": "x"}).status_code == 404
        assert c.post(f"/api/sessions/{sid}/gaps", json={"intent": "x"}).status_code == 422
    run(make(tmp_path), go)


def test_manual_add_and_shared_cap(tmp_path):
    def go(c):
        sid = record(c)
        m = c.post("/api/notebook", json={"kind": "word", "intent": "épuisé", "english": "worn out"}).json()["item"]
        assert m["status"] == "learning" and len(c.get("/api/notebook?due=true").json()["items"]) == 1
        g = c.post(f"/api/sessions/{sid}/gaps", json={"intent": "mon idée"}).json()["item"]
        assert c.post(f"/api/notebook/{g['id']}/suggest").status_code == 200
        # cap of 1 is spent: feedback and suggestions are both refused
        assert c.post(f"/api/notebook/{g['id']}/suggest").status_code == 429
        assert c.post(f"/api/sessions/{sid}/coach").status_code == 429
    run(make(tmp_path, cap=1), go)


def test_suggest_needs_a_model(tmp_path):
    def go(c):
        sid = record(c)
        g = c.post(f"/api/sessions/{sid}/gaps", json={"intent": "mon idée"}).json()["item"]
        assert c.post(f"/api/notebook/{g['id']}/suggest").status_code == 503
        # the notebook still works by hand without any model
        assert c.post(f"/api/notebook/{g['id']}/choose", json={"english": "my idea"}).status_code == 200
    run(make(tmp_path, llm="none"), go)


def test_find_use_ignores_fillers_and_accepts_a_small_slip():
    from app.notebook import find_use
    t = "my father, who um passed away when I was one, left us a house"
    assert find_use("who passed away", t) == "who passed away"
    assert find_use("who has passed away", t) is not None  # one extra word, close enough
    assert find_use("stuck with me", t) is None
    assert find_use("house", t) is None  # a single word proves nothing


def test_targets_are_refused_in_baseline_and_usage_is_checked(tmp_path):
    def go(c):
        item = c.post("/api/notebook", json={"intent": "mourir", "english": "pass away"}).json()["item"]
        base = c.post("/api/sessions", json={"kind": "baseline", "checkpoint": "base0", "topic_id": "a-personal"}).json()["id"]
        assert c.post(f"/api/sessions/{base}/targets", json={"item_ids": [item["id"]]}).status_code == 409
        free = c.post("/api/sessions", json={"kind": "free", "planned_seconds": 120}).json()["id"]
        assert c.post(f"/api/sessions/{free}/targets", json={"item_ids": [999]}).status_code == 404
        assert c.post(f"/api/sessions/{free}/targets", json={"item_ids": [item["id"]], "source": "prep"}).status_code == 201
        assert c.get(f"/api/sessions/{free}/uses").json()["uses"][0]["used"] is None  # no transcript yet
        sug = c.get("/api/notebook/suggested-targets").json()["items"]
        assert [i["id"] for i in sug] == [item["id"]]
    run(make(tmp_path), go)


def test_a_target_phrase_said_in_the_talk_is_detected(tmp_path):
    def go(c):
        # the mock speech provider says "...the best option for everyone involved..." in every recording
        item = c.post("/api/notebook", json={"intent": "la meilleure option", "english": "the best option for everyone"}).json()["item"]
        other = c.post("/api/notebook", json={"intent": "rester en tête", "english": "stuck with me"}).json()["item"]
        sid = c.post("/api/sessions", json={"kind": "free", "planned_seconds": 120}).json()["id"]
        c.post(f"/api/sessions/{sid}/targets", json={"item_ids": [item["id"], other["id"]]})
        assert c.post(f"/api/sessions/{sid}/targets", json={"item_ids": [item["id"]]}).status_code == 201  # idempotent
        up = c.post(f"/api/sessions/{sid}/audio", files={"file": ("a.webm", b"x", "audio/webm")},
                    data={"duration_s": "120", "mime": "audio/webm"})
        assert up.status_code == 200
        assert c.post(f"/api/sessions/{sid}/targets", json={"item_ids": [item["id"]]}).status_code == 409  # too late
        uses = {u["item_id"]: u for u in c.get(f"/api/sessions/{sid}/uses").json()["uses"]}
        assert uses[item["id"]]["used"] is True and uses[other["id"]]["used"] is False
        assert len(c.get("/api/export").json()["notebook_uses"]) == 2
    run(make(tmp_path), go)
