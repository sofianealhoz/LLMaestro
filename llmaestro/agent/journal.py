"""Journal d'audit en JSONL. Noms d'événements figés: la vue pixel les lira tels quels."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

EVENTS = (
    "run_started",
    "step_started",
    "provider_selected",
    "provider_failed",
    "tool_called",
    "tool_result",
    "budget_exceeded",
    "run_finished",
)


class Journal:
    def __init__(self, path, run_id: str, agent: str = "forge", clock=time.time):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.agent = agent
        self._clock = clock
        self._lock = threading.Lock()

    def write(self, event: str, **data) -> dict:
        record = {
            "event": event,
            "run_id": self.run_id,
            "agent_id": self.agent,
            "ts": round(self._clock(), 3),
            "data": data,
        }
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        return record

    def read(self) -> list:
        if not self.path.is_file():
            return []
        records = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
        return records
