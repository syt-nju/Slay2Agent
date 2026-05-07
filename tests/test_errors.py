"""Error classification tests (§9.1)."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import openai
import pytest

from slay2agent.llm.errors import (
    FatalError,
    LLMError,
    RateLimitError,
    TransientError,
    classify,
)


def _api_status_error(status: int, retry_after: str | None = None) -> openai.APIStatusError:
    """Build a minimal APIStatusError with the fields classify() reads."""
    headers = httpx.Headers({"retry-after": retry_after} if retry_after else {})
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    response = httpx.Response(status, headers=headers, request=request)
    return openai.APIStatusError("boom", response=response, body=None)


def test_429_maps_to_rate_limit_with_retry_after():
    err = classify(_api_status_error(429, retry_after="3.5"))
    assert isinstance(err, RateLimitError)
    assert err.retry_after == pytest.approx(3.5)


def test_429_without_header_has_none_retry_after():
    err = classify(_api_status_error(429))
    assert isinstance(err, RateLimitError)
    assert err.retry_after is None


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_5xx_maps_to_transient(status: int):
    err = classify(_api_status_error(status))
    assert isinstance(err, TransientError)
    assert not isinstance(err, RateLimitError)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_4xx_non_429_maps_to_fatal(status: int):
    err = classify(_api_status_error(status))
    assert isinstance(err, FatalError)


def test_timeout_and_connection_map_to_transient():
    request = httpx.Request("POST", "https://example.com")
    t_err = openai.APITimeoutError(request)
    c_err = openai.APIConnectionError(request=request)
    assert isinstance(classify(t_err), TransientError)
    assert isinstance(classify(c_err), TransientError)


def test_unknown_exception_maps_to_fatal():
    err = classify(RuntimeError("mystery"))
    assert isinstance(err, FatalError)


def test_llm_error_passthrough():
    original = TransientError("already classified")
    assert classify(original) is original


def test_hierarchy():
    assert issubclass(RateLimitError, TransientError)
    assert issubclass(TransientError, LLMError)
    assert issubclass(FatalError, LLMError)
