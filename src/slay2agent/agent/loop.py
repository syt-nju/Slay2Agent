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

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from slay2agent.agent.compactor import run_l0_compaction
from slay2agent.agent.issue_logger import log_loop_issue, log_unknown_view_issue
from slay2agent.agent.oracle_updater import run_oracle_updater
from slay2agent.agent.skill_creator import run_skill_creator
from slay2agent.agent.skill_librarian import run_skill_librarian
from slay2agent.agent.tool_bridge import LoopDetected, LoopDetector, ToolBridge, MEMORY_TOOL_NAMES
from slay2agent.agent.trace import (
    StepRecord,
    TerminationReason,
    TraceWriter,
    new_run_id,
)
from slay2agent.config import Config
from slay2agent.game.client import ActionError, GameClient
from slay2agent.game.schema import CombatView, UnknownView, parse, to_compact_prompt
from slay2agent.llm.openai_compat import OpenAICompatibleAdapter
from slay2agent.llm.protocol import AgentRole, LLMAdapter, Message, ToolCall
from slay2agent.llm.retry import call_with_retry
from slay2agent.llm.usage import UsageTracker
from slay2agent.memory.oracle import oracle_version, read_oracle
from slay2agent.memory.skill_cache import SkillCache
from slay2agent.memory.skill_registry import SkillRegistry
from slay2agent.viewer.observer import NoOpObserver, RunObserver

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
- list_skills / read_skill give you strategic memory. Each skill listed below already includes its trigger condition inside the description — call read_skill only when the description matches the current situation.
- game_over means the run ended; you will be stopped automatically.

hand_select rules:
- combat_select_card(index) is a TOGGLE: if the card at that index is not yet selected it becomes selected (and disappears from the available list); if it is already in "Already selected", calling it again will DESELECT it.
- Always check the "Already selected" list before calling combat_select_card to avoid accidentally toggling a card off.
- can_confirm: True means the minimum required cards are selected and you MAY call combat_confirm_selection() to proceed. You can still select more cards first if the situation warrants it.
- When you are satisfied with your selection, call combat_confirm_selection() to execute. Do NOT keep calling combat_select_card on an already-selected card.
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


def _build_run_trace_summary(
    total_steps: int,
    termination_reason: str,
    tracker: UsageTracker,
) -> str:
    """Compact text summary of the completed run for oracle_updater context."""
    usage = tracker.role_totals()
    main_u = usage.get("main")
    sc_u = usage.get("skill_creator")
    return "\n".join([
        "Run summary (for oracle_updater context):",
        f"- Total agent steps: {total_steps}",
        f"- Termination reason: {termination_reason}",
        f"- Main agent tokens used: {(main_u.input_tokens + main_u.output_tokens) if main_u else 0}",
        f"- Skill creator tokens used: {(sc_u.input_tokens + sc_u.output_tokens) if sc_u else 0}",
    ])


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


