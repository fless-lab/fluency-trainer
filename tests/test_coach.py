import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.coach import parse_feedback
from app.config import Settings
from app.llm.anthropic import AnthropicLLM
from app.llm.base import LLMError
from app.llm.openrouter import OpenRouterLLM
from app.main import create_app
from tests.test_api import events, record


def make(tmp_path, llm="mock", cap=30, key="k", model="m"):
    return Settings(data_dir=tmp_path / "data", stt_provider="mock", assemblyai_key="", assemblyai_models=(),
                    max_upload_bytes=1024 * 1024, host="127.0.0.1", port=0, llm_provider=llm, llm_key=key,
                    llm_model=model, coach_max_per_day=cap)


def run(settings, fn):
    app = create_app(settings)
    with TestClient(app) as c:
        c.app_ref = app
        return fn(c)


def test_feedback_is_generated_stored_and_reused_without_new_spend(tmp_path):
    def go(c):
        sid = record(c)
        assert c.get("/api/state").json()["llm"]["configured"] is True
        assert c.get(f"/api/sessions/{sid}/coach").json() == {"coaching": None}
        first = c.post(f"/api/sessions/{sid}/coach")
        assert first.status_code == 200 and first.json()["reused"] is False
        content = first.json()["coaching"]["content"]
        assert content["patterns"][0]["examples"][0]["quote_found"] is True
        again = c.post(f"/api/sessions/{sid}/coach").json()
        assert again["reused"] is True
        assert sum(e["type"] == "coach.start" for e in events(c)) == 1
        c.post(f"/api/sessions/{sid}/coach", params={"regenerate": True})
        assert sum(e["type"] == "coach.start" for e in events(c)) == 2
        assert c.get(f"/api/sessions/{sid}/coach").json()["coaching"]["provider"] == "mock"
        assert "coaching" in c.get("/api/export").json()
    run(make(tmp_path), go)


def test_feedback_unavailable_or_refused_cleanly(tmp_path):
    def none(c):
        sid = record(c)
        assert c.get("/api/state").json()["llm"]["configured"] is False
        r = c.post(f"/api/sessions/{sid}/coach")
        assert r.status_code == 503 and "LLM_PROVIDER" in r.json()["detail"]
    run(make(tmp_path / "a", llm="none"), none)

    def not_done(c):
        sid = c.post("/api/sessions", json={"kind": "free"}).json()["id"]
        assert c.post(f"/api/sessions/{sid}/coach").status_code == 409
        assert c.post("/api/sessions/nope/coach").status_code == 404
    run(make(tmp_path / "b"), not_done)

    def missing_model(c):
        assert "LLM_MODEL" in c.get("/api/state").json()["llm"]["error"]
    run(make(tmp_path / "c", llm="openrouter", model=""), missing_model)


def test_daily_cap_stops_spending(tmp_path):
    def go(c):
        sid = record(c)
        assert c.post(f"/api/sessions/{sid}/coach").status_code == 200
        r = c.post(f"/api/sessions/{sid}/coach", params={"regenerate": True})
        assert r.status_code == 429 and "COACH_MAX_PER_DAY" in r.json()["detail"]
        assert any(e["type"] == "coach.cap_reached" for e in events(c))
    run(make(tmp_path, cap=1), go)


def test_parse_flags_invented_quotes_and_rejects_garbage():
    transcript = "So um I thought I was ready and I just learned some chapters"
    good = {"summary": "ok", "strengths": ["a", "b", "c"], "focus_next": "x", "phrases": [{"phrase": "p", "use": "u"}],
            "patterns": [{"tag": "Verb Tense!", "issue": "i", "why": "w", "examples": [
                {"said": "I just learned some chapters", "better": "I only studied some chapters"},
                {"said": "never said this", "better": "n/a"}]}]}
    out = parse_feedback("Here you go:\n```json\n" + json.dumps(good) + "\n```", transcript)
    assert out["patterns"][0]["tag"] == "verb-tense"
    assert [e["quote_found"] for e in out["patterns"][0]["examples"]] == [True, False]
    assert len(out["strengths"]) == 2
    for bad in ("no json here", "{broken", '{"summary": ""}', "[1, 2]"):
        with pytest.raises(ValueError):
            parse_feedback(bad, transcript)


REAL_CLIENT = httpx.AsyncClient


def _patch_client(monkeypatch, module, handler):
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handler), **kw))


def test_openrouter_request_shape_and_errors(monkeypatch):
    import app.llm.openrouter as mod
    seen = {}

    def ok(request):
        seen["url"], seen["auth"], seen["body"] = str(request.url), request.headers["authorization"], json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "hello"}}], "usage": {"total_tokens": 5}})

    _patch_client(monkeypatch, mod, ok)
    r = asyncio.run(OpenRouterLLM("KEY", "some/model").complete("sys", "usr", 100))
    assert r.text == "hello" and r.usage == {"total_tokens": 5}
    assert seen["url"] == "https://openrouter.ai/api/v1/chat/completions" and seen["auth"] == "Bearer KEY"
    assert seen["body"]["model"] == "some/model"
    assert [m["role"] for m in seen["body"]["messages"]] == ["system", "user"]

    _patch_client(monkeypatch, mod, lambda req: httpx.Response(401, text="bad key"))
    with pytest.raises(LLMError, match="401"):
        asyncio.run(OpenRouterLLM("KEY", "m").complete("s", "u", 10))
    _patch_client(monkeypatch, mod, lambda req: httpx.Response(200, json={"oops": 1}))
    with pytest.raises(LLMError, match="unexpected"):
        asyncio.run(OpenRouterLLM("KEY", "m").complete("s", "u", 10))


def test_anthropic_request_shape_and_errors(monkeypatch):
    import app.llm.anthropic as mod
    seen = {}

    def ok(request):
        seen["url"], seen["h"], seen["body"] = str(request.url), request.headers, json.loads(request.content)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "he"}, {"type": "text", "text": "llo"}],
                                         "usage": {"input_tokens": 3, "output_tokens": 2}})

    _patch_client(monkeypatch, mod, ok)
    r = asyncio.run(AnthropicLLM("KEY", "some-model").complete("sys", "usr", 100))
    assert r.text == "hello" and r.usage["output_tokens"] == 2
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["h"]["x-api-key"] == "KEY" and seen["h"]["anthropic-version"] == "2023-06-01"
    assert seen["body"]["system"] == "sys" and seen["body"]["messages"] == [{"role": "user", "content": "usr"}]
    assert seen["body"]["max_tokens"] == 100

    _patch_client(monkeypatch, mod, lambda req: httpx.Response(529, text="overloaded"))
    with pytest.raises(LLMError, match="529"):
        asyncio.run(AnthropicLLM("KEY", "m").complete("s", "u", 10))
