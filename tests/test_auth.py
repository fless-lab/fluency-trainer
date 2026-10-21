"""The password, the cookie and the limit on guesses, then the whole gate through the app."""
import os
import stat

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.config import Settings
from app.main import create_app


def test_password_is_hashed_checked_and_weak_ones_refused():
    h = auth.hash_password("correct horse battery")
    assert h.startswith("scrypt$") and "correct" not in h
    assert auth.verify_password("correct horse battery", h) and not auth.verify_password("correct horse batterx", h)
    assert not auth.verify_password("x", "garbage") and not auth.verify_password("x", "")
    assert auth.hash_password("same one here!!") != auth.hash_password("same one here!!")  # a different salt each time
    assert auth.check_strength("short") and auth.check_strength("aaaaaaaaaaaaaaaa") and auth.check_strength("a calm river at dawn") is None


def test_tokens_cannot_be_forged_or_used_after_they_expire_or_after_the_secret_changes():
    t = auth.Tokens(b"k" * 32)
    tok = t.issue(now=1000.0)
    assert t.valid(tok, now=1000.0 + 60)
    assert not t.valid(tok, now=1000.0 + auth.SESSION_DAYS * 86400 + 5)
    exp, nonce, sig = tok.split(".")
    assert not t.valid(f"{int(exp) + 99999}.{nonce}.{sig}") and not t.valid("") and not t.valid(None) and not t.valid("a.b")
    assert not auth.Tokens(b"z" * 32).valid(tok, now=1000.0 + 60)  # a new secret logs everybody out


def test_secret_file_is_private_and_rotating_changes_it(tmp_path):
    p = tmp_path / "secret.key"
    a = auth.load_secret(p)
    assert auth.load_secret(p) == a and stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert auth.load_secret(p, rotate=True) != a


def test_five_wrong_guesses_then_a_wait_and_a_good_one_clears_the_count():
    rl = auth.RateLimiter(attempts=5, window=300)
    for i in range(5):
        assert rl.wait("ip", now=100 + i) == 0
        rl.failed("ip", now=100 + i)
    assert 290 <= rl.wait("ip", now=105) <= 300
    assert rl.wait("other", now=105) == 0 and rl.wait("ip", now=100 + 301) == 0  # per address, and it expires
    rl.failed("ip", now=500)
    rl.ok("ip")
    assert rl.wait("ip", now=501) == 0


def make(tmp_path, password=None, host="127.0.0.1"):
    s = Settings(data_dir=tmp_path / "data", stt_provider="mock", assemblyai_key="", assemblyai_models=(), max_upload_bytes=1024 * 1024,
                 host=host, port=0, app_password_hash=auth.hash_password(password) if password else "")
    return create_app(s)


def test_without_a_password_the_app_is_open_as_before(tmp_path):
    with TestClient(make(tmp_path)) as c:
        assert c.get("/api/state").status_code == 200 and c.get("/").status_code == 200
        assert c.get("/api/auth/status").json() == {"enabled": False, "authed": True}


def test_with_a_password_nothing_is_reachable_until_login(tmp_path):
    with TestClient(make(tmp_path, "a calm river at dawn")) as c:
        assert c.get("/api/state").status_code == 401 and c.get("/api/export").status_code == 401
        assert "password" in c.get("/").text.lower() and 'id="pw"' in c.get("/").text  # the page is the login form
        assert c.get("/api/auth/status").json() == {"enabled": True, "authed": False}
        bad = c.post("/api/login", json={"password": "wrong wrong wrong"})
        assert bad.status_code == 401 and "ft_session" not in bad.headers.get("set-cookie", "")
        good = c.post("/api/login", json={"password": "a calm river at dawn"})
        cookie = good.headers["set-cookie"].lower()
        assert good.status_code == 200 and "httponly" in cookie and "samesite=lax" in cookie
        assert c.get("/api/state").status_code == 200 and "Fluency" in c.get("/").text
        assert c.post("/api/logout").status_code == 200 and c.get("/api/state").status_code == 401


