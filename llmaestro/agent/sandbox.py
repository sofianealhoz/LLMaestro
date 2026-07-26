"""Worktree jetable, prison de chemins, commandes en liste blanche.

Tout ce qu'un agent peut toucher passe par ici. Le dépôt réel n'est jamais ouvert en écriture.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..config import home
from ..errors import LLMaestroError

BRANCH_PREFIX = "llmaestro"
SECRET_MARKERS = ("API_KEY", "TOKEN", "SECRET", "PASSWORD")
COMMAND_TIMEOUT = 120.0
OUTPUT_LIMIT = 4000


class Refused(LLMaestroError):
    """Une opération sortait du bac à sable. Jamais fatale: l'agent reçoit le refus."""


@dataclass
class Ran:
    argv: list
    code: int
    output: str

    @property
    def ok(self) -> bool:
        return self.code == 0


@dataclass
class Workspace:
    repo: Path
    root: Path
    branch: str
    base: str
    allowed: tuple = ()
    timeout: float = COMMAND_TIMEOUT
    files_written: set = field(default_factory=set)
    bytes_written: int = 0

    @classmethod
    def create(cls, repo, run_id: str, allowed=(), timeout: float = COMMAND_TIMEOUT):
        repo = Path(repo).expanduser().resolve()
        _require_clean_repo(repo)
        base = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
        branch = f"{BRANCH_PREFIX}/{run_id}"
        root = home() / "runs" / run_id
        root.parent.mkdir(parents=True, exist_ok=True)
        _git(repo, "worktree", "add", "-b", branch, str(root), "HEAD")
        return cls(repo, root.resolve(), branch, base, tuple(allowed), timeout)

    # Chemins

    def resolve(self, path: str) -> Path:
        """Refuse tout ce qui sort de la racine, y compris via .. ou un lien symbolique."""
        candidate = (self.root / str(path)).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise Refused(f"chemin hors du bac à sable: {path}")
        if ".git" in candidate.relative_to(self.root).parts:
            raise Refused("le dossier .git est interdit")
        return candidate

    def relative(self, path: Path) -> str:
        return str(path.relative_to(self.root))

    # Lecture

    def list_files(self, path: str = ".", limit: int = 200) -> list:
        target = self.resolve(path)
        if not target.is_dir():
            raise Refused(f"pas un dossier: {path}")
        found = []
        for entry in sorted(target.rglob("*")):
            if ".git" in entry.relative_to(self.root).parts or entry.is_dir():
                continue
            found.append(self.relative(entry))
            if len(found) >= limit:
                break
        return found

    def read(self, path: str, start: int = 1, count: int = 200) -> str:
        target = self.resolve(path)
        if not target.is_file():
            raise Refused(f"fichier introuvable: {path}")
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(1, int(start))
        chunk = lines[start - 1 : start - 1 + max(1, int(count))]
        return "\n".join(f"{start + n}\t{line}" for n, line in enumerate(chunk))

    def search(self, pattern: str, path: str = ".", limit: int = 60) -> list:
        target = self.resolve(path)
        hits = []
        for entry in sorted(target.rglob("*")):
            if entry.is_dir() or ".git" in entry.relative_to(self.root).parts:
                continue
            try:
                content = entry.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for number, line in enumerate(content.splitlines(), 1):
                if pattern in line:
                    hits.append(f"{self.relative(entry)}:{number}: {line.strip()[:160]}")
                    if len(hits) >= limit:
                        return hits
        return hits

    # Écriture

    def write(self, path: str, content: str) -> str:
        target = self.resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = str(content)
        target.write_text(payload, encoding="utf-8")
        self.files_written.add(self.relative(target))
        self.bytes_written += len(payload.encode("utf-8"))
        return self.relative(target)

    def replace(self, path: str, old: str, new: str) -> str:
        target = self.resolve(path)
        if not target.is_file():
            raise Refused(f"fichier introuvable: {path}")
        content = target.read_text(encoding="utf-8")
        occurrences = content.count(old)
        if occurrences == 0:
            raise Refused("motif absent du fichier")
        if occurrences > 1:
            raise Refused(f"motif présent {occurrences} fois, il doit être unique")
        return self.write(path, content.replace(old, new, 1))

    # Exécution

    def run(self, argv) -> Ran:
        argv = [str(a) for a in (argv or [])]
        if not argv:
            raise Refused("commande vide")
        if not self._allowed(argv):
            raise Refused(f"commande hors liste blanche: {' '.join(argv[:3])}")
        done = subprocess.run(
            argv,
            cwd=self.root,
            env=_clean_env(),
            capture_output=True,
            text=True,
            timeout=self.timeout,
        )
        output = (done.stdout + done.stderr).strip()
        return Ran(argv, done.returncode, output[:OUTPUT_LIMIT])

    def _allowed(self, argv) -> bool:
        return any(
            len(argv) >= len(prefix) and argv[: len(prefix)] == list(prefix)
            for prefix in self.allowed
        )

    # Git

    def snapshot(self, message: str) -> str | None:
        """Commit tout ce qui traîne. Rend chaque étape annulable."""
        if not _git(self.root, "status", "--porcelain").strip():
            return None
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-m", message, "--no-verify")
        return _git(self.root, "rev-parse", "HEAD").strip()

    def diff(self) -> str:
        return _git(self.root, "diff", f"{self.base}...{self.branch}")

    def commits(self) -> list:
        log = _git(self.root, "log", "--oneline", f"{self.base}..{self.branch}")
        return [line for line in log.splitlines() if line.strip()]

    def discard(self) -> None:
        """Retire le worktree. La branche reste, donc rien n'est perdu."""
        _git(self.repo, "worktree", "remove", "--force", str(self.root))

    def forget(self) -> None:
        """Retire aussi la branche du run. Réservé à un run explicitement abandonné."""
        self.discard()
        _git(self.repo, "branch", "-D", self.branch)


def existing(run_id: str) -> Path:
    return home() / "runs" / run_id


def _require_clean_repo(repo: Path) -> None:
    if not (repo / ".git").exists():
        raise Refused(f"pas un dépôt git: {repo}")
    head = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"], cwd=str(repo), capture_output=True, text=True
    )
    if head.returncode != 0:
        raise Refused(f"dépôt sans commit: {repo}. Fais un premier commit d'abord.")
    if _git(repo, "status", "--porcelain").strip():
        raise Refused(f"dépôt non propre: {repo}. Commite ou remise tes changements d'abord.")


def _git(cwd, *args) -> str:
    done = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=60
    )
    if done.returncode != 0:
        raise Refused(f"git {' '.join(args[:2])}: {done.stderr.strip()[:200]}")
    return done.stdout


def _clean_env() -> dict:
    return {
        key: value
        for key, value in os.environ.items()
        if not any(marker in key.upper() for marker in SECRET_MARKERS)
    }
