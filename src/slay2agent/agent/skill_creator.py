"""Skill Creator Sub-agent — F-008b.

Triggered at every ``state_type`` transition where the previous segment had
at least one step (L0 non-empty).  Receives the completed L0 conversation
history, inspects the skill library, and writes / deletes skill files as
appropriate.

The runner is *fire-and-forget* from the main agent's perspective: if it
raises an exception for any reason, that exception is caught here, logged
with ``logger.error``, and the main loop continues uninterrupted.

Mandatory workflow enforced via system prompt:
    1. list_skills   — discover what already exists
    2. read_skill(x) — inspect similar skills before deciding
    3. write_skill / delete_skill — zero or more mutations
    4. text response — summary of changes (or "no changes needed")
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from slay2agent.agent.trace import SubagentRecord, TraceWriter
from slay2agent.agent.tool_bridge import _memory_tool_schemas
from slay2agent.llm.openrouter import OpenRouterAdapter
from slay2agent.llm.protocol import Message, ToolCall, ToolSchema
from slay2agent.llm.retry import call_with_retry
from slay2agent.llm.usage import UsageTracker
from slay2agent.memory.oracle import read_oracle
from slay2agent.memory.skill_registry import SkillRegistry

logger = logging.getLogger(__name__)

_AGENT_ROLE = "skill_creator"

# ── Tool schemas ────────────────────────────────────────────────────────────

_WRITE_SKILL_SCHEMA = ToolSchema(
    name="write_skill",
    description=(
        "Create or overwrite a skill file. "
        "Use to record a new strategic insight or update an existing one."
    ),
    parameters={
        "type": "object",
        "properties": {
            "skill_id": {
                "type": "string",
                "description": (
                    "Unique snake_case identifier (e.g. 'ironclad_early_combat'). "
                    "Use the existing skill_id when updating."
                ),
            },
            "name": {
                "type": "string",
                "description": (
                    "Human-readable display name shown alongside the skill_id "
                    "(e.g. 'Ironclad — Early Combat')."
                ),
            },
            "description": {
                "type": "string",
                "description": (
                    "SOLE trigger signal injected into every system prompt. "
                    "MUST describe both WHAT the skill covers AND WHEN to load "
                    "it. Pattern: '<one-line summary of what>. Use when <concrete "
                    "trigger condition>.' Aim for 1–3 sentences."
                ),
            },
            "body": {
                "type": "string",
                "description": (
                    "Full markdown body. Start with a level-1 heading "
                    "('# <Name>') so it reads as a self-contained SKILL.md. "
                    "Concise, actionable strategy — NOT a replay of the game log."
                ),
            },
        },
        "required": ["skill_id", "name", "description", "body"],
        "additionalProperties": False,
    },
)

_DELETE_SKILL_SCHEMA = ToolSchema(
    name="delete_skill",
    description=(
        "Delete a skill that is no longer useful or has been merged into another."
    ),
    parameters={
        "type": "object",
        "properties": {
            "skill_id": {
                "type": "string",
                "description": "The id of the skill to delete.",
            },
        },
        "required": ["skill_id"],
        "additionalProperties": False,
    },
)


def _all_tools() -> list[ToolSchema]:
    """All tools available to the skill_creator: read-only + write/delete."""
    return list(_memory_tool_schemas()) + [_WRITE_SKILL_SCHEMA, _DELETE_SKILL_SCHEMA]


# ── System prompt ───────────────────────────────────────────────────────────

_SYSTEM_TEMPLATE = """\
You are the skill curator for a Slay the Spire 2 AI agent.
You have just observed a completed gameplay segment. Your job is to update the skill library so future runs benefit from what was learned.

Each skill is a single markdown file in agent_state/skills/<skill_id>.md with
mainstream agent-skill structure (aligned with Claude Code / Cursor SKILL.md):

    ---
    name: <human-readable display name>
    description: <what + when-to-use, one to three sentences>
    ---

    # <Name>

    <markdown body — actionable strategy, NOT a gameplay replay>

The `description` field is the SOLE trigger signal: the main agent only sees
the skill list + descriptions at inference time, and decides whether to
`read_skill` based on description alone. So every description MUST encode
both WHAT the skill covers AND WHEN to load it
(pattern: "<summary>. Use when <concrete trigger condition>.").

