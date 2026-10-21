"""Access to the app from another device: one password, a signed cookie, and a limit on wrong guesses.

Nothing here runs unless a password is set (the default is open, as before, for use on one computer). With a password, every
request needs the cookie that a correct login gives; the page itself then shows the login form. The password is stored only
as a salted scrypt hash. The cookie holds an expiry and a random value signed with a secret kept in the data folder, so it
cannot be forged and logging everyone out is just changing the secret.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path

COOKIE = "ft_session"
MIN_LENGTH = 12
SESSION_DAYS = 30
SCRYPT = {"n": 2**14, "r": 8, "p": 1}
# Any of these means the request came through a proxy (Tailscale, Caddy, a tunnel): the connection looks local but is not.
PROXY_HEADERS = ("x-forwarded-for", "forwarded", "x-real-ip", "x-forwarded-host", "cf-connecting-ip", "tailscale-user-login")


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **SCRYPT)
    return f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt, dk = stored.split("$")
        if kind != "scrypt":
            return False
        calc = hashlib.scrypt(password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(calc, _unb64(dk))
    except (ValueError, TypeError):
        return False


def check_strength(password: str) -> str | None:
    """Why this password is refused, or None."""
    if len(password) < MIN_LENGTH:
        return f"Use at least {MIN_LENGTH} characters: a short passphrase of a few words is easier to remember than symbols."
    if len(set(password)) < 5:
        return "Too repetitive."
    return None


def load_secret(path: Path, rotate: bool = False) -> bytes:
    """The signing secret, made on first use and kept with owner-only permissions. Rotating it logs everyone out."""
    if path.is_file() and not rotate:
        return path.read_bytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(32)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
    os.replace(tmp, path)
    return key


class Tokens:
    """A login cookie names who it is for and carries a version number. The version is changed when that person's code is
    renewed or they are blocked, which makes every cookie they had stop working without touching anyone else's."""

    def __init__(self, secret: bytes):
        self.secret = secret

    def _sig(self, payload: str) -> str:
        return _b64(hmac.new(self.secret, payload.encode(), hashlib.sha256).digest())

    def issue(self, uid: str, version: int = 1, now: float | None = None) -> str:
        expires = int((now or time.time()) + SESSION_DAYS * 86400)
        payload = f"{uid}.{version}.{expires}.{secrets.token_urlsafe(12)}"
        return f"{payload}.{self._sig(payload)}"

    def read(self, token: str | None, now: float | None = None) -> tuple[str, int] | None:
        """(who, version) when the cookie is genuine and not expired, else None. The caller checks the version is still current."""
        if not token:
            return None
        try:
            uid, version, expires, nonce, sig = token.split(".")
            if hmac.compare_digest(sig, self._sig(f"{uid}.{version}.{expires}.{nonce}")) and int(expires) > (now or time.time()):
                return uid, int(version)
        except ValueError:
            pass
        return None


class RateLimiter:
    """A few wrong passwords per address, then a wait. A correct one clears the count."""

    def __init__(self, attempts: int = 5, window: float = 300.0):
        self.attempts, self.window = attempts, window
        self.fails: dict[str, list[float]] = {}

    def wait(self, key: str, now: float | None = None) -> int:
        """Seconds to wait before this address may try again (0 when it may)."""
        now = now or time.time()
        recent = [t for t in self.fails.get(key, []) if now - t < self.window]
        self.fails[key] = recent
        return 0 if len(recent) < self.attempts else int(self.window - (now - recent[0])) + 1

    def failed(self, key: str, now: float | None = None) -> None:
        self.fails.setdefault(key, []).append(now or time.time())

    def ok(self, key: str) -> None:
        self.fails.pop(key, None)


def through_proxy(headers) -> bool:
    return any(h in headers for h in PROXY_HEADERS)
