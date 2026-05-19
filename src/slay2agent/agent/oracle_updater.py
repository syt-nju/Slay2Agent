"""Oracle Updater Sub-agent — F-008c.

Triggered at the end of every run (any termination reason: game_over,
loop_terminated, error).  Receives a compact summary of the completed run
plus the current oracle.md, and rewrites oracle.md with updated global
meta-strategy.

The LLM is given only read-only memory tools (list_skills / read_skill) and
must return the new oracle.md content as plain text.  The runner writes the
content to disk.

This function is *fire-and-forget*: any exception is caught here, logged with
``logger.error``, and the main loop continues uninterrupted.  oracle.md is
never corrupted on failure — the original file is left intact.

Mandatory workflow enforced via system prompt:
    1. (Optionally) list_skills / read_skill — check tactical knowledge already recorded
    2. Reply with the COMPLETE new oracle.md content as plain text
       (runner writes verbatim to oracle.md)
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from slay2agent.agent.trace import SubagentRecord, TraceWriter
from slay2agent.agent.tool_bridge import _memory_tool_schemas
from slay2agent.llm.protocol import LLMAdapter, Message, ToolCall
from slay2agent.llm.retry import call_with_retry
from slay2agent.llm.usage import UsageTracker
from slay2agent.memory.oracle import read_oracle
from slay2agent.memory.skill_registry import SkillRegistry
from slay2agent.viewer.observer import NoOpObserver, RunObserver

logger = logging.getLogger(__name__)

_AGENT_ROLE = "oracle_updater"

# ── System prompt ────────────────────────────────────────────────────────────

_SYSTEM_TEMPLATE = """\
You are the oracle curator for a Slay the Spire 2 AI agent.
You have just completed a full run. Your task is to rewrite oracle.md —
the global meta-strategy document injected into every future system prompt.

HARD LIMIT: Your response MUST be at most {max_tokens} tokens of markdown text
(approximately {max_chars} characters). If the current oracle is already good,
make minimal edits or return it unchanged.

PROCESS:
1. Optionally call list_skills / read_skill to check tactical knowledge already recorded.
2. Reply with the COMPLETE new oracle.md content (no preamble, no explanation).
   Your entire reply will be written verbatim to oracle.md.

