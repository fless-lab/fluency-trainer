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
