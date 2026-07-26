"""Tech watch: collect, dedup, score through the pool, write a digest."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .digest import render
from .scoring import DEFAULT_AXES, Scored, score
from .sources import Item, collect
from .store import Seen

DEFAULT_CONFIG = "watch.toml"
FALLBACK_CONFIG = "watch.example.toml"
OUT_DIR = "out"

__all__ = ["Item", "Scored", "Seen", "Report", "run", "load_config", "collect", "score", "render"]


@dataclass
class Report:
    path: str | None
    markdown: str
    collected: int
    fresh: int
    scored: int
    problems: list


def load_config(path: str | None = None) -> dict:
    """watch.toml when present, the versioned example otherwise."""
    candidates = [path] if path else [DEFAULT_CONFIG, FALLBACK_CONFIG]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return tomllib.loads(Path(candidate).read_text(encoding="utf-8"))
    raise FileNotFoundError(f"no watch config found (looked for {', '.join(map(str, candidates))})")


def run(
    router=None,
    config: dict | None = None,
    workers: int = 4,
    seen: Seen | None = None,
    only=None,
    limit: int | None = None,
    write: bool = True,
    when: str | None = None,
) -> Report:
    config = config or load_config()
    sources = config.get("sources", {})
    axes = config.get("axes") or DEFAULT_AXES
    floor = int(config.get("floor", 0))

    items, problems = collect(sources, only)
    collected = len(items)

    store = seen if seen is not None else Seen()
    try:
        fresh = store.unseen(items)
        if limit:
            fresh = fresh[:limit]

        scored = score(router, fresh, workers, axes) if (router and fresh) else [
            Scored(item) for item in fresh
        ]
        markdown = render(scored, problems, when=when, floor=floor)

        path = _write(markdown, when) if write else None
        if write:
            store.remember(fresh)
    finally:
        if seen is None:
            store.close()

    return Report(path, markdown, collected, len(fresh), len(scored), problems)


def _write(markdown: str, when: str | None) -> str:
    out = Path(OUT_DIR)
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"watch-{when or date.today().isoformat()}.md"
    target.write_text(markdown, encoding="utf-8")
    return str(target)
