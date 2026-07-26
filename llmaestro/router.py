"""Provider selection: where a task runs, and what happens when that fails.

Three decisions live here.

Eligibility: a provider is a candidate only if it has the capabilities the task
requires, a context window large enough, no active cooldown, and quota left in
the ledger. Providers ruled out are recorded with their reason, so a total
failure explains itself instead of just saying no.

Order: whichever rank the requested policy names, cost by default. The `reliable`
policy ignores the catalogue and prefers whatever has been failing the least.

Failure: a retryable error is retried on the same provider with exponential
backoff, honouring Retry-After when the wait is short. Anything else moves to
the next provider. A provider that authenticates badly or rate-limits is put on
cooldown so the next task does not walk into the same wall. Cooldown state is
shared by every worker thread, hence the lock.
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass

from .errors import AllProvidersFailed, ContextTooLarge, ProviderError, RateLimited
from .messages import Message, estimate_tokens, needs_vision, user

DEFAULT_COOLDOWN = 30.0
FAILURES_BEFORE_COOLDOWN = 2
FAILURE_COOLDOWN_STEP = 15.0
MAX_FAILURE_COOLDOWN = 300.0


@dataclass
class Attempt:
    provider: str
    error: str
    elapsed: float = 0.0
    tried: bool = True


@dataclass
class Task:
    messages: list[Message]
    policy: str = "cost"
    require: tuple[str, ...] = ()
    max_tokens: int = 512
    temperature: float = 0.2
    timeout: float = 30.0

    @classmethod
    def from_prompt(cls, prompt: str, images=(), **options) -> "Task":
        return cls(messages=[user(prompt, images)], **options)


@dataclass
class _Health:
    failures: int = 0
    cooling_until: float = 0.0
    reason: str = ""


class Router:
    def __init__(
        self,
        providers,
        ledger=None,
        retries: int = 1,
        backoff: float = 0.5,
        max_wait: float = 5.0,
        clock=time.monotonic,
        sleep=time.sleep,
    ):
        if not providers:
            raise ValueError("a router needs at least one provider")
        self.providers = list(providers)
        self.ledger = ledger
        self.retries = max(0, retries)
        self.backoff = backoff
        self.max_wait = max_wait
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._health = {provider.name: _Health() for provider in self.providers}

    def complete(self, task: Task):
        eligible, rejected = self._candidates(task)
        attempts = list(rejected)

        for provider in eligible:
            for attempt_number in range(self.retries + 1):
                started = self._clock()
                try:
                    completion = provider.complete(
                        task.messages,
                        max_tokens=task.max_tokens,
                        temperature=task.temperature,
                        timeout=task.timeout,
                    )
                except ProviderError as error:
                    attempts.append(
                        Attempt(provider.name, str(error), self._clock() - started)
                    )
                    # A refused call still spent the provider's budget, so it has
                    # to be counted before the refusal is turned into a limit.
                    if self.ledger is not None:
                        self.ledger.record(provider.spec, 0)
                    self._penalise(provider, error)
                    wait = self._wait_before_retry(error, attempt_number)
                    if wait is None:
                        break
                    self._sleep(wait)
                    continue

                self._clear(provider)
                if self.ledger is not None:
                    self.ledger.record(
                        provider.spec, completion.tokens or self._estimate(task)
                    )
                return completion

        raise AllProvidersFailed(attempts)

    def describe(self) -> list[dict]:
        """State of every provider, for the --check report."""
        now = self._clock()
        rows = []
        for provider in self.providers:
            with self._lock:
                health = self._health[provider.name]
                cooling = max(0.0, health.cooling_until - now)
                reason = health.reason
            row = {
                "name": provider.name,
                "model": provider.model,
                "kind": provider.spec.kind,
                "cooling_for": cooling,
                "reason": reason if cooling else "",
                "vision": provider.spec.vision,
                "context_window": provider.spec.context_window,
            }
            if self.ledger is not None:
                row["usage"] = self.ledger.snapshot(provider.spec)
            rows.append(row)
        return rows

    def _candidates(self, task: Task):
        required = set(task.require)
        if needs_vision(task.messages):
            required.add("vision")
        size = self._estimate(task)
        now = self._clock()

        eligible, rejected = [], []
        for provider in self.providers:
            spec = provider.spec
            missing = [c for c in sorted(required) if not spec.supports(c)]
            if missing:
                rejected.append(self._skip(provider, f"lacks {', '.join(missing)}"))
                continue
            if spec.context_window < size:
                rejected.append(
                    self._skip(provider, f"context window {spec.context_window} < {size}")
                )
                continue
            with self._lock:
                health = self._health[provider.name]
                remaining = health.cooling_until - now
                cooling_reason = health.reason
            if remaining > 0:
                rejected.append(
                    self._skip(provider, f"cooling down {remaining:.0f}s: {cooling_reason}")
                )
                continue
            if self.ledger is not None:
                allowed, why = self.ledger.allows(spec, size)
                if not allowed:
                    rejected.append(self._skip(provider, f"quota {why}"))
                    continue
            if provider.probe_before_use and not provider.available():
                rejected.append(self._skip(provider, "not listening"))
                continue
            eligible.append(provider)

        eligible.sort(key=lambda p: self._order(p, task.policy))
        return eligible, rejected

    def _order(self, provider, policy: str):
        spec = provider.spec
        if policy == "reliable":
            with self._lock:
                failures = self._health[provider.name].failures
            return (failures, spec.rank("quality"), spec.name)
        return (spec.rank(policy), spec.rank("latency"), spec.name)

    @staticmethod
    def _estimate(task: Task) -> int:
        return estimate_tokens(task.messages) + task.max_tokens

    @staticmethod
    def _skip(provider, reason: str) -> Attempt:
        return Attempt(provider.name, f"skipped: {reason}", tried=False)

    def _wait_before_retry(self, error: ProviderError, attempt_number: int) -> float | None:
        """How long to wait before trying the same provider again, or None to move on."""
        if not error.retryable or attempt_number >= self.retries:
            return None
        if isinstance(error, RateLimited) and error.retry_after is not None:
            # Waiting out a long cooldown is worse than asking someone else.
            return error.retry_after if error.retry_after <= self.max_wait else None
        base = self.backoff * (2**attempt_number)
        return base + random.random() * base * 0.1

    def _penalise(self, provider, error: ProviderError) -> None:
        if isinstance(error, ContextTooLarge):
            # The prompt is too big for this provider, but the provider is fine.
            return

        cooldown = float(getattr(error, "disable_for", 0.0) or 0.0)
        if isinstance(error, RateLimited):
            if self.ledger is not None:
                self.ledger.learn_from_refusal(provider.spec)
            cooldown = max(cooldown, error.retry_after or DEFAULT_COOLDOWN)

        with self._lock:
            health = self._health[provider.name]
            health.failures += 1
            if not cooldown and health.failures >= FAILURES_BEFORE_COOLDOWN:
                cooldown = min(
                    MAX_FAILURE_COOLDOWN, FAILURE_COOLDOWN_STEP * health.failures
                )
            if cooldown:
                health.cooling_until = max(health.cooling_until, self._clock() + cooldown)
                health.reason = str(error)

    def _clear(self, provider) -> None:
        with self._lock:
            self._health[provider.name] = _Health()
