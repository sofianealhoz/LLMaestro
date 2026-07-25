"""Local inference through Ollama: no key, no network, no quota.

The daemon is often simply not running, which is not a failure of the request
but of the provider, so `available()` lets the router and --check skip it
without burning an attempt.
"""

from __future__ import annotations

import time

from ..errors import BadResponse, ProviderError
from ..messages import read_image
from ..transport import get, post_json
from .base import Completion, Provider, parse_json, raise_for_status

PROBE_TIMEOUT = 1.5


class Ollama(Provider):
    def available(self) -> bool:
        try:
            response = get(f"{self.spec.base_url.rstrip('/')}/api/tags", PROBE_TIMEOUT, self.name)
        except ProviderError:
            return False
        return 200 <= response.status < 300

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
