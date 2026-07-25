"""A provider that answers without a network, for --dry-run and for tests.

It exists so the whole path (router, ledger filtering, worker pool, CLI output)
can be exercised on a machine with no keys and no connectivity.
"""

from __future__ import annotations

import time

from ..config import ProviderSpec
from .base import Completion, Provider

PREVIEW = 160


class Echo(Provider):
    def __init__(self, spec: ProviderSpec | None = None, latency: float = 0.0):
        super().__init__(spec or default_spec())
        self.latency = latency

    def complete(self, messages, *, max_tokens=512, temperature=0.2, timeout=30.0) -> Completion:
        if self.latency:
            time.sleep(self.latency)
        last = messages[-1] if messages else None
        prompt = last.text if last else ""
        images = len(last.images) if last else 0
        note = f" plus {images} image(s)" if images else ""
        return Completion(
            text=f"[dry-run via {self.name}]{note} {prompt[:PREVIEW]}",
            provider=self.name,
            model=self.spec.model,
            latency=self.latency,
            prompt_tokens=len(prompt) // 4,
            completion_tokens=8,
        )


def default_spec() -> ProviderSpec:
    return ProviderSpec(
        name="echo",
        kind="echo",
        base_url="",
        model="dry-run",
        context_window=1_000_000,
        vision=True,
        tools=True,
        cost=1,
        latency=1,
        quality=5,
    )
