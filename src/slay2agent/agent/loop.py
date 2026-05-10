"""Main agent loop — Phase 1 Demo Loop (F-005).

Implements the end-to-end flow described in framework-design.md:

    loop:
        state = get_state()
        if state_type changed and prev segment non-empty:
            flush L0
            (skill_creator stub — runs in F-008b)
        inject skill metadata + oracle.md into system prompt
        main_agent.decide(compact_view(state)) -> tool call or text
        tool_bridge.gate -> post_action_and_settle
        write trace step
        if loop_detector triggers OR state == game_over:
            (oracle_updater stub — runs in F-008c)
            break

L0 is the in-context message history.  It is cleared on every state_type
transition so the agent starts each "screen" with a clean context (only the
injected system prompt carries over via the skill/oracle stubs).

The agent does NOT expose python-exec, compact, or write-memory tools.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from slay2agent.agent.tool_bridge import LoopDetected, LoopDetector, ToolBridge
from slay2agent.agent.trace import (
    StepRecord,
    TerminationReason,
    TraceWriter,
    new_run_id,
)
from slay2agent.config import Config
from slay2agent.game.client import ActionError, GameClient
from slay2agent.game.schema import parse, to_compact_prompt
from slay2agent.llm.openrouter import OpenRouterAdapter
from slay2agent.llm.protocol import AgentRole, Message, ToolCall
from slay2agent.llm.retry import call_with_retry
from slay2agent.llm.usage import UsageTracker

logger = logging.getLogger(__name__)

# Default configuration for the demo loop.
_DEFAULT_WINDOW_SIZE = 10
_DEFAULT_REPEAT_THRESHOLD = 4
_AGENT_ROLE: AgentRole = "main"

# System prompt preamble — injected every LLM call.
_SYSTEM_PREAMBLE = """You are an expert player of Slay the Spire 2.
Your job is to navigate the game from the main menu, select Ironclad at Ascension 0, and play until game_over or until you are told to stop.