Rules:
- Write only actionable, generalizable strategy — no specific run events.
- Keep it under {max_tokens} tokens.
- Preserve valuable existing content; do not discard good strategy without reason.
- Do not modify or reference the skill library files.
{oracle_section}"""


def _build_system_prompt(oracle_max_tokens: int, max_chars: int, oracle_content: str) -> str:
    if oracle_content:
        oracle_section = (
            "\n## Current oracle.md (global strategy — rewrite this)\n" + oracle_content
        )
    else:
        oracle_section = "\n## Current oracle.md\n(empty — write the first version)"
    return _SYSTEM_TEMPLATE.format(
        max_tokens=oracle_max_tokens,
        max_chars=max_chars,
        oracle_section=oracle_section,
    )


def _build_user_prompt(run_trace_summary: str) -> str:
    return (
        "## Completed Run Summary\n"
        + run_trace_summary
        + "\n\nBased on the above, please rewrite oracle.md with updated global strategy."
    )


# ── Write helper ─────────────────────────────────────────────────────────────


def _write_oracle_safe(oracle_path: Path, new_content: str, max_chars: int) -> bool:
    """Attempt to write oracle.md; returns True on success.

    - Empty content: skip write, return False.
    - Overlong content: truncate then write, return True (with warning).
    - OSError: propagates to the caller (caught by outer except).
    """
    content = new_content.strip()
    if not content:
        logger.warning("oracle_updater: LLM returned empty content — skipping write")
        return False
    if len(content) > max_chars:
        logger.warning(
            "oracle_updater: content %d chars > limit %d — truncating",
            len(content),
            max_chars,
        )
        content = content[:max_chars] + "\n\n_(truncated by oracle_updater)_"
    oracle_path.parent.mkdir(parents=True, exist_ok=True)
    oracle_path.write_text(content, encoding="utf-8")
    logger.info("oracle_updater: wrote oracle.md (%d chars)", len(content))
    return True


# ── Internal helpers ──────────────────────────────────────────────────────────


def _timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _message_to_dict(msg: Message) -> dict[str, Any]:
    d: dict[str, Any] = {"role": msg.role}
    if msg.content is not None:
        d["content"] = msg.content
    if msg.tool_calls:
        d["tool_calls"] = [
            {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
            for tc in msg.tool_calls
        ]
    if msg.tool_call_id is not None:
        d["tool_call_id"] = msg.tool_call_id
    return d


def _dispatch_memory_tool(
    action: str,
    args: dict[str, Any],
    registry: SkillRegistry,
) -> dict[str, Any]:
    """Route a read-only memory tool call to the skill registry."""
    if action == "list_skills":
        return registry.list_skills_response()
    if action == "read_skill":
        return registry.read_skill_response(args.get("skill_id", ""))
    raise ValueError(f"unknown tool: {action!r}")


# ── Public entry point ────────────────────────────────────────────────────────


def run_oracle_updater(
    run_trace_summary: str,
    skill_registry: SkillRegistry,
    oracle_path: Path,
    adapter: LLMAdapter,
    tracker: UsageTracker,
    trace: TraceWriter,
    *,
    model: str,
    termination_reason: str,
    oracle_max_tokens: int = 4000,
    max_steps: int = 8,
    observer: RunObserver | None = None,
    extra_body: dict | None = None,
) -> None:
    """Run the oracle_updater sub-agent at the end of a run.

    Dispatches a multi-turn LLM conversation that optionally consults the skill
    library (read-only) then returns the complete new oracle.md content as plain
    text.  The runner writes the text to oracle.md.

    Any exception is caught and logged — this function never raises.

    Args:
        run_trace_summary: Compact text summary of the completed run.
        skill_registry: Shared SkillRegistry (read-only access only).
        oracle_path: Path to oracle.md (may or may not exist yet).
        adapter: Shared LLMAdapter.
        tracker: Shared UsageTracker (calls recorded under "oracle_updater").
        trace: Shared TraceWriter (result written to subagent.jsonl).
        model: LLM model slug.
        termination_reason: e.g. "game_over", "loop_terminated", "error".
        oracle_max_tokens: Soft token limit for oracle.md (default 4000).
        max_steps: Safety limit on tool-call rounds (default 8).
    """
    if observer is None:
        observer = NoOpObserver()
    trigger = f"run_end:{termination_reason}"
    logger.info("oracle_updater: triggered by %s", trigger)

    all_usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
    last_response_msg: dict[str, Any] = {}
    conversation: list[Message] = []
    wrote_oracle = False
    max_chars = oracle_max_tokens * 4

    try:
        oracle_content = read_oracle(oracle_path)
        system_msg = Message(
            role="system",
            content=_build_system_prompt(oracle_max_tokens, max_chars, oracle_content),
        )
        user_msg = Message(
            role="user",
            content=_build_user_prompt(run_trace_summary),
        )
        conversation = [system_msg, user_msg]
        tools = list(_memory_tool_schemas())

        new_oracle_text = ""
        for _step in range(max_steps):
            resp = call_with_retry(
                lambda: adapter.chat(conversation, tools, tool_choice="auto",
                                     extra_body=extra_body)
            )
            tracker.record(_AGENT_ROLE, resp.model, resp.usage)
            all_usage["input_tokens"] += resp.usage.input_tokens
            all_usage["output_tokens"] += resp.usage.output_tokens
            last_response_msg = _message_to_dict(resp.message)
            conversation.append(resp.message)

            if not resp.message.tool_calls:
                # Text response = done; content is the new oracle.md
                new_oracle_text = resp.message.content or ""
                logger.info(
                    "oracle_updater: finished after %d step(s); oracle text length=%d",
                    _step + 1,
                    len(new_oracle_text),
                )
                break

            # Execute first tool call (ignore extras)
            tool_call: ToolCall = resp.message.tool_calls[0]
            extra_calls = resp.message.tool_calls[1:]

            try:
                result = _dispatch_memory_tool(
                    tool_call.name, tool_call.arguments, skill_registry
                )
            except Exception as exc:
                logger.error(
                    "oracle_updater tool dispatch error %r: %s", tool_call.name, exc
                )
                result = {"error": str(exc)}

            conversation.append(
                Message(
                    role="tool",
                    content=json.dumps(result, ensure_ascii=False),
                    tool_call_id=tool_call.id,
                )
            )
            for extra in extra_calls:
                conversation.append(
                    Message(
                        role="tool",
                        content="(skipped — only one tool call executed per step)",
                        tool_call_id=extra.id,
                    )
                )
        else:
            logger.warning(
                "oracle_updater: reached max_steps=%d without text response", max_steps
            )

        # Write oracle (skips on empty content).
        wrote_oracle = _write_oracle_safe(oracle_path, new_oracle_text, max_chars)
        if wrote_oracle:
            observer.on_memory_event("oracle_rewritten", f"{len(new_oracle_text)} chars")

        trace.write_subagent(
            SubagentRecord(
                agent_role=_AGENT_ROLE,
                timestamp=_timestamp(),
                trigger_reason=trigger,
                input_summary=(
                    f"oracle: {'yes' if oracle_content else 'no'}, "
                    f"termination: {termination_reason}"
                ),
                llm_request_messages=[_message_to_dict(m) for m in conversation[:-1]],
                llm_response_message=last_response_msg,
                llm_usage=all_usage,
                file_diff_summary="wrote oracle.md" if wrote_oracle else "no changes",
            )
        )

    except Exception as exc:
        logger.error(
            "oracle_updater: unhandled error (trigger=%s): %s",
            trigger,
            exc,
            exc_info=True,
        )
        # Best-effort: still try to record a failure trace.
        try:
            trace.write_subagent(
                SubagentRecord(
                    agent_role=_AGENT_ROLE,
                    timestamp=_timestamp(),
                    trigger_reason=trigger,
                    input_summary="(FAILED)",
                    llm_request_messages=[_message_to_dict(m) for m in conversation],
                    llm_response_message=last_response_msg,
                    llm_usage=all_usage,
                    file_diff_summary=f"ERROR: {exc}",
                )
            )
        except Exception as trace_exc:
            logger.error(
                "oracle_updater: also failed to write error trace: %s", trace_exc
            )
