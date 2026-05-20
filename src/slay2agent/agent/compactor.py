"""L0 Compaction Sub-agent — F-012.

When the in-context message history (L0) exceeds a configurable threshold,
this sub-agent summarises the oldest messages into a single summary message,
preserving the most-recent K messages verbatim.

Result structure: ``[summary_user_message] + [recent K messages]``

The stable prefix (summary + recent) improves KV-cache hit rate compared to a
sliding window, which would shift the prefix on every step.

This function is *fire-and-keep-going*: any exception is caught, logged with
``logger.error``, and the original L0 is returned unchanged so the main loop
is never blocked.

Compaction is written to ``subagent.jsonl`` via TraceWriter.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from slay2agent.agent.trace import SubagentRecord, TraceWriter
from slay2agent.llm.protocol import LLMAdapter, Message
from slay2agent.llm.retry import call_with_retry
from slay2agent.llm.usage import UsageTracker

logger = logging.getLogger(__name__)

_AGENT_ROLE = "compactor"

# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a context compactor for a Slay the Spire 2 AI agent.

You receive a transcript of past action/result messages from the current
game state segment (same state_type).  Your task: produce a concise but
information-dense summary that preserves:

- The key decisions made and why (inferred from the tool calls and results)
- Important outcomes: card plays, damage dealt/received, HP changes, buffs/debuffs
- Visible patterns or repeated actions and their results
- Anything that would inform future decisions in the same segment

Rules:
- Write in third person, past tense ("The agent played Bash … the enemy took 8 damage")
- Be specific: include numbers, card names, enemy names
- Keep it under 600 words
- Do NOT include game actions or tool calls in your response — plain prose only
- Return the summary and nothing else (no preamble, no sign-off)
"""


# ── Helpers ───────────────────────────────────────────────────────────────────


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


def _format_transcript(messages: list[Message]) -> str:
    """Serialise old L0 messages into a readable transcript for the compactor LLM."""
    lines: list[str] = ["## Transcript to summarise\n"]
    for i, msg in enumerate(messages):
        if msg.role == "assistant":
            if msg.tool_calls:
                tc = msg.tool_calls[0]
                lines.append(
                    f"[{i}] AGENT → tool_call: {tc.name}({tc.arguments})"
                )
            else:
                lines.append(f"[{i}] AGENT → text: {(msg.content or '').strip()[:200]}")
        elif msg.role == "tool":
            content_preview = (msg.content or "").strip()[:300]
            lines.append(f"[{i}] TOOL RESULT: {content_preview}")
        else:
            lines.append(f"[{i}] {msg.role.upper()}: {(msg.content or '').strip()[:200]}")
    return "\n".join(lines)


# ── Public entry point ────────────────────────────────────────────────────────


def run_l0_compaction(
    l0: list[Message],
    *,
    compact_keep: int,
    adapter: LLMAdapter,
    tracker: UsageTracker,
    trace: TraceWriter,
    model: str,
    step: int,
    state_type: str,
    extra_body: dict[str, Any] | None = None,
) -> list[Message]:
    """Compact the oldest part of L0 into a summary message.

    Splits ``l0`` into:
      - ``old = l0[:-compact_keep]``  — messages to summarise
      - ``recent = l0[-compact_keep:]`` — messages preserved verbatim

    Calls the LLM to produce a one-shot summary of ``old``, then returns::

        [Message(role="user", content="[Compacted context …]:\\n{summary}")] + recent

    On any failure, logs an error and returns the original ``l0`` unchanged so
    the main loop is never blocked.

    Args:
        l0: Current in-context message history.
        compact_keep: Number of recent messages to preserve verbatim.
        adapter: Shared LLMAdapter.
        tracker: Shared UsageTracker (calls recorded under "compactor").
        trace: Shared TraceWriter (result written to subagent.jsonl).
        model: LLM model slug.
        step: Current main-loop step (for trace / logging context).
        state_type: Current game state_type (for trace context).
        extra_body: Optional provider-specific request parameters.

    Returns:
        Compacted L0 on success, original L0 on failure.
    """
    old = l0[:-compact_keep] if compact_keep > 0 else l0
    recent = l0[-compact_keep:] if compact_keep > 0 else []
    n_old = len(old)

    logger.info(
        "compactor: triggered at step=%d state_type=%s; l0=%d, old=%d, keep=%d",
        step, state_type, len(l0), n_old, compact_keep,
    )

    all_usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
    request_msgs: list[dict[str, Any]] = []
    response_msg: dict[str, Any] = {}

    try:
        system_msg = Message(role="system", content=_SYSTEM_PROMPT)
        user_msg = Message(role="user", content=_format_transcript(old))
        request_msgs = [_message_to_dict(system_msg), _message_to_dict(user_msg)]

        resp = call_with_retry(
            lambda: adapter.chat(
                [system_msg, user_msg],
                tools=None,
                tool_choice="none",
                extra_body=extra_body,
            )
        )
        tracker.record(_AGENT_ROLE, resp.model, resp.usage)
        all_usage["input_tokens"] = resp.usage.input_tokens
        all_usage["output_tokens"] = resp.usage.output_tokens
        response_msg = _message_to_dict(resp.message)

        summary_text = (resp.message.content or "").strip()
        if not summary_text:
            raise ValueError("compactor returned empty summary")

        summary_msg = Message(
            role="user",
            content=(
                f"[Compacted context — summarised {n_old} messages from {state_type} segment]:\n"
                + summary_text
            ),
        )

        n_after = 1 + len(recent)  # summary + recent
        file_diff = f"compacted {n_old} → 1 summary + {len(recent)} recent ({n_after} total)"
        logger.info("compactor: %s", file_diff)

        trace.write_subagent(
            SubagentRecord(
                agent_role=_AGENT_ROLE,
                timestamp=_timestamp(),
                trigger_reason=f"l0_threshold:step={step}:state_type={state_type}",
                input_summary=(
                    f"l0_len={len(l0)}, old={n_old}, keep={compact_keep}, state_type={state_type}"
                ),
                llm_request_messages=request_msgs,
                llm_response_message=response_msg,
                llm_usage=all_usage,
                file_diff_summary=file_diff,
            )
        )

        return [summary_msg] + recent

    except Exception as exc:
        logger.error(
            "compactor: failed at step=%d (state_type=%s): %s — keeping original L0",
            step, state_type, exc,
            exc_info=True,
        )
        # Best-effort failure trace.
        try:
            trace.write_subagent(
                SubagentRecord(
                    agent_role=_AGENT_ROLE,
                    timestamp=_timestamp(),
                    trigger_reason=f"l0_threshold:step={step}:state_type={state_type}",
                    input_summary=f"l0_len={len(l0)}, old={n_old}, keep={compact_keep} — FAILED",
                    llm_request_messages=request_msgs,
                    llm_response_message=response_msg,
                    llm_usage=all_usage,
                    file_diff_summary=f"ERROR: {exc}",
                )
            )
        except Exception as trace_exc:
            logger.error("compactor: also failed to write error trace: %s", trace_exc)

        return l0
