"""Provider catalogue and credential resolution.

The catalogue lives in providers.toml so the fallback order, the models and the
known quotas can change without touching code. Keys live in the environment and
never in the repository. A provider whose key is missing is not an error: it is
skipped, and the reason is reported so `--check` can tell you what to fix.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CATALOGUE = "providers.toml"

REQUIRED_FIELDS = ("name", "kind", "base_url", "model")
KNOWN_KINDS = ("openai_compat", "ollama", "echo")
POLICIES = ("cost", "latency", "quality", "reliable")


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    kind: str
    base_url: str
    model: str
    env_key: str | None = None
    api_key: str | None = None
    context_window: int = 8192
    vision: bool = False
    tools: bool = False
    cost: int = 5
    latency: int = 5
    quality: int = 5
    rpm: int | None = None
    rpd: int | None = None
    tpm: int | None = None
    tpd: int | None = None
    enabled: bool = True

    def supports(self, capability: str) -> bool:
        return bool(getattr(self, capability, False))

    def rank(self, policy: str) -> int:
        """Position for a routing policy, 1 being best."""
        return int(getattr(self, policy, self.cost))


def load_env(path: str | os.PathLike = ".env", environ: dict | None = None) -> dict:
    """Read a .env file into the environment without overriding what is set.

    An exported variable always wins over the file, which is what you want when
    you run a one-off command with a different key.
    """
    environ = os.environ if environ is None else environ
    source = Path(path)
    if not source.is_file():
        return {}
    found = {}
    for raw in source.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key or not value:
            continue
        found[key] = value
        environ.setdefault(key, value)
    return found


def load_catalogue(
    path: str | os.PathLike = DEFAULT_CATALOGUE,
    environ: dict | None = None,
) -> tuple[list[ProviderSpec], list[tuple[str, str]]]:
    """Return the usable providers and the reasons the others were skipped."""
    environ = os.environ if environ is None else environ
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    entries = data.get("provider")
    if not entries:
        raise ValueError(f"{path} declares no [[provider]] entry")

    ready: list[ProviderSpec] = []
    skipped: list[tuple[str, str]] = []
    for entry in entries:
        spec = _spec(entry, environ)
        if not spec.enabled:
            skipped.append((spec.name, "disabled in providers.toml"))
        elif spec.env_key and not spec.api_key:
            skipped.append((spec.name, f"{spec.env_key} is not set"))
        else:
            ready.append(spec)
    return ready, skipped


def _spec(entry: dict, environ: dict) -> ProviderSpec:
    missing = [f for f in REQUIRED_FIELDS if not entry.get(f)]
    if missing:
        raise ValueError(f"provider entry {entry!r} lacks {', '.join(missing)}")
    if entry["kind"] not in KNOWN_KINDS:
        raise ValueError(f"provider {entry['name']}: unknown kind {entry['kind']!r}")

    fields = {k: v for k, v in entry.items() if k in ProviderSpec.__dataclass_fields__}
    env_key = fields.get("env_key")
    if env_key:
        fields["api_key"] = environ.get(env_key) or None
    if fields["kind"] == "ollama":
        fields["base_url"] = environ.get("OLLAMA_HOST") or fields["base_url"]
    return ProviderSpec(**fields)


def home() -> Path:
    """Directory holding the ledger. The only place outside out/ we write to."""
    override = os.environ.get("LLMAESTRO_HOME")
    return Path(override) if override else Path.home() / ".llmaestro"
