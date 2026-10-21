"""Invited people: each one has their own data, and nothing of one can be reached by another."""
import json

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.test_api import record


def make(tmp_path, **kw):
    return Settings(data_dir=tmp_path / "data", stt_provider="mock", assemblyai_key="", assemblyai_models=(), max_upload_bytes=1024 * 1024,
                    host="127.0.0.1", port=0, tts_provider="mock", llm_provider="mock", **kw)


@pytest.fixture
def world(tmp_path):
    """The owner's client plus two invited people, each logged in with their own code, all on one app."""
    app = create_app(make(tmp_path))
    with TestClient(app) as owner:
        owner.app_ref = app

        def invite(name):
            r = owner.post("/api/users", json={"name": name})
            assert r.status_code == 201, r.text
            person = TestClient(app)
            login = person.post("/api/login", json={"password": r.json()["code"]})
            assert login.status_code == 200, login.text
            return person, r.json()["user"], r.json()["code"]

        (amina, a_user, a_code), (bob, b_user, b_code) = invite("Amina"), invite("Bob")
        yield dict(app=app, owner=owner, amina=amina, bob=bob, a=a_user, b=b_user, a_code=a_code, b_code=b_code, root=tmp_path / "data")


def test_each_person_sees_only_their_own_sessions_in_every_view(world):
    amina, bob, owner = world["amina"], world["bob"], world["owner"]
    sid = record(amina)
    assert [s["id"] for s in amina.get("/api/sessions").json()] == [sid]
    assert bob.get("/api/sessions").json() == [] and owner.get("/api/sessions").json() == []
    for path in (f"/api/sessions/{sid}", f"/api/sessions/{sid}/audio", f"/api/sessions/{sid}/coach", f"/api/sessions/{sid}/pronunciation",
                 f"/api/sessions/{sid}/pronunciation/lesson", f"/api/sessions/{sid}/clip?unit=s&t=1"):
        assert bob.get(path).status_code == 404 and owner.get(path).status_code == 404, path  # not even the owner, by that route
    assert bob.post(f"/api/sessions/{sid}/feedback", json={"self_rating": 5}).status_code == 404
    assert amina.get(f"/api/sessions/{sid}/audio").content == b"fake-audio"


def test_work_done_in_the_background_lands_in_the_right_persons_database(world):
    amina, bob = world["amina"], world["bob"]
    sid = record(amina)
    assert amina.get(f"/api/sessions/{sid}").json()["status"] == "done"  # transcribed after the upload, in Amina's data
    record(bob)
    assert len(amina.get("/api/sessions").json()) == 1 and len(bob.get("/api/sessions").json()) == 1
    assert amina.get("/api/progress", params={"include_mock": "true"}).json()["checkpoints"]["base0"]["n_topics"] == 1


def test_files_are_kept_in_separate_folders_and_the_owners_folder_stays_clean(world):
    root = world["root"]
    record(world["amina"])
    record(world["bob"])
    a_dir, b_dir = root / "users" / world["a"]["id"], root / "users" / world["b"]["id"]
    assert len(list((a_dir / "audio").glob("*"))) == 1 and len(list((b_dir / "audio").glob("*"))) == 1
    assert (a_dir / "fluency.db").is_file() and (b_dir / "fluency.db").is_file()
    assert list((root / "audio").glob("*")) == []  # the owner's own folder has nothing of theirs
    assert list((root / "transcripts").glob("*")) == []


def test_the_notebook_the_start_date_and_the_event_log_are_per_person(world):
    amina, bob, owner = world["amina"], world["bob"], world["owner"]
    amina.post("/api/notebook", json={"kind": "phrase", "intent": "say thank you nicely", "english": "I really appreciate it"})
    assert len(amina.get("/api/notebook").json()["items"]) == 1
    assert bob.get("/api/notebook").json()["items"] == [] and owner.get("/api/notebook").json()["items"] == []
    record(amina)
    assert amina.get("/api/state").json()["start_date"] is not None and bob.get("/api/state").json()["start_date"] is None
    log = amina.get("/api/events.jsonl").text
    assert "session.created" in log and "notebook.add" in log
    assert "session.created" not in bob.get("/api/events.jsonl").text


def test_the_sound_cache_is_shared_but_nothing_personal_is(world):
    a = world["amina"].get("/api/say", params={"text": "think"})
    b = world["bob"].get("/api/say", params={"text": "think"})
    assert a.status_code == 200 and a.content == b.content
    made = [json.loads(x) for x in (world["root"] / "events.jsonl").read_text().splitlines() if "say.made" in x]
    assert len(made) <= 1  # the second person did not pay for it again


