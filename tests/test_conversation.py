import io

from fastapi.testclient import TestClient

from app.config import Settings
from app.conversation import clean_reply, stitch
from app.main import create_app


def settings(tmp_path, **kw):
    base = dict(data_dir=tmp_path, stt_provider="mock", assemblyai_key="", assemblyai_models=(), max_upload_bytes=10_000_000,
                host="127.0.0.1", port=0, llm_provider="mock", tts_provider="mock")
    base.update(kw)
    return Settings(**base)


def test_stitch_removes_gaps_between_turns():
    a = [{"text": "hello", "start": 500, "end": 900}, {"text": "there", "start": 1000, "end": 1400}]
    b = [{"text": "again", "start": 3000, "end": 3400}]
    words, text, seconds = stitch([{"words": a}, {"words": b}])
    assert text == "hello there again"
    assert words[0]["start"] == 0
    assert words[2]["start"] - words[1]["end"] == 200  # below the pause threshold
    assert seconds > 0


def test_clean_reply():
    assert clean_reply("Partner: **Hi** there") == "Hi there"
    assert len(clean_reply("word " * 200).split()) <= 60


def test_conversation_flow(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as c:
        r = c.post("/api/conversations", json={"scenario": "A job interview for a developer role", "minutes": 5})
        assert r.status_code == 201, r.text
        sid = r.json()["id"]
        first = r.json()["turn"]
        assert first["audio_url"] and c.get(first["audio_url"]).status_code == 200
        for i in range(2):
            t = c.post(f"/api/conversations/{sid}/turn", files={"file": ("a.webm", io.BytesIO(b"x" * 2000), "audio/webm")},
                       data={"duration_s": "9", "mime": "audio/webm", "latency_ms": "1800"})
            assert t.status_code == 200, t.text
            assert t.json()["empty"] is False and t.json()["turn"]["text"]
        end = c.post(f"/api/conversations/{sid}/end").json()
        assert end["status"] == "done" and end["delays_ms"] == [1800, 1800]
        s = c.get(f"/api/sessions/{sid}").json()
        assert s["status"] == "done" and s["kind"] == "conversation"
        assert c.get(f"/api/conversations/{sid}").json()["turns"][0]["role"] == "partner"
        assert c.post(f"/api/conversations/{sid}/turn", files={"file": ("a.webm", io.BytesIO(b"x" * 2000), "audio/webm")},
                      data={"duration_s": "9", "mime": "audio/webm"}).status_code == 409


def test_chat_cap(tmp_path):
    with TestClient(create_app(settings(tmp_path, chat_max_per_day=1))) as c:
        assert c.post("/api/conversations", json={"scenario": "chat"}).status_code == 201
        assert c.post("/api/conversations", json={"scenario": "chat"}).status_code == 429


def test_no_voice_falls_back(tmp_path):
    with TestClient(create_app(settings(tmp_path, tts_provider="none"))) as c:
        r = c.post("/api/conversations", json={"scenario": "chat"}).json()
        assert r["turn"]["audio_url"] is None and r["turn"]["voice_note"]


def test_pcm_is_wrapped(monkeypatch):
    import asyncio, httpx
    from app.tts.openai_compat import OpenAICompatTTS

    class R:
        status_code = 200; content = b"\x00\x00" * 100; headers = {"content-type": "audio/pcm"}; text = ""

    class C:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k): return R()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: C())
    out = asyncio.run(OpenAICompatTTS("openrouter", "k", "m", "v").speak("hello"))
    assert out.mime == "audio/wav" and out.audio[:4] == b"RIFF"
