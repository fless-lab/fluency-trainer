"""Pronunciation scoring: audio cutting and summarising, with a recorded Azure-shaped answer and no network."""
import struct

from app import pronunciation as pr
from app.coach import build_messages


def _pcm(seconds, loud_until=None, quiet_at=None):
    n = int(seconds * pr.RATE)
    out = []
    for i in range(n):
        t = i / pr.RATE
        quiet = quiet_at is not None and abs(t - quiet_at) < 0.1
        out.append(0 if quiet else 3000 if (i % 40) < 20 else -3000)
    return struct.pack(f"<{n}h", *out)


def test_short_audio_is_one_window_and_long_audio_is_cut_in_the_quiet_part():
    assert pr.split_windows(_pcm(10)) == [(0.0, 10.0)]
    wins = pr.split_windows(_pcm(60, quiet_at=24.0))
    assert len(wins) >= 3 and all(b - a <= pr.MAX_CHUNK_S + 0.01 for a, b in wins)
    assert abs(wins[0][1] - 24.0) < 0.2  # cut where the silence is
    assert wins[0][1] == wins[1][0] and wins[-1][1] == 60.0


def _azure(words):
    return {"RecognitionStatus": "Success", "NBest": [{"AccuracyScore": 90, "FluencyScore": 80, "ProsodyScore": 70, "PronScore": 85,
            "Words": [{"Word": w, "Offset": 10_000_000 * i, "AccuracyScore": acc, "ErrorType": "None",
                       "Phonemes": [{"Phoneme": p, "AccuracyScore": sc} for p, sc in ph]} for i, (w, acc, ph) in enumerate(words)]}]}


def test_summary_finds_weak_words_and_sounds_and_feeds_the_coach():
    d = pr.summarize_chunk(_azure([("think", 40, [("th", 20), ("ih", 90)]), ("thing", 55, [("th", 40), ("ih", 90)]),
                                    ("cat", 95, [("k", 95), ("ae", 95)])]), 5.0)
    assert d["words"][1]["t"] == 6.0  # offsets are made relative to the whole recording
    agg = pr.aggregate([{"seconds": 3.0, "unit": "s", "chunks": [d]}])
    assert [w["word"] for w in agg["worst_words"]] == ["think", "thing"]
    assert agg["worst_words"][0]["weak_sounds"][0]["p"] == "th"
    assert agg["weak_sounds"][0]["sound"] == "th" and agg["weak_sounds"][0]["count"] == 2
    text = pr.for_coach(agg)
    assert '"think" scored 40' in text and "/th/" in text
    _, user = build_messages({"topic_text": "x"}, {}, "think thing cat", pronunciation=text)
    assert "Pronunciation measured from the audio" in user


def test_nothing_recognised_gives_no_summary():
    assert pr.summarize_chunk({"RecognitionStatus": "InitialSilenceTimeout"}, 0)["words"] == []
    assert pr.for_coach(pr.aggregate([])) == ""


def test_clips_are_taken_around_the_weakest_words_and_overlapping_ones_are_merged():
    summary = {"worst_words": [
        {"word": "a", "score": 10, "weak_sounds": [], "t": 10.0, "unit": "s", "times": 1},
        {"word": "b", "score": 20, "weak_sounds": [], "t": 11.0, "unit": "s", "times": 1},
        {"word": "c", "score": 30, "weak_sounds": [], "t": 30.0, "unit": "s", "times": 1}]}
    clips = pr.pick_clips(summary)
    assert len(clips) == 2 and len(clips[0]["words"]) == 2 and clips[0]["a"] == 8.5 and clips[0]["b"] == 14.5


def test_the_audio_model_answer_is_checked_and_trimmed(monkeypatch):
    import asyncio, httpx

    class Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None, headers=None):
            assert json["messages"][1]["content"][1]["type"] == "input_audio"
            text = 'Sure: {"clips":[{"word":"think","heard":"sink","issue":"th is s","fix":"tongue between teeth"},{"nope":1}],"overall":"flat"}'
            return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})
    monkeypatch.setattr(pr.httpx, "AsyncClient", Client)
    clips = [{"unit": "s", "a": 0.0, "b": 1.0, "words": [{"word": "think", "score": 20, "weak_sounds": [{"p": "th", "s": 10}]}]}]
    out = asyncio.run(pr.listen(clips, {"s": _pcm(2)}, "k", "m"))
    assert out["clips"] == [{"word": "think", "heard": "sink", "issue": "th is s", "fix": "tongue between teeth"}] and out["overall"] == "flat"
    assert "sounds like sink" in pr.ear_for_coach(out)


