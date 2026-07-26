"""Local inference through Ollama. No key, no quota."""

from __future__ import annotations

import time

from ..errors import BadResponse, ProviderError
from ..messages import read_image
from ..transport import get, post_json
from .base import Completion, Provider, parse_json, raise_for_status

PROBE_TIMEOUT = 1.5
PROBE_TTL = 10.0


class Ollama(Provider):
    # Daemon down is the normal case, and a refused localhost connection is
    # instant: cheaper to probe than to spend an attempt on it.
    probe_before_use = True

    def __init__(self, spec, clock=time.monotonic):
        super().__init__(spec)
        self._clock = clock
        self._probed_at = None
        self._probe = False

    def available(self) -> bool:
        now = self._clock()
        if self._probed_at is not None and now - self._probed_at < PROBE_TTL:
            return self._probe
        try:
            response = get(f"{self.spec.base_url.rstrip('/')}/api/tags", PROBE_TIMEOUT, self.name)
            self._probe = 200 <= response.status < 300
        except ProviderError:
            self._probe = False
        self._probed_at = now
        return self._probe

    def models(self, timeout: float = PROBE_TIMEOUT) -> list[str] | None:
        try:
            response = get(f"{self.spec.base_url.rstrip('/')}/api/tags", timeout, self.name)
            payload = response.json()
        except (ProviderError, ValueError):
            return None
        entries = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            return None
        return sorted(str(e["name"]) for e in entries if isinstance(e, dict) and e.get("name"))

    def complete(self, messages, *, max_tokens=512, temperature=0.2, timeout=120.0) -> Completion:
        payload = {
            "model": self.spec.model,
            "messages": [self._wire(message) for message in messages],
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        started = time.monotonic()
        response = post_json(
            f"{self.spec.base_url.rstrip('/')}/api/chat", payload, None, timeout, self.name
        )
        raise_for_status(self.name, response)
        body = parse_json(self.name, response)
        elapsed = time.monotonic() - started

        text = (body.get("message") or {}).get("content")
        if not isinstance(text, str):
            raise BadResponse(self.name, "no message.content in payload")

        return Completion(
            text=text,
            provider=self.name,
            model=body.get("model") or self.spec.model,
            latency=elapsed,
            prompt_tokens=int(body.get("prompt_eval_count") or 0),
            completion_tokens=int(body.get("eval_count") or 0),
        )

    def _wire(self, message) -> dict:
        wire = {"role": message.role, "content": message.text}
        if message.images:
            wire["images"] = [read_image(path)[1] for path in message.images]
        return wire