async def run_demo_loop(
    cfg: Config,
    run_cfg: RunConfig,
    observer: RunObserver | None = None,
    tracker: UsageTracker | None = None,
) -> Path:
    """Run the Phase 1 demo loop.

    Returns the path to the run directory (``runs/<run_id>/``).

    The loop terminates on:
    - ``game_over`` state_type
    - ``LoopDetected`` from the loop detector
    - Unrecoverable error (re-raised after logging)

    All termination paths write ``summary.json`` before returning.
    """
    if observer is None:
        observer = NoOpObserver()
    if tracker is None:
        tracker = UsageTracker()
    run_id = new_run_id()
    run_dir = run_cfg.runs_dir / run_id
    trace = TraceWriter(run_dir)

    api_key = cfg.llm.require_api_key()
    adapter = OpenAICompatibleAdapter(
        cfg.llm.model, api_key, cfg.llm.base_url, timeout=cfg.llm.timeout
    )
    loop_detector = LoopDetector(
        window_size=run_cfg.window_size,
        repeat_threshold=run_cfg.repeat_threshold,
    )

    # F-008a: initialise memory layer with two-level LRU cache.
    skill_cache = SkillCache.load(cfg.memory.skill_cache_path)
    skill_registry = SkillRegistry(cfg.memory.skills_dir, skill_cache=skill_cache)
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
        prev_is_play_phase: bool | None = None
        step = 0
        initial_skill_ids = frozenset(s.skill_id for s in skill_registry.list_skills())
        seen_unknown_state_types: set[str] = set()
        _skill_creator_tasks: list[asyncio.Task[None]] = []

        try:
            while True:
                raw_state = client.get_state()
                parsed = parse(raw_state)
                state_type = parsed.state_type
                compact = to_compact_prompt(parsed)

                # F-011: log first encounter of any unrecognised state_type so
                # researchers can see which ones need a dedicated View.
                if isinstance(parsed.view, UnknownView) and state_type not in seen_unknown_state_types:
                    seen_unknown_state_types.add(state_type)
                    log_unknown_view_issue(
                        issues_path=cfg.memory.issues_path,
                        run_id=run_id,
                        step=step,
                        state_type=state_type,
                        payload_keys=list(parsed.view.payload.keys()),
                    )

                # Extract is_play_phase for combat states so the gate can block
                # play_card / end_turn during enemy turn.
                is_play_phase = (
                    parsed.view.is_play_phase
                    if isinstance(parsed.view, CombatView)
                    else True
                )

                # Reset loop detector on enemy→player phase transition so that
                # the agent correctly calling end_turn once per round doesn't
                # accumulate into a false-positive loop detection.
                if prev_is_play_phase is False and is_play_phase:
                    loop_detector.reset()
                    logger.debug("loop_detector: reset on player turn start (new round)")
                prev_is_play_phase = is_play_phase

                # ── L0 clear on state_type transition ───────────────────────
                l0_cleared = False
                if prev_state_type is not None and state_type != prev_state_type:
                    if l0:
                        prev_l0_segment = l0  # capture before clear
                        logger.info(
                            "state_type changed %s → %s — clearing L0 (%d messages)",
                            prev_state_type, state_type, len(l0),
                        )
                        l0 = []
                        l0_cleared = True
                        observer.on_memory_event("L0_cleared", f"{prev_state_type} → {state_type}")
                        # F-008b: run skill creator on the completed segment (background asyncio task)
                        _task = asyncio.create_task(
                            asyncio.to_thread(
                                run_skill_creator,
                                prev_l0_segment,
                                skill_registry,
                                oracle_path,
                                adapter,
                                tracker,
                                trace,
                                model=cfg.llm.model,
                                prev_state_type=prev_state_type,
                                new_state_type=state_type,
                                observer=observer,
                                extra_body=cfg.llm.subagent_extra_body,
                            ),
                            name=f"skill_creator_{prev_state_type}_{state_type}",
                        )
                        _skill_creator_tasks.append(_task)
                    # Reset loop detector so actions from one screen don't
                    # pollute the window for the next.
                    loop_detector.reset()

                prev_state_type = state_type

                # NOTE: skill_registry.reload() is intentionally NOT called on
                # state_type transitions to preserve KV cache hit rate (the
                # system prompt skill list stays stable within a run). Reload
                # happens once at run end (see finally block).

                skill_meta_lines = skill_registry.metadata_lines()
                oracle_content = read_oracle(oracle_path)
                oracle_ver = oracle_version(oracle_path)

                # ── Terminate on game_over before LLM call ───────────────────
                if state_type == "game_over":
                    logger.info("game_over reached — terminating run")
                    termination_reason = "game_over"
                    observer.on_step_start(step, state_type, compact, "")
                    observer.on_run_end("game_over", step)
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

                # ── F-012: L0 compaction ─────────────────────────────────────
                mem_cfg = cfg.memory
                if (
                    mem_cfg.l0_compact_enabled
                    and len(l0) > mem_cfg.l0_compact_threshold
                    and len(l0) > mem_cfg.l0_compact_keep
                ):
                    l0 = run_l0_compaction(
                        l0,
                        compact_keep=mem_cfg.l0_compact_keep,
                        adapter=adapter,
                        tracker=tracker,
                        trace=trace,
                        model=cfg.llm.model,
                        step=step,
                        state_type=state_type,
                        extra_body=cfg.llm.subagent_extra_body,
                    )
                    observer.on_memory_event(
                        "l0_compacted",
                        f"l0 now {len(l0)} msgs at step {step}",
                    )

                full_messages = [system_msg] + l0 + [user_msg]
                tools = bridge.visible_tools(state_type, is_play_phase=is_play_phase)

                # ── Observer: step start + context ────────────────────────────
                observer.on_step_start(step, state_type, user_content, f"skills={len(skill_meta_lines)} oracle_ver={oracle_ver}")
                observer.on_context_update(
                    oracle_content,
                    [{"id": s.skill_id, "name": s.name, "description": s.description}
                     for s in skill_registry.list_skills()],
                )

                # ── LLM call ─────────────────────────────────────────────────
                _main_extra_body = cfg.llm.extra_body
                resp = call_with_retry(
                    lambda: adapter.chat(full_messages, tools, tool_choice="required",
                                         extra_body=_main_extra_body)
                )
                tracker.record(_AGENT_ROLE, resp.model, resp.usage)

                assistant_msg = resp.message

                # ── Observer: LLM response ────────────────────────────────────
                _tc0 = assistant_msg.tool_calls[0] if assistant_msg.tool_calls else None
                observer.on_llm_response(
                    _tc0.name if _tc0 else None,
                    _tc0.arguments if _tc0 else None,
                    {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens},
                )

                # ── Dispatch tool call ────────────────────────────────────────
                tool_call: ToolCall | None = None
                tool_result_state_type: str | None = None
                settled_summary = compact  # fallback if no tool call
                loop_warning_raw_injected = False

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
                        current_hand = parsed.player.hand if parsed.player else None
                        result_raw, loop_warning = bridge.execute(
                            state_type, action_name, action_args,
                            is_play_phase=is_play_phase,
                            hand=current_hand,
                        )

                        if action_name in MEMORY_TOOL_NAMES:
                            import json as _json
                            settled_summary = _json.dumps(result_raw, ensure_ascii=False, indent=2)
                        else:
                            result_parsed = parse(result_raw)
                            tool_result_state_type = result_parsed.state_type
                            settled_summary = to_compact_prompt(result_parsed)

                        if loop_warning:
                            import json as _json
                            # Inject raw MCP state for self-diagnosis
                            raw_state_str = _json.dumps(result_raw, ensure_ascii=False, indent=2)
                            settled_summary = (
                                loop_warning
                                + "\n\n## Raw MCP State (for debugging)\n```json\n"
                                + raw_state_str
                                + "\n```\n\n"
                                + settled_summary
                            )
                            loop_warning_raw_injected = True
                            # Log issue for post-run analysis
                            log_loop_issue(
                                issues_path=cfg.memory.issues_path,
                                run_dir=run_dir,
                                step=step,
                                state_type=state_type,
                                repeated_action=action_name,
                                repeated_args=action_args,
                                repeat_count=loop_detector.last_warning_count,
                                compact_prompt_snippet=to_compact_prompt(result_parsed) if action_name not in MEMORY_TOOL_NAMES else "",
                            )

                        observer.on_tool_result(action_name, settled_summary[:200])

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
                        observer.on_run_end("loop_terminated", step)
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
                    loop_warning_raw_injected=loop_warning_raw_injected,
                ))

                step += 1

        except LoopDetected:
            # Already handled inside the loop.
            pass
        except Exception as exc:
            logger.error("unhandled error in run loop: %s", exc, exc_info=True)
            termination_reason = "error"
            extra_summary["error"] = str(exc)
            observer.on_run_end("error", step)

        finally:
            # Wait for any background skill_creator tasks to finish before
            # reloading the registry, so the reload picks up all their writes.
            if _skill_creator_tasks:
                logger.info("waiting for %d background skill_creator task(s)…", len(_skill_creator_tasks))
                await asyncio.gather(*_skill_creator_tasks, return_exceptions=True)

            # Reload skill registry at run end so next run picks up all changes
            # made by skill_creator during this run (kept stable mid-run for KV cache).
            skill_registry.reload()

            # F-008c: oracle_updater fires at run end (before writing summary)
            run_oracle_updater(
                run_trace_summary=_build_run_trace_summary(step, termination_reason, tracker),
                skill_registry=skill_registry,
                oracle_path=oracle_path,
                adapter=adapter,
                tracker=tracker,
                trace=trace,
                model=cfg.llm.model,
                termination_reason=termination_reason,
                oracle_max_tokens=cfg.memory.oracle_max_tokens,
                observer=observer,
                extra_body=cfg.llm.subagent_extra_body,
            )

            # Skill librarian: merge overlapping skills if new ones were created.
            current_skill_ids = frozenset(s.skill_id for s in skill_registry.list_skills())
            if current_skill_ids - initial_skill_ids:
                run_skill_librarian(
                    skill_registry=skill_registry,
                    adapter=adapter,
                    tracker=tracker,
                    trace=trace,
                    model=cfg.llm.model,
                    observer=observer,
                    extra_body=cfg.llm.subagent_extra_body,
                )

            # Snapshot post-run memory (oracle + skills) into the run dir so
            # each trace carries an immutable record of what the next run will
            # start from.  Done after oracle_updater so the snapshot reflects
            # the version-of-record.
            trace.write_agent_state_snapshot(cfg.memory.agent_state_dir)
            trace.write_summary(
                termination_reason=termination_reason,
                tracker=tracker,
                extra=extra_summary or None,
            )
            logger.info("run %s finished: %s", run_id, termination_reason)

    return run_dir
