"""Agents confinés: un run travaille dans un worktree jetable, rien ne remonte sans ordre."""

from __future__ import annotations

import json
import tomllib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..config import home
from .journal import Journal
from .loop import Budget, Outcome, drive
from .sandbox import Refused, Workspace
from .tools import SCHEMAS, Toolbox

DEFAULT_CONFIG = "agent.toml"
FALLBACK_CONFIG = "agent.example.toml"

__all__ = [
    "Budget",
    "Journal",
    "Outcome",
    "Refused",
    "Run",
    "Toolbox",
    "Workspace",
    "SCHEMAS",
    "drive",
    "load_config",
    "load_run",
    "promote",
    "runs",
    "start",
]


@dataclass
class Run:
    id: str
    repo: str
    branch: str
    instruction: str
    started: str
    stopped: str = ""
    summary: str = ""
    steps: int = 0
    tokens: int = 0
    providers: tuple = ()
    promoted: bool = False

    @property
    def directory(self) -> Path:
        return home() / "runs" / self.id

    @property
    def card(self) -> Path:
        return home() / "runs" / f"{self.id}.json"

    @property
    def journal(self) -> Path:
        return home() / "runs" / f"{self.id}.jsonl"

    def save(self) -> None:
        self.card.parent.mkdir(parents=True, exist_ok=True)
        self.card.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")


def load_config(path: str | None = None) -> dict:
    for candidate in ([path] if path else [DEFAULT_CONFIG, FALLBACK_CONFIG]):
        if candidate and Path(candidate).is_file():
            return tomllib.loads(Path(candidate).read_text(encoding="utf-8"))
    return {}


def start(router, repo, instruction: str, config: dict | None = None, run_id: str | None = None) -> Run:
    config = config or load_config()
    settings = config.get("agent", {})
    allowed = [tuple(entry) for entry in settings.get("commands", [])]
    budget = Budget(**{k: v for k, v in (settings.get("budget") or {}).items()})

    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    workspace = Workspace.create(
        repo, run_id, allowed=allowed, timeout=float(settings.get("timeout", 120.0))
    )
    run = Run(
        id=run_id,
        repo=str(workspace.repo),
        branch=workspace.branch,
        instruction=instruction,
        started=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    run.save()

    journal = Journal(run.journal, run_id)
    journal.write("run_started", repo=run.repo, branch=run.branch, instruction=instruction)
    outcome = drive(router, workspace, instruction, journal, budget)

    run.stopped = outcome.stopped
    run.summary = outcome.summary
    run.steps = outcome.steps
    run.tokens = outcome.tokens
    run.providers = tuple(outcome.providers)
    run.save()
    workspace.discard()
    return run


def load_run(run_id: str) -> Run:
    card = home() / "runs" / f"{run_id}.json"
    if not card.is_file():
        raise Refused(f"run inconnu: {run_id}")
    return Run(**json.loads(card.read_text(encoding="utf-8")))


def runs() -> list:
    directory = home() / "runs"
    if not directory.is_dir():
        return []
    found = []
    for card in sorted(directory.glob("*.json"), reverse=True):
        try:
            found.append(Run(**json.loads(card.read_text(encoding="utf-8"))))
        except (ValueError, TypeError):
            continue
    return found


def diff(run: Run) -> str:
    from .sandbox import _git

    return _git(run.repo, "diff", f"...{run.branch}")


def promote(run: Run) -> str:
    """Applique la branche du run sur le dépôt réel. Appelé seulement sur ordre explicite."""
    from .sandbox import _git, _require_clean_repo

    repo = Path(run.repo)
    _require_clean_repo(repo)
    _git(repo, "merge", "--no-ff", run.branch, "-m", f"llmaestro run {run.id}")
    run.promoted = True
    run.save()
    return _git(repo, "log", "--oneline", "-1").strip()
