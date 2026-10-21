"""One folder and one database per person, chosen by who is making the request.

The owner (admin) keeps the data folder where it always was. Everyone else lives in data/users/<id>/. The routes of the app
still say `db.update_session(...)` or `data / "audio"`; what these names point to is decided at that moment by a context
variable that the access check sets for each request (tasks started by a request inherit it). If nobody has been set, using the
database or the folders is an error, never a silent fallback to the owner's data.
"""

from __future__ import annotations

import shutil
import threading
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .db import DB
from .eventlog import EventLog
from .users import ADMIN, Registry

SUBDIRS = ("audio", "transcripts", "conv")


@dataclass
class Ctx:
    id: str
    name: str
    dir: Path
    db: DB
    events: EventLog
    is_admin: bool
    native_language: str = ""


CURRENT: ContextVar[Ctx | None] = ContextVar("current_user", default=None)


class NoUser(RuntimeError):
    """Data was asked for while no person was identified."""


def current() -> Ctx:
    ctx = CURRENT.get()
    if ctx is None:
        raise NoUser("No user is set for this work: refusing to guess whose data to use.")
    return ctx


class DBProxy:
    def __getattr__(self, name: str) -> Any:
        return getattr(current().db, name)


class EventsProxy:
    """Events go to the current person's log; outside a request (login, startup) to the owner's, which doubles as the system log."""

    def __init__(self, system: EventLog):
        self._system = system

    def __getattr__(self, name: str) -> Any:
        ctx = CURRENT.get()
        return getattr(ctx.events if ctx else self._system, name)


class UserPath:
    """A path inside the current person's folder, worked out when it is used, not when it is written."""

    def __init__(self, parts: tuple[str, ...] = ()):
        object.__setattr__(self, "_parts", parts)

    def _real(self) -> Path:
        return current().dir.joinpath(*self._parts)

    def __truediv__(self, other: Any) -> "UserPath":
        return UserPath(self._parts + (str(other),))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real(), name)

    def __fspath__(self) -> str:
        return str(self._real())

    def __str__(self) -> str:
        return str(self._real())


class Tenants:
    def __init__(self, root: Path, registry: Registry):
        self.root, self.registry = root, registry
        self.lock = threading.RLock()
        self.cache: dict[str, Ctx] = {}
        self.admin = self._make(ADMIN, "Owner", root, True)

    def _make(self, uid: str, name: str, folder: Path, is_admin: bool) -> Ctx:
        for sub in SUBDIRS:
            (folder / sub).mkdir(parents=True, exist_ok=True)
        (folder / "events.jsonl").touch()  # a new person has a (empty) log from the start
        ctx = Ctx(uid, name, folder, DB(folder / "fluency.db"), EventLog(folder / "events.jsonl"), is_admin)
        self.cache[uid] = ctx
        return ctx

    def for_user(self, uid: str) -> Ctx | None:
        """The context of an invited person (not blocked), or None. Their profile is read fresh each time."""
        if uid == ADMIN:
            return self.admin
        person = self.registry.get(uid)
        if person is None or person.get("disabled"):
            return None
        with self.lock:
            ctx = self.cache.get(uid) or self._make(uid, person["name"], self.root / "users" / uid, False)
        ctx.name, ctx.native_language = person["name"], person.get("native_language", "")
        return ctx

    def everyone(self) -> list[Ctx]:
        out = [self.admin]
        for person in self.registry.list():
            ctx = self.for_user(person["id"])
            if ctx:
                out.append(ctx)
        return out

    def erase(self, uid: str) -> None:
        """Delete a person's folder. Their database connection is dropped first."""
        with self.lock:
            self.cache.pop(uid, None)
        folder = (self.root / "users" / uid).resolve()
        if (self.root / "users").resolve() in folder.parents and folder.is_dir():
            shutil.rmtree(folder)
