"""Shared provider behaviour. raise_for_status is the only place an HTTP status becomes a routable error."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..errors import (
    AuthError,
    BadResponse,
    ContextTooLarge,
    ProviderError,
    ProviderUnavailable,
    RateLimited,
)
from ..transport import Response

CONTEXT_MARKERS = (
    "context length",
    "context_length",
    "context window",
    "maximum context",
    "too many tokens",
    "reduce the length",
    "prompt is too long",
)


# Providers that name the window in the header say exactly which limit applies.
# The bare OpenAI spelling (x-ratelimit-limit-requests) does not, so it is left
# alone rather than guessed at.
RATE_HEADERS = {
    "x-ratelimit-limit-requests-minute": "rpm",
    "x-ratelimit-limit-requests-day": "rpd",
    "x-ratelimit-limit-tokens-minute": "tpm",
    "x-ratelimit-limit-tokens-day": "tpd",
}


@dataclass
class Completion:
    text: str
    provider: str
    model: str
    latency: float
    prompt_tokens: int = 0
    completion_tokens: int = 0
    limits: dict = field(default_factory=dict)
    tool_calls: tuple = ()
    finish_reason: str = ""

    @property
    def tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


def read_limits(headers: dict) -> dict:
    """Quotas the provider states about itself, when it names the window."""
    found = {}
    for header, kind in RATE_HEADERS.items():
        raw = headers.get(header)
        if raw is None:
            continue
        try:
            value = int(float(raw))
        except (TypeError, ValueError):
            continue
        if value > 0:
            found[kind] = value
    return found


class Provider:
    # True when available() is cheap enough for the router to call on every task.
    probe_before_use = False

    def __init__(self, spec):
        self.spec = spec

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def model(self) -> str:
        return self.spec.model

    def available(self) -> bool:
        """Liveness probe. Cloud providers assume yes."""
        return True

    def models(self, timeout: float = 10.0) -> list[str] | None:
        """What the provider actually serves. None when it cannot be listed."""
        return None

    def complete(
        self, messages, *, max_tokens=512, temperature=0.2, timeout=30.0, tools=()
    ) -> Completion:
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name} {self.model}>"


def raise_for_status(provider: str, response: Response) -> None:
    if 200 <= response.status < 300:
        return
    detail = _detail(response.body) or f"HTTP {response.status}"
    status = response.status

    if status == 429:
        raise RateLimited(provider, detail, retry_after=_retry_after(response.headers))
    if status in (401, 403):
        raise AuthError(provider, detail, status)
    if status == 413 or (status == 400 and _mentions_context(detail)):
        raise ContextTooLarge(provider, detail, status)
    if status >= 500:
        raise ProviderUnavailable(provider, detail, status)
    raise ProviderError(provider, detail, status)


def parse_json(provider: str, response: Response) -> dict:
    try:
        payload = response.json()
    except ValueError:
        raise BadResponse(provider, "response is not JSON") from None
    if not isinstance(payload, dict):
        raise BadResponse(provider, f"expected an object, got {type(payload).__name__}")
    return payload


def _mentions_context(detail: str) -> bool:
    lowered = detail.lower()
    return any(marker in lowered for marker in CONTEXT_MARKERS)


def _detail(body: str) -> str:
    """Pull the human-readable message out of an error payload."""
    if not body:
        return ""
    try:
        payload = json.loads(body)
    except ValueError:
        return body.strip()[:200]
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or error).strip()[:200]
    if isinstance(error, str):
        return error.strip()[:200]
    return body.strip()[:200]


def _retry_after(headers: dict) -> float | None:
    raw = headers.get("retry-after")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None
