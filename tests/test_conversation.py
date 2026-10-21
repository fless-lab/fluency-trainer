import io
import json

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
        first = r.json()["sentences"][0]
        assert first["audio_url"] and c.get(first["audio_url"]).status_code == 200
        for i in range(2):
            t = c.post(f"/api/conversations/{sid}/turn", files={"file": ("a.webm", io.BytesIO(b"x" * 2000), "audio/webm")},
                       data={"duration_s": "9", "mime": "audio/webm", "latency_ms": "1800", "live_text": "I think it went well" if i else ""})
            assert t.status_code == 200, t.text
            evs = [json.loads(x) for x in t.text.splitlines() if x]
            assert evs[0]["type"] == "user" and evs[0]["live"] is bool(i)
            assert any(e["type"] == "sentence" for e in evs) and evs[-1]["type"] == "done"
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
        assert r["sentences"][0]["audio_url"] is None and r["sentences"][0]["voice_note"]


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


def test_split_ready_streaming():
    from app.conversation import split_ready
    out, rest = split_ready("Hi there! How has your week been so far? And what")
    assert out == ["Hi there! How has your week been so far?"] and rest == "And what"
    out, rest = split_ready("Oh. Nice to hear that you did that.", final=True)
    assert out == ["Oh. Nice to hear that you did that."] and rest == ""


def test_format_retry(monkeypatch):
    import asyncio, httpx
    from app.tts.openai_compat import OpenAICompatTTS
    calls = []

    class R:
        def __init__(self, code, content=b"", text=""):
            self.status_code, self.content, self.text, self.headers = code, content, text, {"content-type": "audio/pcm"}

    class C:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None, headers=None):
            calls.append(json["response_format"])
            if json["response_format"] != "pcm":
                return R(400, text='{"error":{"message":"Gemini TTS only supports response_format=\\"pcm\\". Got \\"mp3\\"."}}')
            return R(200, b"\x00\x00" * 50)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: C())
    out = asyncio.run(OpenAICompatTTS("openrouter", "k", "some/model", "v").speak("hello"))
    assert calls == ["mp3", "pcm"] and out.audio[:4] == b"RIFF"
    out = asyncio.run(OpenAICompatTTS("openrouter", "k", "google/gemini-x-tts", "v").speak("hello"))
    assert calls[-1] == "pcm"
