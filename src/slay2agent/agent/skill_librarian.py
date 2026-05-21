"""Skill Librarian Sub-agent — run-end library merge pass.

Triggered at the end of every run where new skills were created during the run.
Reviews the entire skill library and merges overlapping/redundant skills.

The runner is *fire-and-forget* from the main agent's perspective: if it
raises an exception for any reason, that exception is caught here, logged
with ``logger.error``, and the main loop continues uninterrupted.

Only two outcomes are possible per skill pair:
    - merge (write_skill target + delete_skill source)
    - no change

The sub-agent signals completion by calling the ``done`` tool.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from slay2agent.agent.trace import SubagentRecord, TraceWriter
from slay2agent.llm.protocol import LLMAdapter, Message, ToolCall, ToolSchema
from slay2agent.llm.retry import call_with_retry
from slay2agent.llm.usage import UsageTracker
from slay2agent.memory.skill_registry import SkillRegistry
from slay2agent.viewer.observer import NoOpObserver, RunObserver

logger = logging.getLogger(__name__)

_AGENT_ROLE = "skill_librarian"

# ── Tool schemas ────────────────────────────────────────────────────────────

_READ_SKILL_SCHEMA = ToolSchema(
    name="read_skill",
    description="Read the full markdown body of a skill by its skill_id.",
    parameters={
        "type": "object",
        "properties": {
            "skill_id": {
                "type": "string",
                "description": "The skill identifier to read.",
            }
        },
        "required": ["skill_id"],
        "additionalProperties": False,
    },
)

_WRITE_SKILL_SCHEMA = ToolSchema(
    name="write_skill",
    description=(
        "Overwrite a skill file with merged content. "
        "Use only on an EXISTING skill_id that is the merge target."
    ),
    parameters={
        "type": "object",
        "properties": {
            "skill_id": {
                "type": "string",
                "description": "The existing skill_id to overwrite (merge target).",
            },
            "name": {
                "type": "string",
                "description": "Updated human-readable display name.",
            },
            "description": {
                "type": "string",
                "description": (
                    "Updated description covering both original trigger conditions. "
                    "Pattern: '<summary>. Use when <trigger>.'"
                ),
            },
            "body": {
                "type": "string",
                "description": (
                    "Full merged markdown body. Start with '# <Name>'. "
                    "Must be concise and actionable — do NOT simply concatenate."
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
        "Delete a skill that has been absorbed into another via merge. "
        "Only call this AFTER writing the merged content to the target skill."
    ),
    parameters={
        "type": "object",
        "properties": {
            "skill_id": {
                "type": "string",
                "description": "The skill_id to delete (merge source, already absorbed).",
            },
        },
        "required": ["skill_id"],
        "additionalProperties": False,
    },
)

_DONE_SCHEMA = ToolSchema(
    name="done",
    description=(
        "Signal that you have finished reviewing and merging. "
        "Call this when all merges are complete or no merges are needed."
    ),
    parameters={
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "Brief summary of merges performed (or 'no merges needed').",
            }
        },
        "required": ["summary"],
        "additionalProperties": False,
    },
)


def _all_tools() -> list[ToolSchema]:
    """All tools available to the skill_librarian."""
    return [_READ_SKILL_SCHEMA, _WRITE_SKILL_SCHEMA, _DELETE_SKILL_SCHEMA, _DONE_SCHEMA]


# ── System prompt ───────────────────────────────────────────────────────────

_SYSTEM_TEMPLATE = """\
You are the skill librarian for a Slay the Spire 2 AI agent.
Your ONLY job is to review the skill library and merge overlapping skills.

Here is the current skill library:

{skill_list_with_descriptions}

PROCESS:
1. Review the skill list above. Identify pairs/groups whose descriptions overlap significantly (same topic, same trigger condition, or one is a subset of another).
2. For each merge candidate pair: call read_skill on both to compare their full bodies.
3. Decide: merge or no change.
   - If merging: write_skill on the TARGET (keep its skill_id, combine the best of both), then delete_skill on the SOURCE.
   - If no overlap worth merging: do nothing for that pair.
4. When ALL merges are done (or no merges needed), call done to signal completion.

