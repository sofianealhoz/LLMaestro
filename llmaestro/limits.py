"""Quota ledger: what each provider has spent, and what it is still allowed.

Free tiers are capped per minute and per day, on requests and on tokens. The
router asks the ledger *before* dispatching instead of discovering the ceiling
through a refusal, which is the difference between a smooth fallback and a
burst of 429s.

Declared limits come from providers.toml and are only a starting point: when a
provider refuses anyway, the observed usage at that moment becomes the learned
limit, so a wrong value in the catalogue self-corrects after one refusal.

State lives in sqlite under ~/.llmaestro so the ledger survives restarts, and
every access is serialised: a single ledger is shared by all worker threads.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from .config import ProviderSpec, home

WINDOWS = {"rpm": 60.0, "tpm": 60.0, "rpd": 86400.0, "tpd": 86400.0}
TOKEN_KINDS = ("tpm", "tpd")
RETENTION = 86400.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
    provider TEXT NOT NULL,
    model    TEXT NOT NULL,
    ts       REAL NOT NULL,
    tokens   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS usage_lookup ON usage (provider, model, ts);
CREATE TABLE IF NOT EXISTS learned (
    provider TEXT NOT NULL,
    model    TEXT NOT NULL,
    kind     TEXT NOT NULL,
    value    INTEGER NOT NULL,
    PRIMARY KEY (provider, model, kind)
);
"""


class Ledger:
    def __init__(self, path: str | Path | None = None, clock=time.time):
        self._clock = clock
        self._lock = threading.Lock()
        target = self._resolve(path)
        self._db = sqlite3.connect(target, check_same_thread=False)
        with self._db:
            self._db.executescript(SCHEMA)
        self._prune()

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

    def allows(self, spec: ProviderSpec, tokens: int = 0) -> tuple[bool, str]:
        """Whether one more call of this size fits in every declared window."""
        with self._lock:
            for kind, window in WINDOWS.items():
                limit = self._effective(spec, kind)
                if limit is None:
                    continue
                used = self._used(spec, window, counting_tokens=kind in TOKEN_KINDS)
                asking = tokens if kind in TOKEN_KINDS else 1
                if used + asking > limit:
                    return False, f"{kind} reached ({used}/{limit})"
        return True, ""

    def record(self, spec: ProviderSpec, tokens: int = 0) -> None:
        with self._lock:
            with self._db:
                self._db.execute(
                    "INSERT INTO usage (provider, model, ts, tokens) VALUES (?, ?, ?, ?)",
                    (spec.name, spec.model, self._clock(), max(0, int(tokens))),
                )

    def learn_from_refusal(self, spec: ProviderSpec) -> dict:
        """Turn a 429 into knowledge: what we just spent is the real ceiling."""
        learned = {}
        with self._lock:
            for kind, window in WINDOWS.items():
                # Only refine a limit the catalogue actually declares. Without a
                # declared value there is nothing tying the refusal to this
                # window, and guessing one taught a daily cap of 1.
                if getattr(spec, kind, None) is None:
                    continue
                observed = self._used(spec, window, counting_tokens=kind in TOKEN_KINDS)
                if observed <= 0:
                    continue
                current = self._effective(spec, kind)
                if current is not None and current <= observed:
                    continue
                self._remember(spec, kind, observed)
                learned[kind] = observed
        return learned

    def forget_learned(self, spec: ProviderSpec | None = None) -> int:
        """Drop learned limits. Needed when a bad run taught nonsense."""
        with self._lock:
            with self._db:
                if spec is None:
                    cursor = self._db.execute("DELETE FROM learned")
                else:
                    cursor = self._db.execute(
                        "DELETE FROM learned WHERE provider = ? AND model = ?",
                        (spec.name, spec.model),
                    )
            return cursor.rowcount

    def snapshot(self, spec: ProviderSpec) -> dict:
        """Usage and effective limits, for the --check report."""
        report = {}
        with self._lock:
            for kind, window in WINDOWS.items():
                report[kind] = {
                    "used": self._used(spec, window, counting_tokens=kind in TOKEN_KINDS),
                    "limit": self._effective(spec, kind),
                }
        return report

    # Everything below assumes the lock is already held.

    def _used(self, spec: ProviderSpec, window: float, counting_tokens: bool) -> int:
        since = self._clock() - window
        column = "COALESCE(SUM(tokens), 0)" if counting_tokens else "COUNT(*)"
        row = self._db.execute(
            f"SELECT {column} FROM usage WHERE provider = ? AND model = ? AND ts >= ?",
            (spec.name, spec.model, since),
        ).fetchone()
        return int(row[0] or 0)

    def _effective(self, spec: ProviderSpec, kind: str) -> int | None:
        declared = getattr(spec, kind, None)
        row = self._db.execute(
            "SELECT value FROM learned WHERE provider = ? AND model = ? AND kind = ?",
            (spec.name, spec.model, kind),
        ).fetchone()
        learned = int(row[0]) if row else None
        candidates = [v for v in (declared, learned) if v is not None]
        return min(candidates) if candidates else None

    def _remember(self, spec: ProviderSpec, kind: str, value: int) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO learned (provider, model, kind, value) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (provider, model, kind) DO UPDATE SET value = excluded.value",
                (spec.name, spec.model, kind, max(1, int(value))),
            )

    def _prune(self) -> None:
        with self._lock:
            with self._db:
                self._db.execute("DELETE FROM usage WHERE ts < ?", (self._clock() - RETENTION,))
