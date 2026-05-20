"""Trace writer + run summary (F-007).

Writes to ``runs/<run_id>/``:
  - ``steps.jsonl``    — one JSON line per main-agent step
  - ``subagent.jsonl`` — one JSON line per sub-agent invocation (F-008b/c)
  - ``summary.json``   — written at run end; termination reason + token totals

All writers are append-safe: ``steps.jsonl`` and ``subagent.jsonl`` are opened
in append mode so a crashed run leaves partial data intact.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from slay2agent.llm.usage import UsageTracker

logger = logging.getLogger(__name__)

TerminationReason = Literal["game_over", "loop_terminated", "error"]


def new_run_id() -> str:
    """Generate a run ID like ``20260510T142300_abc12345``."""
    ts = time.strftime("%Y%m%dT%H%M%S")
    short = uuid.uuid4().hex[:8]
    return f"{ts}_{short}"


@dataclass
class StepRecord:
    """One step of the main agent loop written to ``steps.jsonl``."""

    step: int
    timestamp: str
    state_type: str
    l0_cleared: bool
    # Injected context identifiers
    skill_metadata_ids: list[str]
    oracle_version: str | None
    # LLM call
    llm_request_messages: list[dict[str, Any]]
    llm_response_message: dict[str, Any]
    llm_usage: dict[str, int]
    llm_stop_reason: str
    # Tool call (if any)
    tool_name: str | None
    tool_args: dict[str, Any] | None
    tool_result_state_type: str | None
    # Compact view of state after settle
    settled_state_summary: str
    # Loop warning: raw MCP state was injected into the tool result
    loop_warning_raw_injected: bool = False


@dataclass
class SubagentRecord:
    """One sub-agent invocation written to ``subagent.jsonl``."""

    agent_role: str
    timestamp: str
    trigger_reason: str
    input_summary: str
    llm_request_messages: list[dict[str, Any]]
    llm_response_message: dict[str, Any]
    llm_usage: dict[str, int]
    file_diff_summary: str  # Human-readable diff summary (e.g. "created skill foo.md")


class TraceWriter:
    """Manages the ``runs/<run_id>/`` directory and its three output files."""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._steps_path = run_dir / "steps.jsonl"
        self._subagent_path = run_dir / "subagent.jsonl"
        self._summary_path = run_dir / "summary.json"
        logger.info("trace writer initialised at %s", run_dir)

    # ── step writer ────────────────────────────────────────────────────────

    def write_step(self, record: StepRecord) -> None:
        """Append one step line to ``steps.jsonl``."""
        self._append(self._steps_path, asdict(record))

    # ── sub-agent writer ───────────────────────────────────────────────────

    def write_subagent(self, record: SubagentRecord) -> None:
        """Append one sub-agent invocation line to ``subagent.jsonl``.

        The interface is ready for F-008b/c; no calls happen in F-005.
        """
        self._append(self._subagent_path, asdict(record))

    # ── summary writer ─────────────────────────────────────────────────────

    def write_summary(
        self,
        *,
        termination_reason: TerminationReason,
        tracker: UsageTracker,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Write ``summary.json`` at run end.

        Always includes token fields for all three agent roles, even if they
        recorded no calls (value = 0 as required by F-005 acceptance criteria).
        """
        role_totals = tracker.role_totals()
        call_counts = tracker.role_call_counts()

        all_roles = ["main", "skill_creator", "oracle_updater", "skill_librarian", "compactor"]
        token_summary: dict[str, dict[str, int]] = {}
        for role in all_roles:
            u = role_totals.get(role)  # type: ignore[arg-type]
            token_summary[role] = {
                "input_tokens": u.input_tokens if u else 0,
                "output_tokens": u.output_tokens if u else 0,
                "calls": call_counts.get(role, 0),
            }

        summary: dict[str, Any] = {
            "termination_reason": termination_reason,
            "tokens": token_summary,
            "usage_detail": tracker.snapshot(),
        }
        if extra:
            summary.update(extra)

        self._summary_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        logger.info(
            "run summary written: reason=%s tokens=%s",
            termination_reason,
            {r: token_summary[r]["input_tokens"] + token_summary[r]["output_tokens"]
             for r in all_roles},
        )

    # ── agent_state snapshot ───────────────────────────────────────────────

    def write_agent_state_snapshot(self, agent_state_dir: Path) -> None:
        """Copy ``agent_state_dir`` into ``runs/<run_id>/agent_state_snapshot/``.

        Captures the post-run state of ``oracle.md`` + ``skills/`` so each run
        trace ships with the exact memory snapshot that produced (and was
        produced by) it.  Missing source is a silent no-op — early runs may
        have no ``agent_state/`` yet.
        """
        if not agent_state_dir.exists():
            logger.info(
                "agent_state snapshot: source %s does not exist — skipping",
                agent_state_dir,
            )
            return

        dest = self.run_dir / "agent_state_snapshot"
        try:
            shutil.copytree(agent_state_dir, dest, dirs_exist_ok=True)
        except OSError as exc:
            logger.error("agent_state snapshot: copy failed: %s", exc)
            return

        logger.info("agent_state snapshot written to %s", dest)

    # ── internal ───────────────────────────────────────────────────────────

    @staticmethod
    def _append(path: Path, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
