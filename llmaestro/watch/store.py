"""Dedup store: an item already digested never comes back."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from ..config import home

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    id         TEXT PRIMARY KEY,
    source     TEXT NOT NULL,
    first_seen REAL NOT NULL
);
"""


class Seen:
    def __init__(self, path: str | Path | None = None, clock=time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self._resolve(path), check_same_thread=False)
        with self._db:
            self._db.executescript(SCHEMA)

    @staticmethod
    def _resolve(path) -> str:
        if path == ":memory:":
            return ":memory:"
        target = Path(path) if path else home() / "state.db"
        target.parent.mkdir(parents=True, exist_ok=True)
        return str(target)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def unseen(self, items):
        with self._lock:
            known = {
                row[0]
                for row in self._db.execute("SELECT id FROM seen").fetchall()
            }
        return [item for item in items if item.id not in known]

    def remember(self, items) -> None:
        now = self._clock()
        with self._lock:
            with self._db:
                self._db.executemany(
                    "INSERT OR IGNORE INTO seen (id, source, first_seen) VALUES (?, ?, ?)",
                    [(item.id, item.source, now) for item in items],
                )

    def count(self) -> int:
        with self._lock:
            return int(self._db.execute("SELECT COUNT(*) FROM seen").fetchone()[0])
