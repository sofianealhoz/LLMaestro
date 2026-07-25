"""Error taxonomy for provider calls.

The router only needs to answer two questions about a failure:
is it worth retrying on the *same* provider, and should the provider be
taken out of the rotation for a while. Every error below answers both.
"""


class LLMaestroError(Exception):
    """Base class for everything this package raises."""


class ProviderError(LLMaestroError):
    """A call to one provider failed.

    retryable      the same provider may succeed if we try again
    disable_for    seconds the provider should be skipped entirely (0 = keep it)
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
    """The prompt exceeds what this provider accepts.

    Retrying is pointless, but a provider with a bigger window may take it,
    so the router moves on instead of giving up.
    """


class AuthError(ProviderError):
    """Missing or rejected credentials. Nothing to retry until reconfigured."""

    disable_for = 3600.0


class BadResponse(ProviderError):
    """HTTP 200 but the payload is not what the API contract promises."""

    retryable = True


class AllProvidersFailed(LLMaestroError):
    """Every provider in the chain was exhausted.

    attempts is the ordered log of what was tried, for diagnostics.
    """

    def __init__(self, attempts):
        self.attempts = attempts
        summary = ", ".join(f"{a.provider}({a.error})" for a in attempts) or "no provider available"
        super().__init__(f"all providers failed: {summary}")
