"""Provider clients, one per wire protocol."""

from ..config import ProviderSpec
from .base import Completion, Provider
from .echo import Echo
from .ollama import Ollama
from .openai_compat import OpenAICompatible

CLIENTS = {"openai_compat": OpenAICompatible, "ollama": Ollama, "echo": Echo}

__all__ = [
    "Completion",
    "Provider",
    "OpenAICompatible",
    "Ollama",
    "Echo",
    "build",
    "build_all",
]


def build(spec: ProviderSpec) -> Provider:
    try:
        client = CLIENTS[spec.kind]
    except KeyError:
        raise ValueError(f"provider {spec.name}: no client for kind {spec.kind!r}") from None
    return client(spec)


def build_all(specs) -> list[Provider]:
    return [build(spec) for spec in specs]
