"""Cloud providers that expose the OpenAI chat completion shape.

Cerebras, Groq and OpenRouter differ only by base URL, model name and quota, so
they share one client. Everything provider-specific already lives in
providers.toml.
"""

from __future__ import annotations

import time

from ..errors import BadResponse
from ..messages import read_image
from ..transport import post_json
from .base import Completion, Provider, parse_json, raise_for_status


class OpenAICompatible(Provider):
    def complete(self, messages, *, max_tokens=512, temperature=0.2, timeout=30.0) -> Completion:
        payload = {
            "model": self.spec.model,
            "messages": [self._wire(message) for message in messages],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        headers = {"authorization": f"Bearer {self.spec.api_key}"}
        started = time.monotonic()
        response = post_json(
            f"{self.spec.base_url.rstrip('/')}/chat/completions",
            payload,
            headers,
            timeout,
            self.name,
        )
        raise_for_status(self.name, response)
        body = parse_json(self.name, response)
        elapsed = time.monotonic() - started

        try:
            text = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise BadResponse(self.name, "no choices[0].message.content in payload") from None
        if not isinstance(text, str):
            raise BadResponse(self.name, "message content is not a string")

        usage = body.get("usage") or {}
        return Completion(
            text=text,
            provider=self.name,
            model=body.get("model") or self.spec.model,
            latency=elapsed,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
        )

    def _wire(self, message) -> dict:
        if not message.images:
            return {"role": message.role, "content": message.text}
        blocks = [{"type": "text", "text": message.text}]
        for path in message.images:
            mime, data = read_image(path)
            blocks.append(
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}}
            )
        return {"role": message.role, "content": blocks}