def _settings(tmp_path, ear=False):
    from app.config import Settings
    return Settings(data_dir=tmp_path / "data", stt_provider="mock", assemblyai_key="", assemblyai_models=(),
                    max_upload_bytes=1024 * 1024, host="127.0.0.1", port=0, azure_speech_key="k", azure_speech_region="norwayeast",
                    audio_key="k" if ear else "")


def _wait(c, sid, want, tries=60):
    import time
    for _ in range(tries):
        r = c.get(f"/api/sessions/{sid}/pronunciation").json()
        if r["state"] == want and not r["ear_pending"]:
            return r
        time.sleep(0.05)
    return r


def test_score_button_starts_scoring_in_the_background_and_the_result_is_stored(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from tests.test_api import record
    calls = []

    async def fake_assess(path, key, region, locale, gate=None):
        calls.append(path)
        chunk = pr.summarize_chunk(_azure([("think", 40, [("th", 20), ("ih", 90)])]), 0.0)
        return {"seconds": 3.0, "chunks": [{**chunk, "seconds": 3.0}]}

    async def fake_listen(clips, pcm, key, model, base="x"):
        return {"model": model, "clips": [{"word": "think", "heard": "sink", "issue": "th", "fix": "tongue"}], "overall": "ok"}
    monkeypatch.setattr(pr, "assess_audio", fake_assess)
    monkeypatch.setattr(pr, "decode_pcm16k", lambda path: _pcm(4))
    monkeypatch.setattr(pr, "listen", fake_listen)
    with TestClient(create_app(_settings(tmp_path, ear=True))) as c:
        sid = record(c)                                  # scored right after the upload
        first = _wait(c, sid, "done")
        assert first["state"] == "done" and first["summary"]["worst_words"][0]["word"] == "think"
        assert first["ear"]["clips"][0]["heard"] == "sink"
        n = len(calls)
        r = c.post(f"/api/sessions/{sid}/pronunciation")  # the button: this used to fail with "no running event loop"
        assert r.status_code == 202 and r.json()["state"] == "pending"
        again = _wait(c, sid, "done")
        assert again["state"] == "done" and len(calls) == n + 1 and again["ear"] is not None


def test_score_button_says_so_when_azure_is_not_configured(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.config import Settings
    from tests.test_api import record
    s = Settings(data_dir=tmp_path / "data", stt_provider="mock", assemblyai_key="", assemblyai_models=(),
                 max_upload_bytes=1024 * 1024, host="127.0.0.1", port=0)
    with TestClient(create_app(s)) as c:
        sid = record(c)
        assert c.get(f"/api/sessions/{sid}/pronunciation").json()["state"] == "off"
        assert c.post(f"/api/sessions/{sid}/pronunciation").status_code == 503


def test_small_words_and_half_words_are_left_out_of_the_lists_but_not_the_scores():
    d = pr.summarize_chunk(_azure([("ifying", 29, [("f", 25)]), ("didn't", 5, [("d", 0)]), ("mathematical", 45, [("th", 38), ("l", 27)]),
                                    ("cat", 95, [("k", 95)]), ("so", 34, [("s", 35)]), ("bank", 39, [("b", 26)]), ("vision", 20, [("zh", 18)])]),
                           10.0, cut_start=True, cut_end=True)
    assert d["words"][0]["edge"] is True and d["words"][-1]["edge"] is True and d["words"][2]["edge"] is False
    agg = pr.aggregate([{"seconds": 9.0, "unit": "s", "chunks": [d]}])
    assert [w["word"] for w in agg["worst_words"]] == ["bank", "mathematical"]  # lowest first; no ifying (cut), didn't, so (small), vision (cut)
    assert agg["n_words"] == 7 and agg["scores"]["AccuracyScore"] == 90  # the scores still count every word


def test_a_word_is_cut_out_of_the_persons_own_recording(tmp_path, monkeypatch):
    import io, wave
    from fastapi.testclient import TestClient
    from app.main import create_app
    from tests.test_api import record
    monkeypatch.setattr(pr, "decode_pcm16k", lambda path: _pcm(5))
    with TestClient(create_app(_settings(tmp_path))) as c:
        sid = record(c)
        r = c.get(f"/api/sessions/{sid}/clip", params={"unit": "s", "t": 1.0, "d": 0.5})
        w = wave.open(io.BytesIO(r.content))
        assert r.status_code == 200 and abs(w.getnframes() / w.getframerate() - 1.0) < 0.01  # 0.2 s before, the word, 0.3 s after
        assert c.get(f"/api/sessions/{sid}/clip", params={"unit": "7", "t": 1.0}).status_code == 404
        assert c.get(f"/api/sessions/{sid}/clip", params={"unit": "s", "t": 99.0}).status_code == 404


def test_lesson_is_checked_against_the_measured_words_and_the_sounds_are_given_as_symbols():
    from app import lesson as ls
    summary = pr.aggregate([{"seconds": 3.0, "unit": "s", "chunks": [pr.summarize_chunk(_azure([
        ("children", 50, [("ch", 40), ("ih", 90)]), ("joining", 46, [("jh", 36)]), ("vision", 30, [("zh", 20)])]), 0.0)]}])
    system, user = ls.build_messages(summary, "My children went to school. It was a good time.", "French")
    assert "first language is French" in system and "/tʃ/ (40)" in user and 'They said: "My children went to school."' in user
    raw = ('{"focus":[{"sound":"tʃ","label":"ch","explain":"e","how":"h","pairs":[["chip","ship"],["bad"]],'
           '"your_words":["children","invented"],"sentences":["a","b","c","d"]}],"advice":"a"}')
    out = ls.parse_lesson("Here: " + raw, summary)["focus"][0]
    assert out["your_words"] == ["children"] and out["pairs"] == [["chip", "ship"]] and len(out["sentences"]) == 3
    assert out["clips"][0]["word"] == "children"


def test_lesson_route_makes_stores_and_reuses_the_lesson(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.config import Settings
    from app.main import create_app
    from tests.test_api import record
    async def fake_assess(path, key, region, locale, gate=None):
        return {"seconds": 3.0, "chunks": [{**pr.summarize_chunk(_azure([("children", 50, [("ch", 40)]), ("vision", 30, [("zh", 20)])]), 0.0), "seconds": 3.0}]}
    monkeypatch.setattr(pr, "assess_audio", fake_assess)
    s = Settings(data_dir=tmp_path / "data", stt_provider="mock", assemblyai_key="", assemblyai_models=(), max_upload_bytes=1024 * 1024,
                 host="127.0.0.1", port=0, llm_provider="mock", azure_speech_key="k", azure_speech_region="norwayeast")
    with TestClient(create_app(s)) as c:
        sid = record(c)
        assert _wait(c, sid, "done")["state"] == "done"
        assert c.get(f"/api/sessions/{sid}/pronunciation/lesson").json() == {"lesson": None}
        first = c.post(f"/api/sessions/{sid}/pronunciation/lesson").json()
        assert first["reused"] is False and first["lesson"]["focus"][0]["your_words"] == ["vision", "children"]
        again = c.post(f"/api/sessions/{sid}/pronunciation/lesson").json()
        assert again["reused"] is True and c.get(f"/api/sessions/{sid}/pronunciation/lesson").json()["lesson"] == first["lesson"]


def _session_summary(th, zh, extra_word=None, n=60, th_count=9):
    """An aggregate for a session where /th/ averaged `th` and /zh/ averaged `zh`, spread over n words."""
    words = [(f"think{i}", th, [("th", th)]) for i in range(th_count)]
    words += [(f"vision{i}", zh, [("zh", zh)]) for i in range(th_count)]
    if extra_word:
        words.append((extra_word, 30, [("ae", 30)]))
    words += [(f"word{i}", 95, [("k", 95)]) for i in range(n - len(words))]
    return pr.aggregate([{"seconds": 60.0, "unit": "s", "chunks": [{**pr.summarize_chunk(_azure(words), 0.0), "seconds": 60.0}]}])


def test_trends_show_better_worse_steady_and_words_that_keep_coming_back():
    plan = [(50, 80, "bank"), (55, 80, "bank"), (62, 79, None), (70, 80, "bank")]  # /th/ gains 20 points, /zh/ stays
    sessions = [{"id": f"s{i}", "date": f"2026-10-{10 + i}", "kind": "free", "summary": _session_summary(th, zh, w)}
                for i, (th, zh, w) in enumerate(plan)]
    sessions.append({"id": "short", "date": "2026-10-20", "kind": "free", "summary": _session_summary(10, 10, n=6)})  # too short: ignored
    out = pr.trends(sessions)
    assert out["n_sessions"] == 4
    by = {s["sound"]: s for s in out["sounds"]}
    assert by["th"]["trend"] == "improving" and by["th"]["delta"] >= 8 and by["zh"]["trend"] == "steady"
    assert [s["sound"] for s in out["sounds"]][0] == "th"  # weakest first
    bank = next(w for w in out["recurring_words"] if w["word"] == "bank")
    assert bank["sessions"] == 3 and bank["last"]["id"] == "s3"  # weak in three of the four sessions, last heard in the latest
    assert len(out["scores"]["AccuracyScore"]) == 4


def test_trends_need_enough_data_before_calling_a_direction(tmp_path):
    two = [{"id": f"s{i}", "date": f"2026-10-1{i}", "kind": "free", "summary": _session_summary(50 + 20 * i, 80)} for i in range(2)]
    out = pr.trends(two)
    th = next(s for s in out["sounds"] if s["sound"] == "th")
    assert th["trend"] is None and th["delta"] is None  # two sessions are not a trend
    assert pr.trends([])["n_sessions"] == 0


def test_progress_route_ignores_mock_sessions_and_answers(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from tests.test_api import record
    with TestClient(create_app(_settings(tmp_path))) as c:
        record(c)  # a mock-transcribed session never counts
        out = c.get("/api/pronunciation/progress").json()
        assert out["n_sessions"] == 0 and out["sounds"] == [] and out["recurring_words"] == []


def test_a_big_change_on_very_few_words_is_not_called_a_trend():
    sessions = [{"id": f"s{i}", "date": f"2026-10-1{i}", "kind": "free", "summary": _session_summary(th, 80, th_count=3)}
                for i, th in enumerate((90, 60, 40, 30))]  # falls by 60 points, but only 3 words per session
    th = next(s for s in pr.trends(sessions)["sounds"] if s["sound"] == "th")
    assert th["trend"] is None  # 3 words on each side is not enough to say anything


def test_a_weak_word_goes_to_the_notebook_once(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from tests.test_api import record
    with TestClient(create_app(_settings(tmp_path))) as c:
        sid = record(c)
        body = {"kind": "word", "source": "pronunciation", "session_id": sid, "intent": "Say “think” clearly (weak sound: th 38)", "english": "think"}
        first = c.post("/api/notebook", json=body)
        again = c.post("/api/notebook", json={**body, "english": "Think"})
        assert first.status_code == 201 and first.json()["item"]["english"] == "think" and first.json()["item"]["status"] == "learning"
        assert again.json()["existing"] is True and again.json()["item"]["id"] == first.json()["item"]["id"]
        assert len(c.get("/api/notebook").json()["items"]) == 1


def test_results_stored_before_the_cut_marker_existed_still_hide_half_words():
    def chunk(words):
        c = pr.summarize_chunk(_azure(words), 0.0)
        for w in c["words"]:
            w.pop("edge")  # the old format had no such key
        return {**c, "seconds": 28.0}
    old = {"seconds": 50.0, "unit": "s", "chunks": [chunk([("hello", 90, [("h", 90)]), ("mathematical", 40, [("th", 30)]), ("ifying", 20, [("f", 20)])]),
                                                     chunk([("fication", 25, [("k", 25)]), ("vision", 45, [("zh", 40)]), ("end", 90, [("n", 90)])])]}
    assert [w["word"] for w in pr.aggregate([old])["worst_words"]] == ["mathematical", "vision"]  # the cut ifying / fication are gone
