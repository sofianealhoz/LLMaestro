"""LLMaestro: route each task to the provider that can do it cheapest.

Typical use:

    from llmaestro import Router, Task, build_all, load_catalogue, load_env

    load_env()
    specs, _ = load_catalogue()
    router = Router(build_all(specs))
    print(router.complete(Task.from_prompt("translate to French: hello")).text)
"""

from .config import ProviderSpec, load_catalogue, load_env
from .errors import (
    AllProvidersFailed,
    AuthError,
    BadResponse,
    ContextTooLarge,
    LLMaestroError,
    ProviderError,
    ProviderTimeout,
    ProviderUnavailable,
    RateLimited,
)
from .limits import Ledger
from .messages import Message, assistant, estimate_tokens, system, user
from .pool import Result, WorkerPool, run_prompts
from .providers import Completion, Provider, build, build_all
from .router import Attempt, Router, Task

__version__ = "0.1.0"

__all__ = [
    "AllProvidersFailed",
    "Attempt",
    "AuthError",
    "BadResponse",
    "Completion",
    "ContextTooLarge",
    "LLMaestroError",
    "Ledger",
    "Message",
    "Provider",
    "ProviderError",
    "ProviderSpec",
    "ProviderTimeout",
    "ProviderUnavailable",
    "RateLimited",
    "Result",
    "Router",
    "Task",
    "WorkerPool",
    "assistant",
    "build",
    "build_all",
    "estimate_tokens",
    "load_catalogue",
    "load_env",
    "run_prompts",
    "system",
    "user",
    "__version__",
]