def test_people_cannot_use_the_owners_tools(world):
    for who in (world["amina"], world["bob"]):
        assert who.get("/api/users").status_code == 403 and who.post("/api/users", json={"name": "X"}).status_code == 403
        assert who.get("/api/settings").status_code == 403 and who.post("/api/restart").status_code == 403
        assert who.get("/api/backups").status_code == 403 and who.get("/api/feedback").status_code == 403
        assert who.post(f"/api/users/{world['b']['id']}/renew").status_code == 403
        assert who.delete(f"/api/users/{world['b']['id']}?confirm=Bob").status_code == 403


def test_a_stranger_has_no_access_but_the_owner_at_this_computer_still_does(world):
    with TestClient(world["app"], client=("203.0.113.9", 1)) as far:
        assert far.get("/api/state").status_code == 401 and far.get("/api/sessions").status_code == 401
        assert far.post("/api/login", json={"password": "ABCD-EFGH-JKMN-PQRS"}).status_code == 401
    assert world["owner"].get("/api/state").status_code == 200  # no cookie, this computer, no owner password: the owner


def test_blocking_cuts_a_person_off_at_once_and_unblocking_restores_the_same_cookie_no_longer(world):
    amina, owner = world["amina"], world["owner"]
    assert amina.get("/api/state").status_code == 200
    assert owner.post(f"/api/users/{world['a']['id']}/block", json={"blocked": True}).status_code == 200
    # the cookie still exists in her browser; it no longer works. (This test client is on the owner's computer, so a request
    # without a working cookie falls back to the owner: use a remote client to see the refusal)
    with TestClient(world["app"], client=("203.0.113.9", 1)) as far:
        far.cookies.set("ft_session", amina.cookies.get("ft_session"))
        assert far.get("/api/state").status_code == 401
        assert far.post("/api/login", json={"password": world["a_code"]}).status_code == 401
    owner.post(f"/api/users/{world['a']['id']}/block", json={"blocked": False})
    with TestClient(world["app"], client=("203.0.113.9", 1)) as far:
        far.cookies.set("ft_session", amina.cookies.get("ft_session"))
        assert far.get("/api/state").status_code == 401  # blocking changed the version: the old cookie stays dead
        assert far.post("/api/login", json={"password": world["a_code"]}).status_code == 200


def test_a_renewed_code_replaces_the_old_one_and_logs_the_person_out(world):
    owner = world["owner"]
    new = owner.post(f"/api/users/{world['a']['id']}/renew").json()["code"]
    assert new != world["a_code"]
    with TestClient(world["app"], client=("203.0.113.9", 1)) as far:
        far.cookies.set("ft_session", world["amina"].cookies.get("ft_session"))
        assert far.get("/api/state").status_code == 401
        assert far.post("/api/login", json={"password": world["a_code"]}).status_code == 401
        assert far.post("/api/login", json={"password": new}).status_code == 200 and far.get("/api/state").status_code == 200


def test_deleting_a_person_erases_their_folder_and_needs_their_name_typed(world):
    owner, root = world["owner"], world["root"]
    record(world["amina"])
    folder = root / "users" / world["a"]["id"]
    assert folder.is_dir()
    assert owner.delete(f"/api/users/{world['a']['id']}").status_code == 422 and folder.is_dir()
    assert owner.delete(f"/api/users/{world['a']['id']}?confirm=Amina").status_code == 200
    assert not folder.exists() and world["bob"].get("/api/state").status_code == 200  # Bob is untouched
    assert [p["name"] for p in owner.get("/api/users").json()["people"]] == ["Bob"]


def test_a_person_can_export_and_delete_only_their_own_data(world):
    import io, zipfile
    amina, bob = world["amina"], world["bob"]
    record(amina)
    record(bob)
    z = zipfile.ZipFile(io.BytesIO(amina.get("/api/me/export").content))
    names = z.namelist()
    assert "fluency.db" in names and sum(n.startswith("audio/") for n in names) == 1 and not any("users/" in n for n in names)
    assert amina.delete("/api/me").status_code == 422
    assert amina.delete("/api/me", params={"confirm": "Amina"}).status_code == 200
    assert not (world["root"] / "users" / world["a"]["id"]).exists() and len(bob.get("/api/sessions").json()) == 1


def test_first_use_asks_for_consent_and_the_profile_is_the_persons_own(world):
    amina = world["amina"]
    me = amina.get("/api/me").json()
    assert me["name"] == "Amina" and me["is_admin"] is False and me["needs_consent"] is True
    amina.post("/api/me/consent")
    assert amina.get("/api/me").json()["needs_consent"] is False
    assert amina.post("/api/me/profile", json={"native_language": " Ewe "}).json()["native_language"] == "Ewe"
    assert world["bob"].get("/api/me").json()["native_language"] == ""
    assert world["owner"].get("/api/me").json()["is_admin"] is True


