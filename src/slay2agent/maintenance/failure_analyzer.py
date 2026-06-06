"""Phase 1 of the F-013 pipeline — per-run trajectory review (failure analyzer).

Given a deterministically reconstructed trajectory, a single LLM call performs
a *复盘* (post-run review) of the WHOLE run — win or loss is irrelevant, every
run is reviewed — and returns concrete failure reasons, each backed by a step
range and a short trajectory excerpt.

The model must answer through the ``submit_failure_report`` tool so the output
is structured JSON rather than free text we would have to parse.  This module
is pure: it returns the report dict and never touches disk (the CLI writes it).
"""

from __future__ import annotations

import json
import logging
import time

from slay2agent.llm.protocol import LLMAdapter, Message, ToolSchema
from slay2agent.llm.retry import call_with_retry
from slay2agent.llm.usage import UsageTracker

logger = logging.getLogger(__name__)

_AGENT_ROLE = "failure_analyzer"

_SYSTEM_PROMPT = """\
You are a meticulous post-run reviewer for a Slay the Spire 2 AI agent.

You are given a COMPLETE, deterministically reconstructed trajectory of one run
(every step: the screen state, the action taken, any feedback/error, and the
resulting state). Perform a 复盘 (full review) of the run.

IMPORTANT:
- Review EVERY run regardless of outcome. Winning, losing, and unfinished runs
  all contain decision mistakes worth recording. Do NOT only look at deaths.
- A "failure" is any concrete, avoidable mistake: a bad strategic/tactical
  decision, wasted resources (energy/HP/gold), a misunderstanding of a game
  rule or UI, a repeated/looping action, a missed opportunity, or a death that
  good play would have prevented.
- Be specific and grounded. Every failure MUST cite the step range where it
  occurred and a short excerpt copied from the trajectory as evidence.
- If the run was genuinely well-played, return an empty failures list and say
  so in overall_review. Do not invent failures.

Respond by calling submit_failure_report exactly once.
"""

_REPORT_TOOL = ToolSchema(
    name="submit_failure_report",
    description="Submit the structured failure-analysis report for this run.",
    parameters={
        "type": "object",
        "properties": {
            "overall_review": {
                "type": "string",
                "description": "2-5 sentence holistic review of how the run went.",
            },
            "failures": {
                "type": "array",
                "description": "Concrete, evidence-backed mistakes. May be empty.",
                "items": {
                    "type": "object",
                    "properties": {
                        "summary": {
                            "type": "string",
                            "description": "One concise sentence naming the failure reason.",
                        },
                        "detail": {
                            "type": "string",
                            "description": "What went wrong and why it was avoidable.",
                        },
                        "step_range": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "description": "Inclusive [start, end] step indices where it occurred.",
                        },
                        "excerpt": {
                            "type": "string",
                            "description": "Short excerpt copied from the trajectory as evidence.",
                        },
                    },
                    "required": ["summary", "detail"],
                },
            },
        },
        "required": ["overall_review", "failures"],
    },
)


def _build_user_prompt(run_id: str, termination_reason: str, total_steps: int, trajectory: str) -> str:
    return (
        f"## Run metadata\n"
        f"- run_id: {run_id}\n"
        f"- termination_reason: {termination_reason}\n"
        f"- total_steps: {total_steps}\n\n"
        f"## Trajectory\n{trajectory}\n"
    )


def _normalize_failures(raw: object) -> list[dict]:
    """Coerce the model's failures payload into a stable shape."""
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        step_range = item.get("step_range")
        if not (isinstance(step_range, list) and all(isinstance(x, int) for x in step_range)):
            step_range = []
        out.append({
            "summary": str(item.get("summary", "")).strip(),
            "detail": str(item.get("detail", "")).strip(),
            "step_range": step_range,
            "excerpt": str(item.get("excerpt", "")).strip(),
        })
    return out


def analyze_run(
    trajectory: str,
    *,
    run_id: str,
    termination_reason: str,
    total_steps: int,
    adapter: LLMAdapter,
    tracker: UsageTracker,
    model: str,
    extra_body: dict | None = None,
) -> dict:
    """Run the single-shot failure review for one trajectory.

    Returns the failure-report dict (the CLI persists it). Raises ``ValueError``
    when the model does not return a usable ``submit_failure_report`` call so
    the run stays unanalyzed and is retried on the next pass.
    """
    system_msg = Message(role="system", content=_SYSTEM_PROMPT)
    user_msg = Message(
        role="user",
        content=_build_user_prompt(run_id, termination_reason, total_steps, trajectory),
    )

    resp = call_with_retry(
        lambda: adapter.chat(
            [system_msg, user_msg],
            [_REPORT_TOOL],
            tool_choice="required",
            extra_body=extra_body,
        )
    )
    tracker.record(_AGENT_ROLE, resp.model, resp.usage)

    tool_calls = resp.message.tool_calls or []
    report_call = next((tc for tc in tool_calls if tc.name == "submit_failure_report"), None)
    if report_call is None:
        raise ValueError(
            f"failure_analyzer: model did not call submit_failure_report for run {run_id}"
        )

    args = report_call.arguments
    if isinstance(args, str):
        # Some providers serialise arguments as a JSON string.
        args = json.loads(args)

    failures = _normalize_failures(args.get("failures"))
    logger.info("failure_analyzer: run %s → %d failure(s)", run_id, len(failures))

    return {
        "run_id": run_id,
        "analyzed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "model": resp.model,
        "termination_reason": termination_reason,
        "total_steps": total_steps,
        "overall_review": str(args.get("overall_review", "")).strip(),
        "failures": failures,
        "llm_usage": {
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
        },
    }
