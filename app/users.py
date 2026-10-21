"""The people who may use this app besides its owner, and their invitation codes.

The owner (the admin) is the person at the computer; their data stays where it always was. Every invited person gets a code like
K7QM-4TXD-92PA-X8WC, shown once to the owner, who passes it on. Only a hash of the code is kept. The first group is a public
label used to find the person quickly; the whole code is checked against the hash. Each person has a folder of their own
(data/users/<id>/) holding their own database and recordings, so nothing of one person can be reached from another's session.

The list lives in data/users.json, written atomically.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import auth

ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"  # no 0/O, 1/I/L: a code is read aloud and typed by hand
GROUPS, GROUP_LEN = 4, 4  # 30^16 is about 2^78: not guessable, and the login is rate limited as well
ADMIN = "admin"


def new_code() -> str:
    return "-".join("".join(secrets.choice(ALPHABET) for _ in range(GROUP_LEN)) for _ in range(GROUPS))


def normalise_code(raw: str) -> str | None:
    """What the person typed, made into the canonical form, or None if it cannot be a code (so it is tried as a password)."""
    chars = "".join(c for c in raw.upper() if c.isalnum())
    if len(chars) != GROUPS * GROUP_LEN or any(c not in ALPHABET for c in chars):
        return None
    return "-".join(chars[i:i + GROUP_LEN] for i in range(0, len(chars), GROUP_LEN))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Registry:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()

    # storage -------------------------------------------------------------------------------------
    def _read(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"admin_version": 1, "users": []}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)

    # reading -------------------------------------------------------------------------------------
    def list(self) -> list[dict[str, Any]]:
        with self.lock:
            return [self._public(u) for u in self._read()["users"]]

    def get(self, uid: str) -> dict[str, Any] | None:
        with self.lock:
            u = next((u for u in self._read()["users"] if u["id"] == uid), None)
            return self._public(u) if u else None

    def any_users(self) -> bool:
        with self.lock:
            return bool(self._read()["users"])

    @staticmethod
    def _public(u: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in u.items() if k != "code_hash"}  # the hash never leaves this module

    def version(self, uid: str) -> int | None:
        """The number a login cookie must carry. It changes when the code is renewed or the person removed, which logs them out."""
        with self.lock:
            d = self._read()
            if uid == ADMIN:
                return d.get("admin_version", 1)
            u = next((u for u in d["users"] if u["id"] == uid), None)
            return None if u is None or u.get("disabled") else u.get("version", 1)

    # changing ------------------------------------------------------------------------------------
    def bump_admin(self) -> None:
        with self.lock:
            d = self._read()
            d["admin_version"] = d.get("admin_version", 1) + 1
            self._write(d)

    def create(self, name: str, note: str = "") -> tuple[dict[str, Any], str]:
        name = " ".join(name.split())[:60]
        if not name:
            raise ValueError("A name is needed.")
        with self.lock:
            d = self._read()
            code = new_code()
            while any(u["lookup"] == code[:GROUP_LEN] for u in d["users"]):
                code = new_code()
            user = {"id": "u" + secrets.token_hex(4), "name": name, "note": " ".join(note.split())[:200], "lookup": code[:GROUP_LEN],
                    "code_hash": auth.hash_password(code), "created": _now(), "last_login": None, "disabled": False, "version": 1,
                    "native_language": "", "consent_at": None, "limits": {}}
            d["users"].append(user)
            self._write(d)
            return self._public(user), code

    def renew_code(self, uid: str) -> str:
        with self.lock:
            d = self._read()
            u = self._find(d, uid)
            code = new_code()
            while any(x["lookup"] == code[:GROUP_LEN] and x["id"] != uid for x in d["users"]):
                code = new_code()
            u.update(lookup=code[:GROUP_LEN], code_hash=auth.hash_password(code), version=u.get("version", 1) + 1, disabled=False)
            self._write(d)
            return code

    def set_disabled(self, uid: str, disabled: bool) -> None:
        with self.lock:
            d = self._read()
            u = self._find(d, uid)
            u["disabled"] = disabled
            u["version"] = u.get("version", 1) + 1
            self._write(d)

    def update(self, uid: str, **fields: Any) -> dict[str, Any]:
        allowed = {"name", "note", "native_language", "limits", "consent_at", "last_login"}
        with self.lock:
            d = self._read()
            u = self._find(d, uid)
            for k, v in fields.items():
                if k in allowed:
                    u[k] = " ".join(v.split())[:60] if k in ("name", "native_language") and isinstance(v, str) else v
            self._write(d)
            return self._public(u)

    def delete(self, uid: str) -> None:
        with self.lock:
            d = self._read()
            self._find(d, uid)
            d["users"] = [u for u in d["users"] if u["id"] != uid]
            self._write(d)

    @staticmethod
    def _find(d: dict[str, Any], uid: str) -> dict[str, Any]:
        u = next((u for u in d["users"] if u["id"] == uid), None)
        if u is None:
            raise KeyError(uid)
        return u

    # logging in ----------------------------------------------------------------------------------
    def check_code(self, raw: str) -> dict[str, Any] | None:
        """The person this code belongs to, or None. Only one hash is computed: the first group tells which person to check."""
        code = normalise_code(raw)
        if code is None:
            return None
        with self.lock:
            d = self._read()
            u = next((u for u in d["users"] if u["lookup"] == code[:GROUP_LEN] and not u.get("disabled")), None)
            if u is None or not auth.verify_password(code, u["code_hash"]):
                return None
            u["last_login"] = _now()
            self._write(d)
            return self._public(u)