def test_login_is_slowed_after_five_wrong_tries(tmp_path):
    with TestClient(make(tmp_path, "a calm river at dawn")) as c:
        codes = [c.post("/api/login", json={"password": f"wrong guess {i}"}).status_code for i in range(7)]
        assert codes[:5] == [401] * 5 and codes[5:] == [429, 429]
        assert c.post("/api/login", json={"password": "a calm river at dawn"}).status_code == 429  # even the right one waits


def test_a_post_from_another_website_is_refused_with_or_without_a_password(tmp_path):
    with TestClient(make(tmp_path)) as c:
        r = c.post("/api/start", json={}, headers={"Origin": "https://evil.example"})
        assert r.status_code == 403
        assert c.post("/api/start", json={}, headers={"Origin": "http://testserver"}).status_code in (200, 201)
        assert c.post("/api/start", json={}).status_code in (200, 201)  # no Origin: not a browser cross-site request


def test_settings_refuse_requests_that_came_through_a_proxy_even_from_this_machine(tmp_path):
    with TestClient(make(tmp_path)) as c:
        assert c.get("/api/settings").status_code == 200
        assert c.get("/api/settings", headers={"X-Forwarded-For": "100.64.0.7"}).status_code == 403
        assert c.get("/api/settings", headers={"Tailscale-User-Login": "me@example.com"}).status_code == 403


def test_the_password_is_set_and_removed_from_this_computer_and_applies_at_once(tmp_path, monkeypatch):
    import app.main as main
    monkeypatch.setattr(main, "ROOT", tmp_path)
    (tmp_path / ".env").write_text("STT_PROVIDER=mock\n")
    with TestClient(make(tmp_path)) as c:
        assert c.post("/api/auth/password", json={"password": "short"}).status_code == 422
        r = c.post("/api/auth/password", json={"password": "a calm river at dawn"})
        assert r.status_code == 200 and "APP_PASSWORD_HASH=scrypt$" in (tmp_path / ".env").read_text()
        assert "calm river" not in (tmp_path / ".env").read_text()
        assert c.get("/api/state").status_code == 200  # this browser was logged in by setting it
        other = TestClient(c.app)
        assert other.get("/api/state").status_code == 401
        assert c.post("/api/auth/password", json={"password": None}).status_code == 200  # remove
        assert other.get("/api/state").status_code == 200 and "APP_PASSWORD_HASH=\n" in (tmp_path / ".env").read_text() + "\n"
    with TestClient(make(tmp_path), client=("203.0.113.5", 1)) as far:
        assert far.post("/api/auth/password", json={"password": "a calm river at dawn"}).status_code == 403


def test_the_app_refuses_to_listen_on_the_network_without_a_password(monkeypatch):
    import app.__main__ as entry
    from app.config import Settings
    def fake(host, pw):
        return Settings(data_dir=__import__("pathlib").Path("/tmp/x"), stt_provider="mock", assemblyai_key="", assemblyai_models=(),
                        max_upload_bytes=1, host=host, port=1, app_password_hash=pw)
    monkeypatch.setattr(entry, "load_settings", lambda: fake("0.0.0.0", ""))
    with pytest.raises(SystemExit) as e:
        entry.main()
    assert "Refusing to listen" in str(e.value)


def test_the_page_can_be_installed_on_a_home_screen_and_the_icons_are_public(tmp_path):
    with TestClient(make(tmp_path, "a calm river at dawn")) as c:
        assert c.get("/static/manifest.webmanifest").status_code == 200  # served without login: no data in it
        assert c.get("/static/icon-180.png").content[:4] == b"\x89PNG"
        page = TestClient(make(tmp_path)).get("/").text
        assert 'rel="manifest"' in page and "apple-touch-icon" in page and "apple-mobile-web-app-capable" in page
