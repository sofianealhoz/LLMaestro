"""Cerebras, Groq and OpenRouter: same /chat/completions shape, one client."""

from __future__ import annotations

import json
import time

from ..errors import BadResponse, ProviderError
from ..messages import ToolCall, read_image
from ..transport import get, post_json
from .base import Completion, Provider, parse_json, raise_for_status, read_limits


class OpenAICompatible(Provider):
    def models(self, timeout: float = 10.0) -> list[str] | None:
        try:
            response = get(
                f"{self.spec.base_url.rstrip('/')}/models",
                timeout,
                self.name,
                {"authorization": f"Bearer {self.spec.api_key}"},
            )
            if not 200 <= response.status < 300:
                return None
            payload = response.json()
        except (ProviderError, ValueError):
            return None
        entries = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            return None
        return sorted(str(e["id"]) for e in entries if isinstance(e, dict) and e.get("id"))

    def complete(
        self, messages, *, max_tokens=512, temperature=0.2, timeout=30.0, tools=()
    ) -> Completion:
        payload = {
            "model": self.spec.model,
            "messages": [self._wire(message) for message in messages],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = [
                {"type": "function", "function": schema} for schema in tools
            ]
            payload["tool_choice"] = "auto"
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
            choice = body["choices"][0]
            answer = choice["message"]
        except (KeyError, IndexError, TypeError):
            raise BadResponse(self.name, "no choices[0].message in payload") from None

        calls = _tool_calls(answer.get("tool_calls"))
        text = answer.get("content")
        if text is None:
            # A turn that only asks for tools carries no content.
            text = ""
        if not isinstance(text, str):
            raise BadResponse(self.name, "message content is not a string")
        if not text and not calls:
            raise BadResponse(self.name, "empty answer with no tool call")

        usage = body.get("usage") or {}
        return Completion(
            text=text,
            provider=self.name,
            model=body.get("model") or self.spec.model,
            latency=elapsed,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            limits=read_limits(response.headers),
            tool_calls=calls,
            finish_reason=str(choice.get("finish_reason") or ""),
        )

    def _wire(self, message) -> dict:
        if message.role == "tool":
            return {
                "role": "tool",
                "tool_call_id": message.tool_call_id,
                "content": message.text,
            }
        if message.tool_calls:
            return {
                "role": message.role,
                "content": message.text or None,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments),
                        },
                    }
                    for call in message.tool_calls
                ],
            }
        if not message.images:
            return {"role": message.role, "content": message.text}
        blocks = [{"type": "text", "text": message.text}]
        for path in message.images:
            mime, data = read_image(path)
            blocks.append(
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}}
            )
        return {"role": message.role, "content": blocks}


def _tool_calls(raw) -> tuple:
    if not isinstance(raw, list):
        return ()
    calls = []
    for entry in raw:
        function = (entry or {}).get("function") or {}
        name = function.get("name")
        if not name:
            continue
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            # Models sometimes emit a broken fragment: an empty call beats a crash.
            try:
                arguments = json.loads(arguments or "{}")
            except ValueError:
                arguments = {}
        calls.append(
            ToolCall(
                id=str(entry.get("id") or f"call_{len(calls)}"),
                name=str(name),
                arguments=arguments if isinstance(arguments, dict) else {},
            )
        )
    return tuple(calls)
