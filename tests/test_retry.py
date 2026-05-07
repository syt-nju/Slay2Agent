"""Retry wrapper tests (§9.2).

Mocks ``time.sleep`` so the test suite runs in milliseconds.
"""

from __future__ import annotations

import logging

import pytest

from slay2agent.llm import retry as retry_mod
from slay2agent.llm.errors import FatalError, RateLimitError, TransientError
from slay2agent.llm.retry import call_with_retry


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    calls: list[float] = []
    monkeypatch.setattr(retry_mod.time, "sleep", lambda s: calls.append(s))
    return calls


def test_succeeds_first_try_no_sleep(no_sleep):
    assert call_with_retry(lambda: 42) == 42
    assert no_sleep == []


def test_transient_retries_then_succeeds(no_sleep):
    attempts = {"n": 0}

    def fn():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise TransientError("5xx")
        return "ok"

    assert call_with_retry(fn, base_delay=0.1, max_delay=0.1) == "ok"
    assert attempts["n"] == 3
    assert len(no_sleep) == 2


def test_rate_limit_retry_after_honoured(no_sleep):
    attempts = {"n": 0}

    def fn():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RateLimitError("slow down", retry_after=7.5)
        return "ok"

    assert call_with_retry(fn) == "ok"
    assert no_sleep == [7.5]


def test_rate_limit_without_retry_after_uses_backoff(no_sleep, monkeypatch):
    monkeypatch.setattr(retry_mod, "jittered_backoff", lambda *_a, **_kw: 0.25)
    attempts = {"n": 0}

    def fn():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RateLimitError("slow down")
        return "ok"

    assert call_with_retry(fn) == "ok"
    assert no_sleep == [0.25]


def test_fatal_aborts_immediately(no_sleep):
    with pytest.raises(FatalError):
        call_with_retry(lambda: (_ for _ in ()).throw(FatalError("nope")))
    assert no_sleep == []


def test_exhausts_max_attempts(no_sleep, caplog):
    caplog.set_level(logging.WARNING, logger="slay2agent.llm.retry")

    def fn():
        raise TransientError("flaky")

    with pytest.raises(TransientError):
        call_with_retry(fn, max_attempts=3, base_delay=0.01, max_delay=0.01)

    assert len(no_sleep) == 2  # sleeps between attempts 1→2 and 2→3
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(warnings) == 2
    assert len(errors) == 1


def test_unknown_exception_is_classified_fatal(no_sleep):
    def fn():
        raise RuntimeError("??")

    with pytest.raises(FatalError):
        call_with_retry(fn)
    assert no_sleep == []
