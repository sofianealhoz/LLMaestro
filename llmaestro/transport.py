"""HTTP on urllib. Network failures raise, HTTP answers come back as they are: 4xx and 5xx mean different things per provider."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .errors import ProviderTimeout, ProviderUnavailable

USER_AGENT = "llmaestro/0.1"


@dataclass
class Response:
    status: int
    body: str
    headers: dict = field(default_factory=dict)

    def json(self) -> dict:
        return json.loads(self.body)


def post_json(
    url: str,
    payload: dict,
    headers: dict | None = None,
    timeout: float = 30.0,
    provider: str = "?",
) -> Response:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST"
    )
    request.add_header("content-type", "application/json")
    request.add_header("user-agent", USER_AGENT)
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    return _send(request, timeout, provider)


def get(
    url: str,
    timeout: float = 10.0,
    provider: str = "?",
    headers: dict | None = None,
) -> Response:
    request = urllib.request.Request(url, method="GET")
    request.add_header("user-agent", USER_AGENT)
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    return _send(request, timeout, provider)


def _send(request: urllib.request.Request, timeout: float, provider: str) -> Response:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return Response(
                response.status,
                response.read().decode("utf-8", "replace"),
                _headers(response.headers),
            )
    except urllib.error.HTTPError as error:
        # A refusal is still an answer: the provider layer maps the status.
        body = error.read().decode("utf-8", "replace") if error.fp else ""
        return Response(error.code, body, _headers(error.headers))
    except TimeoutError:
        raise ProviderTimeout(provider, f"no response within {timeout:g}s") from None
    except urllib.error.URLError as error:
        if isinstance(error.reason, TimeoutError):
            raise ProviderTimeout(provider, f"no response within {timeout:g}s") from None
        raise ProviderUnavailable(provider, str(error.reason)) from None
    except OSError as error:
        raise ProviderUnavailable(provider, str(error)) from None


def _headers(raw) -> dict:
    if raw is None:
        return {}
    return {key.lower(): value for key, value in raw.items()}