Rules:
- Always call exactly one tool per response. Never reply with plain text only.
- Use menu_select to navigate menus (main menu, character select, singleplayer, ascension).
- In combat, play cards or end_turn. Spend energy efficiently.
- On the map, choose_map_node to advance.
- On rewards/card_reward, claim what is useful or skip.
- list_skills / read_skill give you strategic memory — consult them when unsure.
- game_over means the run ended; you will be stopped automatically.
"""


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


@dataclass
class RunConfig:
    """User-facing configuration for a single demo run."""

    character: str = "IRONCLAD"
    ascension: int = 0
    runs_dir: Path = field(default_factory=lambda: Path("runs"))
    window_size: int = _DEFAULT_WINDOW_SIZE
    repeat_threshold: int = _DEFAULT_REPEAT_THRESHOLD


def _build_system_prompt(
    *,
    skill_metadata_list: list[str],
    oracle_content: str,
) -> str:
    """Assemble the full system prompt from preamble + memory injections.

    In F-005 (pre-F-008a) both lists are empty/empty-string; the structure is
    already correct so F-008a just fills them in.
    """
    parts = [_SYSTEM_PREAMBLE.strip()]

    if oracle_content:
        parts.append("\n## Oracle (global strategy)\n" + oracle_content)

    if skill_metadata_list:
        parts.append("\n## Available Skills\n" + "\n".join(skill_metadata_list))
    else:
        parts.append("\n## Available Skills\n(none yet)")

    return "\n".join(parts)


def run_demo_loop(cfg: Config, run_cfg: RunConfig) -> Path:
    """Run the Phase 1 demo loop.

    Returns the path to the run directory (``runs/<run_id>/``).

    The loop terminates on:
    - ``game_over`` state_type
    - ``LoopDetected`` from the loop detector
    - Unrecoverable error (re-raised after logging)

    All termination paths write ``summary.json`` before returning.
    """
    run_id = new_run_id()
    run_dir = run_cfg.runs_dir / run_id
    trace = TraceWriter(run_dir)
    tracker = UsageTracker()

    api_key = cfg.llm.require_api_key()
    adapter = OpenRouterAdapter(cfg.llm.model, api_key, timeout=cfg.llm.timeout)
    loop_detector = LoopDetector(
        window_size=run_cfg.window_size,
        repeat_threshold=run_cfg.repeat_threshold,
    )

    logger.info("starting run %s  character=%s asc=%d", run_id, run_cfg.character, run_cfg.ascension)

    termination_reason: TerminationReason = "error"
    extra_summary: dict[str, Any] = {}

    with GameClient(cfg.game.base_url, timeout=cfg.game.timeout) as client:
        bridge = ToolBridge(client=client, loop_detector=loop_detector)

        # L0: in-context conversation history (cleared on state_type change).
        l0: list[Message] = []
        prev_state_type: str | None = None
        step = 0

        try:
            while True:
                raw_state = client.get_state()
                parsed = parse(raw_state)
                state_type = parsed.state_type
                compact = to_compact_prompt(parsed)

                # ── L0 clear on state_type transition ───────────────────────
                l0_cleared = False
                if prev_state_type is not None and state_type != prev_state_type:
                    if l0:
                        logger.info(
                            "state_type changed %s → %s — clearing L0 (%d messages)",
                            prev_state_type, state_type, len(l0),
                        )
                        l0 = []
                        l0_cleared = True
                    # F-008b: skill_creator would fire here (stub in F-005).

                prev_state_type = state_type

                # ── Terminate on game_over before LLM call ───────────────────
                if state_type == "game_over":
                    logger.info("game_over reached — terminating run")
                    termination_reason = "game_over"
                    # Write a terminal step record without an LLM call.
                    trace.write_step(StepRecord(
                        step=step,
                        timestamp=_timestamp(),
                        state_type=state_type,
                        l0_cleared=l0_cleared,
                        skill_metadata_ids=[],
                        oracle_version=None,
                        llm_request_messages=[],
                        llm_response_message={},
                        llm_usage={"input_tokens": 0, "output_tokens": 0},
                        llm_stop_reason="stop",
                        tool_name=None,
                        tool_args=None,
                        tool_result_state_type=None,
                        settled_state_summary=compact,
                    ))
                    break

                # ── Assemble system prompt ───────────────────────────────────
                system_content = _build_system_prompt(
                    skill_metadata_list=[],   # F-008a will fill this
                    oracle_content="",        # F-008a will fill this
                )
                system_msg = Message(role="system", content=system_content)
                user_msg = Message(role="user", content=compact)

                full_messages = [system_msg] + l0 + [user_msg]
                tools = bridge.visible_tools(state_type)

                # ── LLM call ─────────────────────────────────────────────────
                resp = call_with_retry(
                    lambda: adapter.chat(full_messages, tools, tool_choice="required")
                )
                tracker.record(_AGENT_ROLE, resp.model, resp.usage)

                assistant_msg = resp.message

                # ── Dispatch tool call ────────────────────────────────────────
                tool_call: ToolCall | None = None
                tool_result_state_type: str | None = None
                settled_summary = compact  # fallback if no tool call

                if assistant_msg.tool_calls:
                    tool_call = assistant_msg.tool_calls[0]
                    action_name = tool_call.name
                    action_args = tool_call.arguments

                    try:
                        result_raw = bridge.execute(state_type, action_name, action_args)
                        result_parsed = parse(result_raw)
                        tool_result_state_type = result_parsed.state_type
                        settled_summary = to_compact_prompt(result_parsed)

                        # Append to L0: assistant message + tool result.
                        l0.append(assistant_msg)
                        l0.append(Message(
                            role="tool",
                            content=settled_summary,
                            tool_call_id=tool_call.id,
                        ))

                    except LoopDetected as exc:
                        logger.error("loop_detector fired: %s", exc)
                        termination_reason = "loop_terminated"
                        extra_summary["loop_detail"] = {
                            "action": exc.action,
                            "args": exc.args,
                            "count": exc.count,
                            "window": exc.window,
                        }
                        # Write final step then break.
                        trace.write_step(StepRecord(
                            step=step,
                            timestamp=_timestamp(),
                            state_type=state_type,
                            l0_cleared=l0_cleared,
                            skill_metadata_ids=[],
                            oracle_version=None,
                            llm_request_messages=[_message_to_dict(m) for m in full_messages],
                            llm_response_message=_message_to_dict(assistant_msg),
                            llm_usage={
                                "input_tokens": resp.usage.input_tokens,
                                "output_tokens": resp.usage.output_tokens,
                            },
                            llm_stop_reason=resp.stop_reason,
                            tool_name=action_name,
                            tool_args=action_args,
                            tool_result_state_type=None,
                            settled_state_summary=compact,
                        ))
                        break

                    except ActionError as exc:
                        logger.error("action %r failed: %s — injecting error into L0", action_name, exc)
                        l0.append(assistant_msg)
                        l0.append(Message(
                            role="tool",
                            content=f"ERROR: {exc}",
                            tool_call_id=tool_call.id,
                        ))

                    except ValueError as exc:
                        # Gate rejection.
                        logger.error("gate rejected %r: %s", action_name, exc)
                        l0.append(assistant_msg)
                        l0.append(Message(
                            role="tool",
                            content=f"ERROR: {exc}",
                            tool_call_id=tool_call.id,
                        ))

                else:
                    # LLM returned text only (tool_choice=required should prevent this,
                    # but handle gracefully).
                    logger.warning("LLM returned no tool call at step %d — appending text to L0", step)
                    l0.append(assistant_msg)

                # ── Write step trace ──────────────────────────────────────────
                trace.write_step(StepRecord(
                    step=step,
                    timestamp=_timestamp(),
                    state_type=state_type,
                    l0_cleared=l0_cleared,
                    skill_metadata_ids=[],
                    oracle_version=None,
                    llm_request_messages=[_message_to_dict(m) for m in full_messages],
                    llm_response_message=_message_to_dict(assistant_msg),
                    llm_usage={
                        "input_tokens": resp.usage.input_tokens,
                        "output_tokens": resp.usage.output_tokens,
                    },
                    llm_stop_reason=resp.stop_reason,
                    tool_name=tool_call.name if tool_call else None,
                    tool_args=tool_call.arguments if tool_call else None,
                    tool_result_state_type=tool_result_state_type,
                    settled_state_summary=settled_summary,
                ))

                step += 1

        except LoopDetected:
            # Already handled inside the loop.
            pass
        except Exception as exc:
            logger.error("unhandled error in run loop: %s", exc, exc_info=True)
            termination_reason = "error"
            extra_summary["error"] = str(exc)

        finally:
            # F-008c: oracle_updater would fire here.
            trace.write_summary(
                termination_reason=termination_reason,
                tracker=tracker,
                extra=extra_summary or None,
            )
            logger.info("run %s finished: %s", run_id, termination_reason)

    return run_dir
