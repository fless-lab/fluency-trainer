"""The click-to-hear route: the same text is made once and cached, with limits on length and daily use."""
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.test_api import events


def make(tmp_path, tts="mock", cap=60000):
    return Settings(data_dir=tmp_path / "data", stt_provider="mock", assemblyai_key="", assemblyai_models=(),
                    max_upload_bytes=1024 * 1024, host="127.0.0.1", port=0, tts_provider=tts, tts_max_chars_per_day=cap)


def test_a_word_is_made_once_then_served_from_the_cache(tmp_path):
    app = create_app(make(tmp_path))
    with TestClient(app) as c:
        c.app_ref = app
        a = c.get("/api/say", params={"text": "think"})
        b = c.get("/api/say", params={"text": "  think  "})  # same text after tidying the spaces
        other = c.get("/api/say", params={"text": "thing"})
        assert a.status_code == 200 and a.content[:4] == b"RIFF" and a.content == b.content
        assert "immutable" in a.headers["cache-control"] and other.status_code == 200
        assert sum(e["type"] == "say.made" for e in events(c)) == 2  # think once, thing once
        assert len(list((tmp_path / "data" / "say").glob("*.wav"))) == 2


def test_empty_long_and_capped_requests_are_refused(tmp_path):
    with TestClient(create_app(make(tmp_path, cap=20))) as c:
        assert c.get("/api/say", params={"text": "   "}).status_code == 422
        assert c.get("/api/say", params={"text": "x " * 200}).status_code == 422
        assert c.get("/api/say", params={"text": "hello"}).status_code == 200
        assert c.get("/api/say", params={"text": "a sentence that is clearly longer than the rest"}).status_code == 429
        assert c.get("/api/say", params={"text": "hello"}).status_code == 200  # already made: free, even when capped


def test_without_a_voice_the_route_says_so(tmp_path):
    with TestClient(create_app(make(tmp_path, tts="none"))) as c:
        r = c.get("/api/say", params={"text": "hello"})
        assert r.status_code == 503
