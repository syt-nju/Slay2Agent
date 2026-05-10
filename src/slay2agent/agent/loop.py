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
from slay2agent.game.schema import CombatView, parse, to_compact_prompt
from slay2agent.llm.openrouter import OpenRouterAdapter
from slay2agent.llm.protocol import AgentRole, Message, ToolCall
from slay2agent.llm.retry import call_with_retry
from slay2agent.llm.usage import UsageTracker
from slay2agent.memory.oracle import oracle_version, read_oracle
from slay2agent.memory.skill_registry import SkillRegistry

logger = logging.getLogger(__name__)

# Default configuration for the demo loop.
_DEFAULT_WINDOW_SIZE = 12
_DEFAULT_REPEAT_THRESHOLD = 6
_AGENT_ROLE: AgentRole = "main"

# System prompt preamble — injected every LLM call.
_SYSTEM_PREAMBLE = """You are an expert player of Slay the Spire 2.
The character and ascension level have already been selected for you. Your job is to play the run until game_over or until you are told to stop.

Rules:
- CRITICAL: Call exactly ONE tool per response. Never call multiple tools at once. Never reply with plain text only.
- Use menu_select to navigate any remaining menu screens (e.g. ascension selection).
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
    """Assemble the full system prompt from preamble + memory injections."""
    parts = [_SYSTEM_PREAMBLE.strip()]

    if oracle_content:
        parts.append("\n## Oracle (global strategy)\n" + oracle_content)

    if skill_metadata_list:
        parts.append("\n## Available Skills\n" + "\n".join(skill_metadata_list))
    else:
        parts.append("\n## Available Skills\n(none yet)")

    return "\n".join(parts)


from slay2agent.game.action_schemas import dispatch


def _navigate_to_run_start(client: GameClient, character: str) -> None:
    """Hard-navigate from main menu → character confirmed → ready for ascension.

    This runs before the agent loop so the LLM never has to decide which
    character to pick.  The sequence is:
        main menu → singleplayer → standard → select <character>
        → confirm → embark
    Each step calls dispatch which blocks until the game state settles.
    If the game is not at the main menu (state_type != 'menu'), navigation is
    skipped and the agent loop takes over from wherever the game currently is.
    """
    current = client.get_state()
    if current.get("state_type") != "menu":
        logger.info(
            "pre-loop nav: current state_type=%r — skipping character navigation",
            current.get("state_type"),
        )
        return

    steps = [
        ("menu_select", {"option": "singleplayer"}),
        ("menu_select", {"option": "standard"}),
        ("menu_select", {"option": character}),
        ("menu_select", {"option": "embark"}),
    ]
    for action, args in steps:
        logger.info("pre-loop nav: %s %s", action, args)
        dispatch(client, action, args)


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

    # F-008a: initialise memory layer.
    skill_registry = SkillRegistry(cfg.memory.skills_dir)
    oracle_path = cfg.memory.oracle_path

    logger.info("starting run %s  character=%s asc=%d", run_id, run_cfg.character, run_cfg.ascension)

    termination_reason: TerminationReason = "error"
    extra_summary: dict[str, Any] = {}

    with GameClient(cfg.game.base_url, timeout=cfg.game.timeout) as client:
        bridge = ToolBridge(
            client=client,
            loop_detector=loop_detector,
            skill_registry=skill_registry,
        )

        # Hard-navigate to run start before handing control to the agent.
        _navigate_to_run_start(client, run_cfg.character)

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

                # Extract is_play_phase for combat states so the gate can block
                # play_card / end_turn during enemy turn.
                is_play_phase = (
                    parsed.view.is_play_phase
                    if isinstance(parsed.view, CombatView)
                    else True
                )

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
                    # Reset loop detector so actions from one screen don't
                    # pollute the window for the next.
                    loop_detector.reset()
                    # F-008b: skill_creator would fire here (stub in F-005).

                prev_state_type = state_type

                # ── F-008a: reload + read memory layer ──────────────────────
                # Reload after L0 clear (state_type transition) so that skills
                # written by a future skill_creator sub-agent (F-008b) are
                # picked up immediately on the next screen.
                if l0_cleared:
                    skill_registry.reload()

                skill_meta_lines = skill_registry.metadata_lines()
                oracle_content = read_oracle(oracle_path)
                oracle_ver = oracle_version(oracle_path)

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
                        skill_metadata_ids=[m.skill_id for m in skill_registry.list_skills()],
                        oracle_version=oracle_ver,
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
                    skill_metadata_list=skill_meta_lines,
                    oracle_content=oracle_content,
                )
                system_msg = Message(role="system", content=system_content)

                # On the very first step, append an ascension hint so the agent
                # selects the configured difficulty level.
                user_content = compact
                if step == 0 and run_cfg.ascension >= 0:
                    user_content += f"\n\n(Hint: please select Ascension {run_cfg.ascension}.)"
                user_msg = Message(role="user", content=user_content)

                full_messages = [system_msg] + l0 + [user_msg]
                tools = bridge.visible_tools(state_type, is_play_phase=is_play_phase)

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

                    # Warn if the LLM returned multiple tool_calls — we only
                    # execute the first one.  We must still append stub tool
                    # responses for ALL tool_call_ids to keep the OpenAI
                    # message format valid (an assistant message with
                    # tool_calls must be followed by one tool message per id).
                    extra_calls = assistant_msg.tool_calls[1:]
                    if extra_calls:
                        logger.warning(
                            "LLM returned %d extra tool_calls at step %d — "
                            "only the first (%r) will be executed",
                            len(extra_calls), step, action_name,
                        )

                    try:
                        result_raw = bridge.execute(state_type, action_name, action_args, is_play_phase=is_play_phase)
                        result_parsed = parse(result_raw)
                        tool_result_state_type = result_parsed.state_type
                        settled_summary = to_compact_prompt(result_parsed)

                        # Append to L0: assistant message + tool results for
                        # every tool_call_id (extras get a stub notice).
                        l0.append(assistant_msg)
                        l0.append(Message(
                            role="tool",
                            content=settled_summary,
                            tool_call_id=tool_call.id,
                        ))
                        for extra in extra_calls:
                            l0.append(Message(
                                role="tool",
                                content="(skipped — only one tool call is executed per step)",
                                tool_call_id=extra.id,
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
                            skill_metadata_ids=[m.skill_id for m in skill_registry.list_skills()],
                            oracle_version=oracle_ver,
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
                        for extra in extra_calls:
                            l0.append(Message(
                                role="tool",
                                content="(skipped — only one tool call is executed per step)",
                                tool_call_id=extra.id,
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
                        for extra in extra_calls:
                            l0.append(Message(
                                role="tool",
                                content="(skipped — only one tool call is executed per step)",
                                tool_call_id=extra.id,
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
                    skill_metadata_ids=[m.skill_id for m in skill_registry.list_skills()],
                    oracle_version=oracle_ver,
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
