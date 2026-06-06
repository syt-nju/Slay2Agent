"""Phase 1 orchestration — ``slay2agent analyze`` (F-013).

Discovers runs that still lack a ``failure_report.json``, reconstructs each
trajectory deterministically, reviews it with the failure analyzer, and writes
the report.  Kept separate from the argparse layer so it is unit-testable with
a fake adapter.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from slay2agent.llm.protocol import LLMAdapter
from slay2agent.llm.usage import UsageTracker
from slay2agent.maintenance.failure_analyzer import analyze_run
from slay2agent.maintenance.report import (
    count_steps,
    find_unanalyzed_runs,
    iter_run_dirs,
    meets_min_steps,
    read_run_meta,
    write_report,
)
from slay2agent.memory.trajectory import reconstruct_trajectory

logger = logging.getLogger(__name__)


@dataclass
class AnalyzeResult:
    analyzed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    skipped: int = 0
    skipped_short: int = 0

    @property
    def total_failures(self) -> int:
        return len(self.analyzed)


def analyze_runs(
    runs_dir: Path,
    adapter: LLMAdapter,
    tracker: UsageTracker,
    *,
    model: str,
    force: bool = False,
    min_steps: int = 10,
    extra_body: dict | None = None,
) -> AnalyzeResult:
    """Analyze every run missing a report (or all runs when ``force``).

    Runs with fewer than ``min_steps`` are skipped without writing a report —
    they are usually manual aborts or early crashes, not worth learning from.

    A failure on one run is logged and does not abort the batch — the run is
    left unanalyzed so the next pass retries it.
    """
    targets = iter_run_dirs(runs_dir) if force else find_unanalyzed_runs(runs_dir)
    result = AnalyzeResult()
    logger.info(
        "analyze: %d run(s) to review under %s (force=%s, min_steps=%d)",
        len(targets), runs_dir, force, min_steps,
    )

    for run_dir in targets:
        run_id = run_dir.name
        total_steps = count_steps(run_dir)
        if not meets_min_steps(run_dir, min_steps):
            logger.info(
                "analyze: %s has %d step(s) < min_steps=%d — skipping",
                run_id, total_steps, min_steps,
            )
            result.skipped_short += 1
            continue

        trajectory = reconstruct_trajectory(run_dir / "steps.jsonl")
        if not trajectory:
            logger.warning("analyze: %s has no usable trajectory — skipping", run_id)
            result.skipped += 1
            continue

        meta = read_run_meta(run_dir)
        try:
            report = analyze_run(
                trajectory,
                run_id=run_id,
                termination_reason=meta["termination_reason"],
                total_steps=total_steps,
                adapter=adapter,
                tracker=tracker,
                model=model,
                extra_body=extra_body,
            )
        except Exception as exc:
            logger.error("analyze: review failed for %s: %s", run_id, exc)
            result.failed.append(run_id)
            continue

        write_report(run_dir, report)
        result.analyzed.append(run_id)

    return result
