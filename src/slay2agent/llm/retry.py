"""Retry utilities: jittered exponential backoff + typed retry wrapper.

``jittered_backoff`` is adapted from NousResearch/hermes-agent's retry_utils.
``call_with_retry`` adds an LLMError-aware wrapper so adapter call sites do
not need their own try/except around every SDK invocation.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import Callable, TypeVar

from slay2agent.llm.errors import (
    FatalError,
    LLMError,
    RateLimitError,
    TransientError,
    classify,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

_jitter_counter = 0
_jitter_lock = threading.Lock()


def jittered_backoff(
    attempt: int,
    *,
    base_delay: float = 5.0,
    max_delay: float = 120.0,
    jitter_ratio: float = 0.5,
) -> float:
    """Decorrelated exponential backoff. ``attempt`` is 1-based."""
    global _jitter_counter
    with _jitter_lock:
        _jitter_counter += 1
        tick = _jitter_counter

    exponent = max(0, attempt - 1)
    if exponent >= 63 or base_delay <= 0:
        delay = max_delay
    else:
        delay = min(base_delay * (2**exponent), max_delay)

    seed = (time.time_ns() ^ (tick * 0x9E3779B9)) & 0xFFFFFFFF
    rng = random.Random(seed)
    return delay + rng.uniform(0, jitter_ratio * delay)


def call_with_retry(
    fn: Callable[[], T],
    *,
    max_attempts: int = 4,
    base_delay: float = 5.0,
    max_delay: float = 120.0,
) -> T:
    """Invoke ``fn`` with retry on TransientError/RateLimitError.

    - ``RateLimitError.retry_after`` overrides the jittered backoff when set.
    - ``FatalError`` (and unknown exceptions, after classification) abort.
    - Each retry logs ``warning``, final failure logs ``error`` and re-raises.
    """
    last: LLMError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except BaseException as raw:
            err = classify(raw)
            last = err
            if isinstance(err, FatalError) or attempt >= max_attempts:
                break
            if isinstance(err, RateLimitError) and err.retry_after is not None:
                delay = err.retry_after
            else:
                delay = jittered_backoff(
                    attempt, base_delay=base_delay, max_delay=max_delay
                )
            logger.warning(
                "LLM call failed (attempt %d/%d, sleeping %.2fs): %s",
                attempt,
                max_attempts,
                delay,
                err,
            )
            time.sleep(delay)

    assert last is not None
    logger.error("LLM call exhausted retries: %s", last)
    raise last
