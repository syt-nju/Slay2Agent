"""Usage tracker: per-model token accumulation. No pricing.

Kept deliberately minimal — budget enforcement can be layered in ``record()``
later if a run ever needs a hard token cap.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from slay2agent.llm.protocol import Usage

logger = logging.getLogger(__name__)


@dataclass
class UsageTracker:
    _buckets: dict[str, Usage] = field(default_factory=dict)

    def record(self, model: str, usage: Usage) -> None:
        bucket = self._buckets.setdefault(model, Usage())
        bucket.input_tokens += usage.input_tokens
        bucket.output_tokens += usage.output_tokens
        logger.info(
            "usage model=%s this=[in=%d out=%d] total=[in=%d out=%d]",
            model,
            usage.input_tokens,
            usage.output_tokens,
            bucket.input_tokens,
            bucket.output_tokens,
        )

    def snapshot(self) -> dict[str, dict[str, int]]:
        return {
            model: {
                "input": u.input_tokens,
                "output": u.output_tokens,
                "total": u.total,
            }
            for model, u in self._buckets.items()
        }

    def total(self) -> Usage:
        agg = Usage()
        for u in self._buckets.values():
            agg.input_tokens += u.input_tokens
            agg.output_tokens += u.output_tokens
        return agg
