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
