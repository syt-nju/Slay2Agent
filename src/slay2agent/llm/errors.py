"""LLM error taxonomy + classifier.

Three-level hierarchy is enough for retry decisions. Unknown exceptions are
classified as Fatal on purpose (see docs/llm-adapter.md §7.2): real transient
conditions surface as ``APITimeoutError`` / ``APIConnectionError`` / 5xx, which
are covered explicitly; the fallback branch should not silently hide bugs.
"""

from __future__ import annotations

from typing import Any

import openai


class LLMError(Exception):
    """Base class for all adapter-layer errors."""


class FatalError(LLMError):
    """Error that should not be retried (4xx except 429, schema, auth)."""


class TransientError(LLMError):
    """Error that may succeed on retry (5xx, timeout, connection)."""


class RateLimitError(TransientError):
    """HTTP 429 / provider rate limit. Honours ``retry_after`` if present."""

    def __init__(self, message: str = "", *, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


def _extract_retry_after(exc: openai.APIStatusError) -> float | None:
    """Best-effort pull ``Retry-After`` (seconds) from SDK response object."""
    response: Any = getattr(exc, "response", None)
    headers: Any = getattr(response, "headers", None) if response is not None else None
    if headers is None:
        return None
    raw = headers.get("retry-after") if hasattr(headers, "get") else None
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def classify(exc: BaseException) -> LLMError:
    """Wrap a provider/SDK exception in the appropriate LLMError subclass.

    Already-LLMError instances are returned unchanged so ``call_with_retry``
    can be layered without double-wrapping.
    """
    if isinstance(exc, LLMError):
        return exc

    if isinstance(exc, openai.APIStatusError):
        status = getattr(exc, "status_code", None)
        if status == 429:
            return RateLimitError(str(exc), retry_after=_extract_retry_after(exc))
        if status is not None and 500 <= status < 600:
            return TransientError(f"{status}: {exc}")
        return FatalError(f"{status}: {exc}")

    if isinstance(exc, (openai.APITimeoutError, openai.APIConnectionError)):
        return TransientError(str(exc))

    return FatalError(f"{type(exc).__name__}: {exc}")