Rules:
- You may ONLY delete a skill as part of a merge (its content must first be absorbed into another).
- You may NOT create a skill_id that doesn't already exist — only write to existing IDs.
- Prefer keeping the skill with broader scope as the merge target.
- After merging, update the target's description to cover both original trigger conditions.
- Body must remain concise and actionable — do NOT simply concatenate the two bodies.
- If no skills overlap, call done immediately (no other tool calls needed).
- You MUST call done when finished. Do not end with a text-only response."""


def _build_system_prompt(skill_lines: list[str]) -> str:
    if skill_lines:
        skill_list = "\n".join(skill_lines)
    else:
        skill_list = "(empty library)"
    return _SYSTEM_TEMPLATE.format(skill_list_with_descriptions=skill_list)


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
) -> dict[str, Any] | None:
    """Route a tool call to the skill registry. Returns None for 'done'."""
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
    if action == "done":
        return None  # Sentinel: loop terminates
    raise ValueError(f"unknown tool: {action!r}")


# ── Public entry point ───────────────────────────────────────────────────────

def run_skill_librarian(
    skill_registry: SkillRegistry,
    adapter: LLMAdapter,
    tracker: UsageTracker,
    trace: TraceWriter,
    *,
    model: str,
    max_steps: int = 100,
    observer: RunObserver | None = None,
    extra_body: dict | None = None,
) -> None:
    """Run the skill_librarian sub-agent to merge overlapping skills.

    Dispatches a multi-turn LLM conversation that inspects the skill library
    and merges overlapping skills.  The sub-agent signals completion by calling
    the ``done`` tool.

    Any exception is caught and logged — this function never raises, so the
    main agent loop is never interrupted by skill_librarian failures.

    Args:
        skill_registry: The shared SkillRegistry (read/write/delete access).
        adapter: Shared LLMAdapter.
        tracker: Shared UsageTracker (calls recorded under "skill_librarian").
        trace: Shared TraceWriter (result written to subagent.jsonl).
        model: LLM model slug.
        max_steps: Safety limit on tool-call rounds (default 100).
        observer: Optional RunObserver for memory events.
        extra_body: Optional extra_body for LLM calls (e.g. thinking config).
    """
    if observer is None:
        observer = NoOpObserver()

    logger.info("skill_librarian: triggered")

    all_usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
    file_changes: list[str] = []
    last_response_msg: dict[str, Any] = {}
    conversation: list[Message] = []
    done_summary = ""

    try:
        # Build skill list for system prompt injection.
        all_skills = skill_registry.list_skills()
        skill_lines = [
            f"- [{s.skill_id}] {s.name} — {s.description}"
            for s in all_skills
        ]

        system_msg = Message(role="system", content=_build_system_prompt(skill_lines))
        user_msg = Message(role="user", content="Please review and merge overlapping skills.")
        conversation = [system_msg, user_msg]

        tools = _all_tools()

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
                # Text-only response without calling done — treat as implicit done.
                done_summary = resp.message.content or "(no summary)"
                logger.info(
                    "skill_librarian: text response without done tool at step %d — treating as done",
                    _step + 1,
                )
                break

            # Execute first tool call (ignore extras, consistent with other sub-agents).
            tool_call: ToolCall = resp.message.tool_calls[0]
            extra_calls = resp.message.tool_calls[1:]

            try:
                result = _dispatch_tool(tool_call.name, tool_call.arguments, skill_registry)
            except Exception as exc:
                logger.error("skill_librarian tool dispatch error %r: %s", tool_call.name, exc)
                result = {"error": str(exc)}

            # Handle done tool — terminates the loop.
            if tool_call.name == "done":
                done_summary = tool_call.arguments.get("summary", "")
                logger.info(
                    "skill_librarian: done called after %d step(s); summary: %s; changes: %s",
                    _step + 1,
                    done_summary,
                    file_changes or "none",
                )
                # Append the tool response for trace completeness.
                conversation.append(Message(
                    role="tool",
                    content=json.dumps({"ok": True}, ensure_ascii=False),
                    tool_call_id=tool_call.id,
                ))
                for extra in extra_calls:
                    conversation.append(Message(
                        role="tool",
                        content="(skipped — only one tool call executed per step)",
                        tool_call_id=extra.id,
                    ))
                break

            # Track file changes for trace summary.
            if tool_call.name in ("write_skill", "delete_skill"):
                sid = tool_call.arguments.get("skill_id", "?")
                file_changes.append(f"{tool_call.name}({sid})")
                observer.on_memory_event(
                    "skill_merged" if tool_call.name == "write_skill" else "skill_deleted_merge",
                    sid,
                )

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
                "skill_librarian: reached max_steps=%d without done call", max_steps
            )
            done_summary = f"(max_steps={max_steps} reached without done)"

        # Write subagent trace.
        trace.write_subagent(SubagentRecord(
            agent_role=_AGENT_ROLE,
            timestamp=_timestamp(),
            trigger_reason="run_end:skills_created",
            input_summary=f"skills: {len(all_skills)}",
            llm_request_messages=[_message_to_dict(m) for m in conversation[:-1]],
            llm_response_message=last_response_msg,
            llm_usage=all_usage,
            file_diff_summary=(
                ", ".join(file_changes) if file_changes else "no merges"
            ),
        ))

    except Exception as exc:
        logger.error(
            "skill_librarian: unhandled error: %s",
            exc,
            exc_info=True,
        )
        # Best-effort: still try to record a failure trace.
        try:
            trace.write_subagent(SubagentRecord(
                agent_role=_AGENT_ROLE,
                timestamp=_timestamp(),
                trigger_reason="run_end:skills_created",
                input_summary="(FAILED)",
                llm_request_messages=[_message_to_dict(m) for m in conversation],
                llm_response_message=last_response_msg,
                llm_usage=all_usage,
                file_diff_summary=f"ERROR: {exc}",
            ))
        except Exception as trace_exc:
            logger.error("skill_librarian: also failed to write error trace: %s", trace_exc)
