"""Usage tracker: per-(role, model) token accumulation. No pricing.

Bucketing by ``(agent_role, model)`` is required so run summaries can split
tokens across agent roles (main / oracle_updater / compactor) even when they
share the same model slug — see F-002 acceptance #4 and the run summary
requirement in F-005 / F-007.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from slay2agent.llm.protocol import AgentRole, Usage

logger = logging.getLogger(__name__)


@dataclass
class UsageTracker:
    _buckets: dict[tuple[AgentRole, str], Usage] = field(default_factory=dict)
    _calls: dict[tuple[AgentRole, str], int] = field(default_factory=dict)

    def record(self, role: AgentRole, model: str, usage: Usage) -> None:
        bucket = self._buckets.setdefault((role, model), Usage())
        bucket.input_tokens += usage.input_tokens
        bucket.output_tokens += usage.output_tokens
        self._calls[(role, model)] = self._calls.get((role, model), 0) + 1
        logger.info(
            "usage role=%s model=%s this=[in=%d out=%d] total=[in=%d out=%d]",
            role,
            model,
            usage.input_tokens,
            usage.output_tokens,
            bucket.input_tokens,
            bucket.output_tokens,
        )

    def snapshot(self) -> dict[str, dict[str, dict[str, int]]]:
        """Nested view: ``{role: {model: {input, output, total}}}``."""
        out: dict[str, dict[str, dict[str, int]]] = {}
        for (role, model), u in self._buckets.items():
            out.setdefault(role, {})[model] = {
                "input": u.input_tokens,
                "output": u.output_tokens,
                "total": u.total,
            }
        return out

    def role_totals(self) -> dict[str, Usage]:
        """Per-role aggregate across all models that role used.

        Used by run summaries to report ``three agents x (input, output)``.
        Roles never recorded against return ``Usage()`` zeros via the caller's
        ``.get(role, Usage())`` — this method only includes seen roles.
        """
        agg: dict[str, Usage] = {}
        for (role, _model), u in self._buckets.items():
            target = agg.setdefault(role, Usage())
            target.input_tokens += u.input_tokens
            target.output_tokens += u.output_tokens
        return agg

    def role_call_counts(self) -> dict[str, int]:
        """Per-role total LLM call count across all models."""
        agg: dict[str, int] = {}
        for (role, _model), count in self._calls.items():
            agg[role] = agg.get(role, 0) + count
        return agg

    def total(self) -> Usage:
        agg = Usage()
        for u in self._buckets.values():
            agg.input_tokens += u.input_tokens
            agg.output_tokens += u.output_tokens
        return agg
