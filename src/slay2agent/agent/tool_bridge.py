"""Tool bridge: action gate + loop detector (F-006).

The bridge sits between the main agent and the game client.
It handles two concerns:

1. **Gate** — only expose LLM tool schemas applicable to the current
   ``state_type``. Memory tools (``list_skills`` / ``read_skill``) are always
   visible regardless of state_type (stubs until F-008a).

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

    Records ``(action, args_key)`` for each step. If any pair appears
    ``repeat_threshold`` or more times within the last ``window_size``
    entries, ``check_and_record`` raises ``LoopDetected``.
    """

    window_size: int = 10
    repeat_threshold: int = 4
    _history: list[tuple[str, str]] = field(default_factory=list)

    def check_and_record(self, action: str, args: dict[str, Any] | None = None) -> None:
        """Record this step and raise ``LoopDetected`` if threshold is met."""
        key = _args_key(args)
        pair = (action, key)

        # Record first so the current step is in the window when checking.
        self._history.append(pair)
        window = self._history[-self.window_size :]
        count = window.count(pair)

        if count >= self.repeat_threshold:
            logger.error(
                "loop_detector: %r repeated %d times in last %d steps — terminating run",
                action,
                count,
                self.window_size,
            )
            raise LoopDetected(action, args or {}, count, self.window_size)


@dataclass
class ToolBridge:
    """Combines gate + loop detector into a single surface for the agent loop.

    Usage::

        bridge = ToolBridge(client, loop_detector)

        # Before calling the LLM:
        tools = bridge.visible_tools(state_type)

        # After the LLM returns a tool call:
        new_state = bridge.execute(state_type, action_name, args)
    """

    client: GameClient
    loop_detector: LoopDetector

    def visible_tools(self, state_type: str) -> list[ToolSchema]:
        """Tool schemas visible to the LLM for ``state_type``.

        Combines gated game actions with always-visible memory stubs.
        Memory tools are no-ops until F-008a is implemented.
        """
        game_tools = [to_tool_schema(a) for a in actions_for_state(state_type)]
        memory_tools = _memory_tool_schemas()
        return game_tools + memory_tools

    def execute(
        self, state_type: str, action: str, args: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Validate gate membership, check loop, dispatch, return settled state.

        Raises:
            ValueError: if ``action`` is not in the allowed set for ``state_type``
                (and is not a memory tool).
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

        # Memory tools are handled in-process (stubs for now).
        if action in allowed_memory:
            return _handle_memory_tool(action, args or {})

        # Game action: loop-check then dispatch.
        self.loop_detector.check_and_record(action, args)
        return dispatch(self.client, action, args)


# ── Memory tool stubs (F-008a will replace these) ──────────────────────────


_LIST_SKILLS_SCHEMA = ToolSchema(
    name="list_skills",
    description=(
        "List all available skills with their metadata (id, description, "
        "when_to_read). Skills contain strategic guidance for specific "
        "game situations."
    ),
    parameters={"type": "object", "properties": {}, "additionalProperties": False},
)

_READ_SKILL_SCHEMA = ToolSchema(
    name="read_skill",
    description="Read the full body of a skill by its id.",
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


def _handle_memory_tool(action: str, args: dict[str, Any]) -> dict[str, Any]:
    """Stub handlers for memory tools — returns minimal plausible responses.

    F-008a will replace these with real skill registry reads.
    """
    if action == "list_skills":
        logger.debug("list_skills called (stub — skill library empty)")
        return {"skills": []}
    if action == "read_skill":
        skill_id = args.get("skill_id", "")
        logger.debug("read_skill(%r) called (stub — no skills yet)", skill_id)
        return {"skill_id": skill_id, "body": "(skill library not yet implemented)"}
    raise ValueError(f"unknown memory tool: {action!r}")
