"""Failure-report I/O and run discovery for the F-013 pipeline.

A ``failure_report.json`` lives next to each run's ``steps.jsonl`` and marks
that run as analyzed.  Phase 1 (analyze) writes these; Phase 2 (distill)
consumes them.  Keeping the schema flat and explicit avoids coupling the two
phases to anything beyond plain JSON on disk.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

FAILURE_REPORT_FILENAME = "failure_report.json"
STEPS_FILENAME = "steps.jsonl"
SUMMARY_FILENAME = "summary.json"
# Marker written into a report once its failures have been folded into the
# skill library by a distill pass — lets distill skip already-consumed runs.
DISTILLED_KEY = "distilled_at"

# Runs shorter than this are excluded from analyze/distill — they are usually
# manual aborts or early crashes, not meaningful play to learn from.
DEFAULT_MAINTENANCE_MIN_STEPS = 10


def report_path(run_dir: Path) -> Path:
    return run_dir / FAILURE_REPORT_FILENAME


def has_report(run_dir: Path) -> bool:
    return report_path(run_dir).exists()


def write_report(run_dir: Path, report: dict) -> None:
    path = report_path(run_dir)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("failure report written: %s", path)


def read_report(run_dir: Path) -> dict | None:
    path = report_path(run_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.error("failure report unreadable %s: %s", path, exc)
        return None


def read_run_meta(run_dir: Path) -> dict:
    """Best-effort read of ``summary.json`` for run metadata.

    Returns a dict with at least ``termination_reason`` (``"unknown"`` when the
    summary is missing or malformed — a run can be reviewed without it).
    """
    path = run_dir / SUMMARY_FILENAME
    if not path.exists():
        return {"termination_reason": "unknown"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.error("summary unreadable %s: %s", path, exc)
        return {"termination_reason": "unknown"}
    return {"termination_reason": data.get("termination_reason", "unknown")}


def count_steps(run_dir: Path) -> int:
    """Return the number of usable steps in a run's ``steps.jsonl``."""
    from slay2agent.memory.trajectory import load_steps

    return len(load_steps(run_dir / STEPS_FILENAME))


def meets_min_steps(run_dir: Path, min_steps: int) -> bool:
    """True when the run has at least ``min_steps`` reconstructed steps."""
    if min_steps <= 0:
        return True
    return count_steps(run_dir) >= min_steps


def iter_run_dirs(runs_dir: Path) -> list[Path]:
    """Return run directories (those containing ``steps.jsonl``), sorted by name."""
    if not runs_dir.exists():
        logger.error("runs dir not found: %s", runs_dir)
        return []
    return sorted(
        d for d in runs_dir.iterdir() if d.is_dir() and (d / STEPS_FILENAME).exists()
    )


def find_unanalyzed_runs(runs_dir: Path) -> list[Path]:
    """Runs that have a trajectory but no failure report yet (Phase 1 input)."""
    return [d for d in iter_run_dirs(runs_dir) if not has_report(d)]


def find_undistilled_reports(runs_dir: Path) -> list[Path]:
    """Run dirs whose failure report exists but has not been distilled (Phase 2 input)."""
    out: list[Path] = []
    for d in iter_run_dirs(runs_dir):
        report = read_report(d)
        if report is not None and not report.get(DISTILLED_KEY):
            out.append(d)
    return out
