"""A conversation, possibly with images. Kept neutral: each client serialises its own wire format."""

from __future__ import annotations

import base64
import json
import mimetypes
from dataclasses import dataclass, field
from pathlib import Path

CHARS_PER_TOKEN = 4
TOKENS_PER_IMAGE = 800


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict = field(default_factory=dict)
    # Charge opaque du fournisseur, a renvoyer telle quelle au tour suivant.
    # Gemini 3 y met une thought_signature et refuse la suite sans elle.
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Message:
    role: str
    text: str = ""
    images: tuple[str, ...] = ()
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    name: str | None = None


def system(text: str) -> Message:
    return Message("system", text)


def user(text: str, images: tuple[str, ...] | list[str] = ()) -> Message:
    return Message("user", text, tuple(images))


def assistant(text: str = "", tool_calls: tuple[ToolCall, ...] | list[ToolCall] = ()) -> Message:
    return Message("assistant", text, tool_calls=tuple(tool_calls))


def tool_result(call: ToolCall | str, content: str, name: str | None = None) -> Message:
    """What a tool answered, tied to the call that asked for it."""
    call_id = call.id if isinstance(call, ToolCall) else call
    tool_name = name or (call.name if isinstance(call, ToolCall) else None)
    return Message("tool", content, tool_call_id=call_id, name=tool_name)


def needs_vision(messages: list[Message]) -> bool:
    return any(m.images for m in messages)


def estimate_tokens(messages: list[Message]) -> int:
    """Rough size, only accurate enough to rule out providers whose window is too small."""
    characters = sum(len(m.text) for m in messages)
    characters += sum(
        len(call.name) + len(json.dumps(call.arguments))
        for m in messages
        for call in m.tool_calls
    )
    images = sum(len(m.images) for m in messages)
    return characters // CHARS_PER_TOKEN + images * TOKENS_PER_IMAGE


def read_image(path: str) -> tuple[str, str]:
    """Return the mime type and base64 payload of an image on disk."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"image not found: {path}")
    mime = mimetypes.guess_type(source.name)[0] or "image/png"
    return mime, base64.b64encode(source.read_bytes()).decode("ascii")
