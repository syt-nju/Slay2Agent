"""Deterministic trajectory reconstruction from ``steps.jsonl`` (F-013).

The offline skill maintenance pipeline reviews complete runs. To keep that
review reproducible and cheap, the trajectory fed to the failure analyzer is
rebuilt by **fixed code logic** — no LLM call, no re-parsing of raw game JSON.
We project a small, stable set of fields already recorded per step:

    state_type → action (tool_name + tool_args) → action_feedback → result state

Because ``steps.jsonl`` is the single source of truth, the same run always
reconstructs to the same transcript. Traces written before F-013 lack the
``action_feedback`` field; reconstruction treats it as absent (the failure /
rejection text then only appears embedded in the following step's result).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TrajectoryStep:
    """One projected step — the deterministic slice of a StepRecord we review."""

    step: int
    state_type: str
    l0_cleared: bool
    tool_name: str | None
    tool_args: dict | None
    action_feedback: str | None
    result_state_type: str | None
    settled_state_summary: str


def load_steps(steps_path: Path) -> list[TrajectoryStep]:
    """Parse ``steps.jsonl`` into projected trajectory steps.

    Malformed lines are logged and skipped rather than aborting the whole
    reconstruction — a single corrupt line should not lose an entire run.
    """
    steps: list[TrajectoryStep] = []
    if not steps_path.exists():
        logger.error("trajectory: steps file not found: %s", steps_path)
        return steps

    raw = steps_path.read_text(encoding="utf-8")
    for lineno, line in enumerate(raw.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError as exc:
            logger.error("trajectory: skip malformed line %d in %s: %s", lineno, steps_path, exc)
            continue
        steps.append(
            TrajectoryStep(
                step=d.get("step", lineno - 1),
                state_type=d.get("state_type", "?"),
                l0_cleared=bool(d.get("l0_cleared", False)),
                tool_name=d.get("tool_name"),
                tool_args=d.get("tool_args"),
                action_feedback=d.get("action_feedback"),
                result_state_type=d.get("tool_result_state_type"),
                settled_state_summary=d.get("settled_state_summary", ""),
            )
        )
    return steps


def _format_action(tool_name: str | None, tool_args: dict | None) -> str:
    if not tool_name:
        return "(no action)"
    args_str = ""
    if tool_args:
        args_str = json.dumps(tool_args, ensure_ascii=False, sort_keys=True)
    return f"{tool_name}({args_str})"


def _format_step(s: TrajectoryStep) -> str:
    header = f"───── step {s.step} | state={s.state_type}"
    if s.l0_cleared:
        header += " | [screen boundary]"
    header += " ─────"

    lines = [header, f"ACTION: {_format_action(s.tool_name, s.tool_args)}"]
    if s.action_feedback:
        lines.append(f"FEEDBACK: {s.action_feedback}")
    result_state = s.result_state_type or s.state_type
    lines.append(f"RESULT → state={result_state}")
    if s.settled_state_summary:
        lines.append(s.settled_state_summary)
    return "\n".join(lines)


def reconstruct_trajectory(steps_path: Path) -> str:
    """Rebuild a human/LLM-readable transcript of a full run.

    Returns the empty string when there are no usable steps.
    """
    steps = load_steps(steps_path)
    if not steps:
        return ""

    blocks = [f"=== Reconstructed Trajectory ({len(steps)} steps) ==="]
    blocks.extend(_format_step(s) for s in steps)
    return "\n\n".join(blocks)
