"""Backups of the data folder, and the way back.

A backup is one zip. The database is copied with SQLite's own backup call, so the copy is consistent even while the app is
writing. The light kind (database, transcripts, event log) is small and is made every day by the app; the full kind also holds
your recordings. The sound cache of the click-to-hear voice is left out: it can be made again.

Restore from a terminal, never from the running app:
    python -m app.backup restore backup.zip --to ./data-restored
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

KEEP_AUTOMATIC = 14
LIGHT_DIRS = ("transcripts",)
FULL_DIRS = ("transcripts", "audio", "conv")


def _snapshot_db(db_path: Path, into: Path, name: str = "fluency.db") -> Path:
    out = into / name
    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(out)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return out


def _add_folder(z: zipfile.ZipFile, folder: Path, prefix: str, full: bool, scratch: Path, counts: dict[str, int]) -> None:
    db = folder / "fluency.db"
    if db.is_file():
        snap = scratch / (prefix.replace("/", "_") + "fluency.db")
        z.write(_snapshot_db(db, snap.parent, snap.name), prefix + "fluency.db")
        counts["files"] += 1
    events = folder / "events.jsonl"
    if events.is_file():
        z.write(events, prefix + "events.jsonl")
        counts["files"] += 1
    for name in (FULL_DIRS if full else LIGHT_DIRS):
        for f in sorted((folder / name).rglob("*")) if (folder / name).is_dir() else []:
            if f.is_file():
                z.write(f, f"{prefix}{name}/{f.relative_to(folder / name).as_posix()}")
                counts["files"] += 1
                counts["bytes"] += f.stat().st_size


def make_backup(data_dir: Path, dest: Path, full: bool, app_version: str = "", include_users: bool = True) -> dict[str, Any]:
    """Write the zip to dest (atomically) and return what it holds. With include_users, the invited people's folders and the list
    of people (without anything but hashes) go in as well, under users/."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    counts = {"files": 0, "bytes": 0}
    with tempfile.TemporaryDirectory() as scratch, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        _add_folder(z, data_dir, "", full, Path(scratch), counts)
        if include_users and (data_dir / "users").is_dir():
            for person in sorted((data_dir / "users").iterdir()):
                if person.is_dir():
                    _add_folder(z, person, f"users/{person.name}/", full, Path(scratch), counts)
            if (data_dir / "users.json").is_file():
                z.write(data_dir / "users.json", "users.json")
                counts["files"] += 1
        manifest = {"app_version": app_version, "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "kind": "full" if full else "light", **counts}
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
    os.replace(tmp, dest)
    return {**manifest, "path": str(dest), "size": dest.stat().st_size}


def list_backups(folder: Path) -> list[dict[str, Any]]:
    out = []
    for f in sorted(folder.glob("*.zip"), reverse=True) if folder.is_dir() else []:
        out.append({"name": f.name, "size": f.stat().st_size, "modified": datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")})
    return out


def prune_automatic(folder: Path, keep: int = KEEP_AUTOMATIC) -> list[str]:
    """Keep the newest automatic backups. Backups you made yourself (other names) are never removed."""
    old = sorted(folder.glob("auto-*.zip"), reverse=True)[keep:]
    for f in old:
        f.unlink(missing_ok=True)
    return [f.name for f in old]


def automatic_due(folder: Path, now: datetime | None = None) -> bool:
    """True when there is no automatic backup from the last 24 hours."""
    now = now or datetime.now(timezone.utc)
    newest = max(folder.glob("auto-*.zip"), key=lambda f: f.stat().st_mtime, default=None) if folder.is_dir() else None
    return newest is None or now.timestamp() - newest.stat().st_mtime > 24 * 3600


def restore(zip_path: Path, to: Path) -> int:
    """Unpack a backup into a new or empty folder. Refuses paths that would escape it, and a folder that already has data."""
    to = to.resolve()
    if to.exists() and any(to.iterdir()):
        raise SystemExit(f"{to} is not empty: restore into a new folder, then swap it in once you have looked at it.")
    to.mkdir(parents=True, exist_ok=True)
    n = 0
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            target = (to / info.filename).resolve()
            if to not in target.parents and target != to:
                raise SystemExit(f"refusing {info.filename}: it points outside the folder")
            if info.filename == "manifest.json" or info.is_dir():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, "wb") as dst:
                dst.write(src.read())
            n += 1
    return n


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m app.backup")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create", help="make a backup now")
    c.add_argument("dest", nargs="?", default=None)
    c.add_argument("--light", action="store_true", help="leave the recordings out")
    r = sub.add_parser("restore", help="unpack a backup into a new folder")
    r.add_argument("zip")
    r.add_argument("--to", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "create":
        from .config import load_settings
        data = load_settings().data_dir
        dest = Path(a.dest) if a.dest else data / "backups" / f"backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
        print(json.dumps(make_backup(data, dest, full=not a.light), indent=2))
    else:
        print(f"restored {restore(Path(a.zip), Path(a.to))} files into {a.to}")


if __name__ == "__main__":
    main(sys.argv[1:])
