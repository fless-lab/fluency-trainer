"""Backups: what goes in, that the copy of the database is usable, the way back, and the limits."""
import json
import os
import sqlite3
import time
import zipfile
from datetime import datetime, timezone

import pytest

from app import backup as bk


def make_data(tmp_path):
    d = tmp_path / "data"
    for sub in ("transcripts", "audio", "conv", "say"):
        (d / sub).mkdir(parents=True)
    db = sqlite3.connect(d / "fluency.db")
    db.execute("create table sessions (id text, note text)")
    db.execute("insert into sessions values ('s1', 'hello')")
    db.commit()
    db.close()
    (d / "transcripts" / "s1.json").write_text('{"text": "hi"}')
    (d / "audio" / "s1.webm").write_bytes(b"A" * 1000)
    (d / "conv" / "c_user0.wav").write_bytes(b"B" * 500)
    (d / "say" / "cache.wav").write_bytes(b"C" * 300)
    (d / "events.jsonl").write_text('{"type": "x"}\n')
    return d


def test_light_and_full_backups_hold_the_right_things_and_the_database_is_usable(tmp_path):
    d = make_data(tmp_path)
    light = bk.make_backup(d, tmp_path / "b" / "light.zip", full=False, app_version="9")
    full = bk.make_backup(d, tmp_path / "b" / "full.zip", full=True)
    names = lambda p: set(zipfile.ZipFile(p).namelist())  # noqa: E731
    assert names(light["path"]) == {"fluency.db", "events.jsonl", "transcripts/s1.json", "manifest.json"}
    assert names(full["path"]) == names(light["path"]) | {"audio/s1.webm", "conv/c_user0.wav"}  # never the voice cache
    assert json.loads(zipfile.ZipFile(light["path"]).read("manifest.json"))["kind"] == "light"
    assert full["size"] > light["size"] and not list((tmp_path / "b").glob("*.part"))
    out = tmp_path / "restored"
    assert bk.restore(tmp_path / "b" / "full.zip", out) == 5
    assert sqlite3.connect(out / "fluency.db").execute("select note from sessions").fetchone() == ("hello",)
    assert (out / "audio" / "s1.webm").read_bytes() == b"A" * 1000


def test_restore_refuses_a_zip_that_points_outside_and_a_folder_that_has_data(tmp_path):
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("../escaped.txt", "x")
    with pytest.raises(SystemExit):
        bk.restore(evil, tmp_path / "out")
    assert not (tmp_path / "escaped.txt").exists()
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "file").write_text("mine")
    good = bk.make_backup(make_data(tmp_path), tmp_path / "good.zip", full=False)
    with pytest.raises(SystemExit):
        bk.restore(good["path"], busy)
    assert (busy / "file").read_text() == "mine"


def test_only_the_newest_automatic_backups_are_kept_and_manual_ones_never(tmp_path):
    f = tmp_path / "backups"
    f.mkdir()
    for i in range(20):
        p = f / f"auto-202610{i + 1:02d}.zip"
        p.write_bytes(b"z")
    (f / "backup-mine.zip").write_bytes(b"z")
    removed = bk.prune_automatic(f, keep=14)
    assert len(removed) == 6 and removed[0] == "auto-20261006.zip" and (f / "backup-mine.zip").exists()
    assert len(list(f.glob("auto-*.zip"))) == 14


def test_an_automatic_backup_is_due_once_a_day(tmp_path):
    f = tmp_path / "backups"
    assert bk.automatic_due(f) is True  # none yet
    f.mkdir()
    p = f / "auto-20261021.zip"
    p.write_bytes(b"z")
    assert bk.automatic_due(f) is False
    old = time.time() - 25 * 3600
    os.utime(p, (old, old))
    assert bk.automatic_due(f) is True


def _client(tmp_path, remote=False):
    from fastapi.testclient import TestClient
    from app.config import Settings
    from app.main import create_app
    s = Settings(data_dir=tmp_path / "data", stt_provider="mock", assemblyai_key="", assemblyai_models=(), max_upload_bytes=1024 * 1024, host="127.0.0.1", port=0)
    return TestClient(create_app(s), client=("203.0.113.9", 1) if remote else ("127.0.0.1", 1))


def test_routes_make_list_and_download_backups_and_refuse_odd_names_and_strangers(tmp_path):
    from tests.test_api import record
    with _client(tmp_path) as c:
        record(c)
        for _ in range(40):  # the daily backup is made when the app starts
            if list((tmp_path / "data" / "backups").glob("auto-*.zip")):
                break
            time.sleep(0.05)
        made = c.post("/api/backups", params={"full": "true"})
        assert made.status_code == 201 and made.json()["kind"] == "full"
        names = [b["name"] for b in c.get("/api/backups").json()["items"]]
        assert made.json()["name"] in names and any(n.startswith("auto-") for n in names)
        got = c.get(f"/api/backups/{made.json()['name']}")
        assert got.status_code == 200 and got.content[:2] == b"PK"
        assert c.get("/api/backups/..%2Ffluency.db").status_code == 404
        assert c.get("/api/backups/nope.zip").status_code == 404
    with _client(tmp_path, remote=True) as c:
        assert c.get("/api/backups").status_code == 403 and c.post("/api/backups").status_code == 403