MANDATORY PROCESS — follow this order exactly:
1. Call list_skills to see what skills already exist.
2. For each insight you want to record, call read_skill on any skills with similar names or themes.
3. Decide for each insight: extend an existing skill / merge two similar skills / create a new skill / no-op.
4. Call write_skill or delete_skill as needed (may be zero calls).
5. When finished, reply with a plain-text summary of what you changed (or "no changes needed" if nothing was worth recording).

Rules:
- PREFER extending an existing skill over creating a new one.
- PREFER merging two similar skills over keeping duplicates.
- Only create a NEW skill for genuinely distinct strategic knowledge not covered elsewhere.
- skill_id must be snake_case, no spaces, no special characters.
- Body must start with a level-1 heading and be self-contained markdown.
- Never call write_skill or delete_skill before calling list_skills first.
- You cannot modify oracle.md.
{oracle_section}"""


def _build_system_prompt(oracle_content: str) -> str:
    if oracle_content:
        oracle_section = "\n## Current oracle.md (global strategy — read-only reference)\n" + oracle_content
    else:
        oracle_section = ""
    return _SYSTEM_TEMPLATE.format(oracle_section=oracle_section)


# ── L0 context serialisation ─────────────────────────────────────────────────

_MAX_L0_CHARS = 8000
_MAX_MSG_CONTENT = 600


def _summarise_l0(l0: list[Message], state_type: str) -> str:
    """Convert prev_l0 to a compact user message for the skill_creator."""
    lines = [f"## Gameplay Segment — state_type: {state_type!r}"]
    for msg in l0:
        role = msg.role
        if msg.tool_calls:
            calls_repr = ", ".join(
                f"{tc.name}({json.dumps(tc.arguments, ensure_ascii=False)[:120]})"
                for tc in msg.tool_calls
            )
            content_repr = f"[tool_calls: {calls_repr}]"
        elif msg.content:
            content_repr = msg.content[:_MAX_MSG_CONTENT]
            if len(msg.content) > _MAX_MSG_CONTENT:
                content_repr += " …(truncated)"
        else:
            content_repr = "(empty)"
        lines.append(f"{role}: {content_repr}")

    full = "\n".join(lines)
    if len(full) > _MAX_L0_CHARS:
        full = full[:_MAX_L0_CHARS] + "\n…(L0 truncated)"
    return full


# ── Internal helpers ─────────────────────────────────────────────────────────

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


def _dispatch_tool(
    action: str,
    args: dict[str, Any],
    registry: SkillRegistry,
) -> dict[str, Any]:
    """Route a tool call to the skill registry."""
    if action == "list_skills":
        return registry.list_skills_response()
    if action == "read_skill":
        return registry.read_skill_response(args.get("skill_id", ""))
    if action == "write_skill":
        registry.write_skill(
            skill_id=args["skill_id"],
            name=args["name"],
            description=args["description"],
            body=args["body"],
        )
        return {"ok": True, "skill_id": args["skill_id"]}
    if action == "delete_skill":
        deleted = registry.delete_skill(args.get("skill_id", ""))
        return {"ok": deleted, "skill_id": args.get("skill_id", "")}
    raise ValueError(f"unknown tool: {action!r}")


# ── Public entry point ───────────────────────────────────────────────────────

def run_skill_creator(
    prev_l0: list[Message],
    skill_registry: SkillRegistry,
    oracle_path: Path,
    adapter: OpenRouterAdapter,
    tracker: UsageTracker,
    trace: TraceWriter,
    *,
    model: str,
    prev_state_type: str,
    new_state_type: str,
    max_steps: int = 12,
) -> None:
    """Run the skill_creator sub-agent for a completed gameplay segment.

    Dispatches a multi-turn LLM conversation that inspects the skill library
    and writes/deletes skill files as appropriate.  Any exception is caught
    and logged — this function never raises, so the main agent loop is never
    interrupted by skill_creator failures.

    Args:
        prev_l0: The completed L0 history from the previous state_type segment.
        skill_registry: The shared SkillRegistry (write methods will be called).
        oracle_path: Path to oracle.md for read-only context injection.
        adapter: Shared OpenRouterAdapter.
        tracker: Shared UsageTracker (calls recorded under "skill_creator").
        trace: Shared TraceWriter (result written to subagent.jsonl).
        model: LLM model slug.
        prev_state_type: state_type of the segment just completed.
        new_state_type: state_type the game transitioned into.
        max_steps: Safety limit on tool-call rounds (default 12).
    """
    trigger = f"{prev_state_type} → {new_state_type}"
    logger.info("skill_creator: triggered by %s, L0 len=%d", trigger, len(prev_l0))

    all_usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
    file_changes: list[str] = []
    last_response_msg: dict[str, Any] = {}
    conversation: list[Message] = []

    try:
        oracle_content = read_oracle(oracle_path)
        system_msg = Message(role="system", content=_build_system_prompt(oracle_content))
        context_msg = Message(role="user", content=_summarise_l0(prev_l0, prev_state_type))
        conversation = [system_msg, context_msg]

        tools = _all_tools()

        for _step in range(max_steps):
            resp = call_with_retry(lambda: adapter.chat(conversation, tools, tool_choice="auto"))
            tracker.record(_AGENT_ROLE, resp.model, resp.usage)
            all_usage["input_tokens"] += resp.usage.input_tokens
            all_usage["output_tokens"] += resp.usage.output_tokens
            last_response_msg = _message_to_dict(resp.message)
            conversation.append(resp.message)

            if not resp.message.tool_calls:
                # Text response = done
                logger.info(
                    "skill_creator: finished after %d step(s); changes: %s",
                    _step + 1,
                    file_changes or "none",
                )
                break

            # Execute first tool call (ignore extras, consistent with main agent)
            tool_call: ToolCall = resp.message.tool_calls[0]
            extra_calls = resp.message.tool_calls[1:]

            try:
                result = _dispatch_tool(tool_call.name, tool_call.arguments, skill_registry)
            except Exception as exc:
                logger.error("skill_creator tool dispatch error %r: %s", tool_call.name, exc)
                result = {"error": str(exc)}

            if tool_call.name in ("write_skill", "delete_skill"):
                sid = tool_call.arguments.get("skill_id", "?")
                file_changes.append(f"{tool_call.name}({sid})")

            conversation.append(Message(
                role="tool",
                content=json.dumps(result, ensure_ascii=False),
                tool_call_id=tool_call.id,
            ))
            for extra in extra_calls:
                conversation.append(Message(
                    role="tool",
                    content="(skipped — only one tool call executed per step)",
                    tool_call_id=extra.id,
                ))
        else:
            logger.warning(
                "skill_creator: reached max_steps=%d without text response", max_steps
            )

        # Write subagent trace (always, even on no-change runs).
        trace.write_subagent(SubagentRecord(
            agent_role=_AGENT_ROLE,
            timestamp=_timestamp(),
            trigger_reason=trigger,
            input_summary=(
                f"L0 messages: {len(prev_l0)}, "
                f"oracle: {'yes' if oracle_content else 'no'}"
            ),
            llm_request_messages=[_message_to_dict(m) for m in conversation[:-1]],
            llm_response_message=last_response_msg,
            llm_usage=all_usage,
            file_diff_summary=(
                ", ".join(file_changes) if file_changes else "no changes"
            ),
        ))

    except Exception as exc:
        logger.error(
            "skill_creator: unhandled error (trigger=%s): %s",
            trigger,
            exc,
            exc_info=True,
        )
        # Best-effort: still try to record a failure trace.
        try:
            trace.write_subagent(SubagentRecord(
                agent_role=_AGENT_ROLE,
                timestamp=_timestamp(),
                trigger_reason=trigger,
                input_summary=f"L0 messages: {len(prev_l0)} (FAILED)",
                llm_request_messages=[_message_to_dict(m) for m in conversation],
                llm_response_message=last_response_msg,
                llm_usage=all_usage,
                file_diff_summary=f"ERROR: {exc}",
            ))
        except Exception as trace_exc:
            logger.error("skill_creator: also failed to write error trace: %s", trace_exc)
