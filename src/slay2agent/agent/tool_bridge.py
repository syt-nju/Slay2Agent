"""Tool bridge: action gate + loop detector (F-006).

The bridge sits between the main agent and the game client.
It handles two concerns:

1. **Gate** — only expose LLM tool schemas applicable to the current
   ``state_type``. Memory tools (``list_skills`` / ``read_skill``) are always
   visible regardless of state_type.

2. **Loop detector** — if the same ``(action, args_key)`` pair appears
   ``repeat_threshold`` or more times within the last ``window_size`` steps,
   the run is terminated.

No pre-execute parameter validation is done here; STS2MCP errors surface
as ``ActionError`` from the game client.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from slay2agent.game.action_schemas import (
    actions_for_state,
    dispatch,
    to_tool_schema,
)
from slay2agent.game.client import GameClient
from slay2agent.llm.protocol import ToolSchema
from slay2agent.memory.skill_registry import SkillRegistry

logger = logging.getLogger(__name__)


class LoopDetected(Exception):
    """Raised when the loop detector triggers."""

    def __init__(self, action: str, args: dict[str, Any], count: int, window: int) -> None:
        self.action = action
        self.args = args
        self.count = count
        self.window = window
        super().__init__(
            f"loop detected: ({action!r}, {args}) repeated {count}x in last {window} steps"
        )


def _args_key(args: dict[str, Any] | None) -> str:
    """Stable, hashable representation of action args for loop detection."""
    return json.dumps(args or {}, sort_keys=True)


@dataclass
class LoopDetector:
    """Sliding-window duplicate detector.

    Records the ``(action, args_key)`` pair for each step. If the exact same
    pair appears ``repeat_threshold`` or more times within the last
    ``window_size`` entries, ``check_and_record`` raises ``LoopDetected``.

    Using the full pair (not just action name) avoids false positives when the
    agent legitimately plays many different cards in sequence — only a truly
    stuck agent that keeps repeating the *exact same* action with the *exact
    same* arguments will trigger.

    Call ``reset()`` on state-type transitions so that actions from one screen
    do not pollute the window for the next.
    """

    window_size: int = 10
    repeat_threshold: int = 4
    _history: list[tuple[str, str]] = field(default_factory=list)

    def reset(self) -> None:
        """Clear history (call on state-type transitions)."""
        self._history.clear()

    def check_and_record(self, action: str, args: dict[str, Any] | None = None) -> None:
        """Record this step and raise ``LoopDetected`` if threshold is met."""
        key = _args_key(args)
        pair = (action, key)

        self._history.append(pair)
        window = self._history[-self.window_size :]
        count = sum(1 for p in window if p == pair)

        if count >= self.repeat_threshold:
            logger.error(
                "loop_detector: %r %s repeated %d times in last %d steps — terminating run",
                action,
                args,
                count,
                self.window_size,
            )
            raise LoopDetected(action, args or {}, count, self.window_size)


MEMORY_TOOL_NAMES = {"list_skills", "read_skill"}

# Actions that require it to be the player's turn in combat.
# When is_play_phase=False these are excluded from visible_tools and
# rejected by the gate so the LLM physically cannot call them.
_COMBAT_PLAYER_ONLY = {"play_card", "end_turn", "use_potion", "discard_potion"}


@dataclass
class ToolBridge:
    """Combines gate + loop detector into a single surface for the agent loop.

    Usage::

        bridge = ToolBridge(client, loop_detector, skill_registry)

        # Before calling the LLM:
        tools = bridge.visible_tools(state_type, is_play_phase=True)

        # After the LLM returns a tool call:
        new_state = bridge.execute(state_type, action_name, args, is_play_phase=True)
    """

    client: GameClient
    loop_detector: LoopDetector
    skill_registry: SkillRegistry | None = None

    def visible_tools(
        self, state_type: str, *, is_play_phase: bool = True
    ) -> list[ToolSchema]:
        """Tool schemas visible to the LLM for ``state_type``.

        When ``is_play_phase=False`` (enemy turn), combat-player-only actions
        (play_card, end_turn, use_potion) are excluded so the LLM cannot call them.
        Memory tools are always visible regardless of phase.
        """
        game_tools = [
            to_tool_schema(a)
            for a in actions_for_state(state_type)
            if is_play_phase or a.name not in _COMBAT_PLAYER_ONLY
        ]
        memory_tools = _memory_tool_schemas()
        return game_tools + memory_tools

    def execute(
        self,
        state_type: str,
        action: str,
        args: dict[str, Any] | None = None,
        *,
        is_play_phase: bool = True,
    ) -> dict[str, Any]:
        """Validate gate membership, check loop, dispatch, return settled state.

        Raises:
            ValueError: if ``action`` is not in the allowed set for ``state_type``
                (and is not a memory tool), or if ``action`` is a player-only
                combat action called during enemy turn (``is_play_phase=False``).
            LoopDetected: if the loop detector threshold is met.
            ActionError: if STS2MCP rejects the action.
        """
        allowed_game = {a.name for a in actions_for_state(state_type)}
        allowed_memory = {s.name for s in _memory_tool_schemas()}
        allowed = allowed_game | allowed_memory

        if action not in allowed:
            logger.error(
                "gate rejected action %r for state_type %r (allowed: %s)",
                action,
                state_type,
                sorted(allowed),
            )
            raise ValueError(
                f"action {action!r} is not allowed in state {state_type!r}"
            )

        # Enforce play-phase gate for combat player-only actions.
        if not is_play_phase and action in _COMBAT_PLAYER_ONLY:
            logger.error(
                "gate rejected %r: is_play_phase=False (enemy turn)", action
            )
            raise ValueError(
                f"action {action!r} is not allowed during enemy turn (is_play_phase=False)"
            )

        # Memory tools are handled in-process via skill registry.
        if action in allowed_memory:
            return self._handle_memory_tool(action, args or {})

        # Game action: loop-check then dispatch.
        self.loop_detector.check_and_record(action, args)
        return dispatch(self.client, action, args)

    # ── Memory tool handlers ────────────────────────────────────────────────

    def _handle_memory_tool(self, action: str, args: dict[str, Any]) -> dict[str, Any]:
        """Route memory tool calls to the skill registry."""
        registry = self.skill_registry

        if action == "list_skills":
            if registry is None:
                logger.debug("list_skills called (no registry configured — empty)")
                return {"skills": []}
            return registry.list_skills_response()

        if action == "read_skill":
            skill_id = args.get("skill_id", "")
            if registry is None:
                logger.debug("read_skill(%r) called (no registry configured)", skill_id)
                return {"skill_id": skill_id, "body": "(skill library not configured)"}
            return registry.read_skill_response(skill_id)

        raise ValueError(f"unknown memory tool: {action!r}")


# ── Memory tool schemas ─────────────────────────────────────────────────────

_LIST_SKILLS_SCHEMA = ToolSchema(
    name="list_skills",
    description=(
        "List all available skills with their metadata (skill_id, name, "
        "description). Each description encodes both what the skill covers "
        "and when to read it — use it to decide whether to load the full "
        "skill body via read_skill."
    ),
    parameters={"type": "object", "properties": {}, "additionalProperties": False},
)

_READ_SKILL_SCHEMA = ToolSchema(
    name="read_skill",
    description="Read the full markdown body of a skill by its skill_id.",
    parameters={
        "type": "object",
        "properties": {
            "skill_id": {
                "type": "string",
                "description": "The skill identifier (from list_skills).",
            }
        },
        "required": ["skill_id"],
        "additionalProperties": False,
    },
)


def _memory_tool_schemas() -> list[ToolSchema]:
    return [_LIST_SKILLS_SCHEMA, _READ_SKILL_SCHEMA]
