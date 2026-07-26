"""Error taxonomy. Each one answers: retry the same provider, and skip it for how long."""


class LLMaestroError(Exception):
    """Base class for everything this package raises."""


class ProviderError(LLMaestroError):
    """A call to one provider failed.

    retryable: the same provider may succeed on a second try.
    disable_for: seconds to skip it entirely, 0 to keep it in rotation.
    """

    retryable = False
    disable_for = 0.0

    def __init__(self, provider, message, status=None):
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.status = status


class ProviderTimeout(ProviderError):
    retryable = True


class ProviderUnavailable(ProviderError):
    """Connection refused, DNS failure, 5xx."""

    retryable = True


class RateLimited(ProviderError):
    """429. Carries the server's Retry-After when it sent one."""

    retryable = True

    def __init__(self, provider, message, status=429, retry_after=None):
        super().__init__(provider, message, status)
        self.retry_after = retry_after


class ContextTooLarge(ProviderError):
    """Prompt too big for this provider. No retry, but a bigger window may take it."""


class AuthError(ProviderError):
    """Missing or rejected credentials. Nothing to retry until reconfigured."""

    disable_for = 3600.0


class BadResponse(ProviderError):
    """HTTP 200 but the payload is not what the API contract promises."""

    retryable = True


class AllProvidersFailed(LLMaestroError):
    """Chain exhausted. attempts is the ordered log of what was tried."""

    def __init__(self, attempts):
        self.attempts = attempts
        summary = ", ".join(f"{a.provider}({a.error})" for a in attempts) or "no provider available"
        super().__init__(f"all providers failed: {summary}")
