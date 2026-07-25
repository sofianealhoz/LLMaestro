"""The unit of work: a conversation, which may carry images.

Providers disagree on the wire format for multimodal input (OpenAI uses content
blocks with data URIs, Ollama a plain base64 array), so messages stay neutral
here and each client serialises them. Anything that has to travel to a model
goes through this module.
"""

from __future__ import annotations

import base64
import mimetypes
from dataclasses import dataclass
from pathlib import Path

CHARS_PER_TOKEN = 4
TOKENS_PER_IMAGE = 800


@dataclass(frozen=True)
class Message:
    role: str
    text: str
    images: tuple[str, ...] = ()


def system(text: str) -> Message:
    return Message("system", text)


def user(text: str, images: tuple[str, ...] | list[str] = ()) -> Message:
    return Message("user", text, tuple(images))


def assistant(text: str) -> Message:
    return Message("assistant", text)


def needs_vision(messages: list[Message]) -> bool:
    return any(m.images for m in messages)


def estimate_tokens(messages: list[Message]) -> int:
    """Rough size of a conversation, used to rule out providers too small for it.

    Deliberately approximate: it only has to be good enough to avoid sending a
    prompt that a provider will refuse, and every provider is generous about the
    margin.
    """
    characters = sum(len(m.text) for m in messages)
    images = sum(len(m.images) for m in messages)
    return characters // CHARS_PER_TOKEN + images * TOKENS_PER_IMAGE


def read_image(path: str) -> tuple[str, str]:
    """Return the mime type and base64 payload of an image on disk."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"image not found: {path}")
    mime = mimetypes.guess_type(source.name)[0] or "image/png"
    return mime, base64.b64encode(source.read_bytes()).decode("ascii")