def test_feedback_from_a_tester_reaches_the_owner_with_who_sent_it(world):
    assert world["amina"].post("/api/feedback", json={"text": "The mic button is hard to find", "page": "today"}).status_code == 201
    items = world["owner"].get("/api/feedback").json()["items"]
    assert items[0]["name"] == "Amina" and items[0]["page"] == "today" and "mic button" in items[0]["text"]
    assert world["bob"].get("/api/feedback").status_code == 403


def test_the_owners_usage_view_counts_each_person_separately(world):
    record(world["amina"])
    out = world["owner"].get("/api/users").json()
    by = {p["name"]: p for p in out["people"]}
    assert by["Amina"]["usage"]["sessions"] == 1 and by["Bob"]["usage"]["sessions"] == 0 and out["owner"]["usage"]["sessions"] == 0
    assert by["Amina"]["usage"]["usage_7d"].get("stt_seconds", 0) > 0 and "code_hash" not in json.dumps(out)


def test_using_the_database_with_nobody_identified_is_an_error_not_a_guess(world):
    from app.tenancy import NoUser
    with pytest.raises(NoUser):
        world["app"].state.tenants  # exists
        from app.tenancy import current
        current()


def test_two_people_working_at_the_same_time_never_mix_their_data(world):
    import threading
    amina, bob = world["amina"], world["bob"]
    errors = []

    def work(client, n):
        try:
            for _ in range(n):
                record(client)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
    threads = [threading.Thread(target=work, args=(amina, 8)), threading.Thread(target=work, args=(bob, 8))]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    a_ids = {s["id"] for s in amina.get("/api/sessions").json()}
    b_ids = {s["id"] for s in bob.get("/api/sessions").json()}
    assert len(a_ids) == 8 and len(b_ids) == 8 and not (a_ids & b_ids)
    root = world["root"] / "users"
    assert {p.stem for p in (root / world["a"]["id"] / "audio").glob("*")} == a_ids
    assert {p.stem for p in (root / world["b"]["id"] / "audio").glob("*")} == b_ids
    assert all(s["status"] == "done" for s in amina.get("/api/sessions").json() + bob.get("/api/sessions").json())


def test_pronunciation_scored_in_the_background_is_saved_for_the_right_person(tmp_path, monkeypatch):
    import time
    from app import pronunciation as pr
    from tests.test_pronunciation import _azure

    async def fake_assess(path, key, region, locale, gate=None):
        chunk = pr.summarize_chunk(_azure([("children", 50, [("ch", 40)])]), 0.0)
        return {"seconds": 3.0, "chunks": [{**chunk, "seconds": 3.0}]}
    monkeypatch.setattr(pr, "assess_audio", fake_assess)
    app = create_app(make(tmp_path, azure_speech_key="k", azure_speech_region="norwayeast"))
    with TestClient(app) as owner:
        persons = []
        for name in ("Amina", "Bob"):
            r = owner.post("/api/users", json={"name": name}).json()
            c = TestClient(app)
            c.post("/api/login", json={"password": r["code"]})
            persons.append(c)
        amina, bob = persons
        sid = record(amina)
        for _ in range(60):
            if amina.get(f"/api/sessions/{sid}/pronunciation").json()["state"] == "done":
                break
            time.sleep(0.05)
        assert amina.get(f"/api/sessions/{sid}/pronunciation").json()["summary"]["worst_words"][0]["word"] == "children"
        assert bob.get(f"/api/sessions/{sid}/pronunciation").status_code == 404
        assert amina.get("/api/pronunciation/progress").json()["n_sessions"] == 0 and bob.get("/api/pronunciation/progress").json()["n_sessions"] == 0
        assert amina.app.state.tenants.for_user(app.state.registry.list()[0]["id"]).db.usage_since("2000")["pron_seconds"] == 3.0


def test_work_interrupted_before_a_restart_is_finished_for_each_person(tmp_path):
    app = create_app(make(tmp_path))
    with TestClient(app) as owner:
        r = owner.post("/api/users", json={"name": "Amina"}).json()
        amina = TestClient(app)
        amina.post("/api/login", json={"password": r["code"]})
        sid = record(amina)
        db = app.state.tenants.for_user(r["user"]["id"]).db
        db.update_session(sid, status="uploaded", transcript_text=None)  # as if the app had stopped before transcribing
    app2 = create_app(make(tmp_path))  # a restart on the same folder
    with TestClient(app2):
        import time
        db2 = app2.state.tenants.for_user(r["user"]["id"]).db
        for _ in range(60):
            if db2.get_session(sid)["status"] == "done":
                break
            time.sleep(0.05)
        assert db2.get_session(sid)["status"] == "done"
