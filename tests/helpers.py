"""Shared fixtures: fake providers and a clock we control.

No test touches the network or reads a key, so the suite runs anywhere.
"""

from __future__ import annotations

import base64

from llmaestro.config import ProviderSpec
from llmaestro.providers.base import Completion, Provider

# Smallest valid PNG, for the multimodal tests.
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)


def spec(name: str, **overrides) -> ProviderSpec:
    fields = {
        "name": name,
        "kind": "echo",
        "base_url": "http://example.invalid",
        "model": f"{name}-model",
        "context_window": 8192,
        "cost": 5,
        "latency": 5,
        "quality": 5,
    }
    fields.update(overrides)
    return ProviderSpec(**fields)


class Scripted(Provider):
    """Answers according to a script: exceptions are raised, strings returned.

    The last entry repeats, so a one-item script describes a provider that
    always behaves the same way.
    """

    def __init__(self, name: str, script, **spec_overrides):
        super().__init__(spec(name, **spec_overrides))
        self.script = list(script)
        self.calls = 0
        self.seen = []
        self.tools_seen = []

    def complete(self, messages, *, max_tokens=512, temperature=0.2, timeout=30.0, tools=()):
        self.calls += 1
        self.seen.append(messages)
        self.tools_seen.append(tools)
        step = self.script[min(self.calls - 1, len(self.script) - 1)]
        if isinstance(step, BaseException):
            raise step
        if isinstance(step, Completion):
            return step
        return Completion(
            text=step,
            provider=self.name,
            model=self.spec.model,
            latency=0.0,
            prompt_tokens=4,
            completion_tokens=4,
        )


class FakeClock:
    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class Sleeps:
    """Stand-in for time.sleep that records instead of waiting."""

    def __init__(self):
        self.waits = []

    def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)

    @property
    def total(self) -> float:
        return sum(self.waits)
