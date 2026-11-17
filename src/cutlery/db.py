"""SQLite database with numbered migrations (migrations/NNN_name.sql), applied automatically at start."""
from __future__ import annotations

import contextlib
import datetime
import importlib.resources
import sqlite3
import threading
import time


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path):
        self.path = str(path)
        self._local = threading.local()
        with self.connect() as con:
            migrate(con)

    def connect(self) -> sqlite3.Connection:
        con = getattr(self._local, "con", None)
        if con is None:
            con = sqlite3.connect(self.path, timeout=30, isolation_level=None)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA foreign_keys=ON")
            con.execute("PRAGMA busy_timeout=30000")
            self._local.con = con
        return contextlib.nullcontext(con)

    @contextlib.contextmanager
    def tx(self):
        with self.connect() as con:
            # In WAL mode SQLite answers "database is locked" at once, without the busy timeout, when this
            # connection's last read is older than another connection's write: start again, for up to 30 s.
            for attempt in range(300):
                try:
                    con.execute("BEGIN IMMEDIATE")
                    break
                except sqlite3.OperationalError as e:
                    if "locked" not in str(e) or attempt == 299:
                        raise
                    time.sleep(0.1)
            try:
                yield con
            except BaseException:
                con.execute("ROLLBACK")
                raise
            con.execute("COMMIT")

    def all(self, sql, args=()):
        with self.connect() as con:
            return [dict(r) for r in con.execute(sql, args)]

    def one(self, sql, args=()):
        with self.connect() as con:
            r = con.execute(sql, args).fetchone()
            return dict(r) if r else None


def migrations():
    files = importlib.resources.files("cutlery") / "migrations"
    found = sorted((f.name, f) for f in files.iterdir() if f.name.endswith(".sql"))
    return [(int(name.split("_", 1)[0]), f.read_text(encoding="utf-8")) for name, f in found]


def migrate(con: sqlite3.Connection) -> int:
    version = con.execute("PRAGMA user_version").fetchone()[0]
    for number, sql in migrations():
        if number <= version:
            continue
        con.executescript("BEGIN;\n" + sql + f"\nPRAGMA user_version = {number};\nCOMMIT;")
        version = number
    return version
