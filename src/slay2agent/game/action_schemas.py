"""Declarative table of every STS2MCP singleplayer action.

Single source of truth for action metadata. Provides:

* ``ACTION_SCHEMAS`` — the table itself, keyed by STS2MCP action name.
* ``dispatch(client, name, args)`` — generic runner. Unknown name -> ``KeyError``.
* ``actions_for_state(state_type)`` — gate input for the F-006 tool bridge.
* ``to_tool_schema(action)`` — adapter into the canonical ``ToolSchema`` so the
  LLM sees the same metadata that gates the dispatch.

Source spec: ``vendor/sts2mcp-docs/raw-simplified.md`` (mod release 0.4.0).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from slay2agent.game.client import GameClient
from slay2agent.llm.protocol import ToolSchema

ParamType = Literal["int", "str"]

_JSON_TYPE: dict[ParamType, str] = {"int": "integer", "str": "string"}


@dataclass(frozen=True)
class ParamSpec:
    type: ParamType
    description: str
    required: bool = True
    enum: tuple[str, ...] | None = None


@dataclass(frozen=True)
class ActionSchema:
    name: str
    description: str
    params: dict[str, ParamSpec] = field(default_factory=dict)
    applicable_state_types: frozenset[str] = field(default_factory=frozenset)


# Convenience: states where potions are accessible per STS2MCP docs.
_POTION_STATES = frozenset(
    {
        "monster",
        "elite",
        "boss",
        "map",
        "event",
        "rewards",
        "card_reward",
        "rest_site",
        "treasure",
        "shop",
        "fake_merchant",
    }
)
_PROCEED_STATES = frozenset(
    {"rewards", "rest_site", "shop", "fake_merchant", "treasure"}
)
_COMBAT_STATES = frozenset({"monster", "elite", "boss"})


ACTION_SCHEMAS: dict[str, ActionSchema] = {
    "menu_select": ActionSchema(
        name="menu_select",
        description=(
            "Click a menu option by its advertised label. Options are "
            "case-insensitive. Used in main menu, submenus, character select, "
            "blocking popups, and to leave game_over via 'main_menu'. "
            "Optional 'seed' is only accepted in contexts that allow it."
        ),
        params={
            "option": ParamSpec("str", "Label exposed by the current menu state."),
            "seed": ParamSpec(
                "str",
                "Optional run seed; rejected outside contexts that accept it.",
                required=False,
            ),
        },
        applicable_state_types=frozenset({"menu", "game_over"}),
    ),
    "play_card": ActionSchema(
        name="play_card",
        description=(
            "Play a card from hand by zero-based index. Single-target cards "
            "require 'target' as an enemy entity_id (e.g. 'JAW_WORM_0')."
        ),
        params={
            "card_index": ParamSpec("int", "Zero-based index into hand."),
            "target": ParamSpec(
                "str",
                "Enemy entity_id for single-target cards; omit otherwise.",
                required=False,
            ),
        },
        applicable_state_types=_COMBAT_STATES,
    ),
    "use_potion": ActionSchema(
        name="use_potion",
        description=(
            "Drink the potion in the given belt slot. 'target' is required for "
            "enemy-targeting potions. Works in any state where potions are "
            "accessible (combat, map, events, etc.)."
        ),
        params={
            "slot": ParamSpec("int", "Zero-based potion belt slot."),
            "target": ParamSpec(
                "str",
                "Enemy entity_id for enemy-targeting potions; omit otherwise.",
                required=False,
            ),
        },
        applicable_state_types=_POTION_STATES,
    ),
    "discard_potion": ActionSchema(
        name="discard_potion",
        description=(
            "Discard a potion to free up its slot. Use when the belt is full "
            "and you want room for incoming potions."
        ),
        params={"slot": ParamSpec("int", "Zero-based potion belt slot.")},
        applicable_state_types=_POTION_STATES,
    ),
    "end_turn": ActionSchema(
        name="end_turn",
        description="End the player's turn.",
        applicable_state_types=_COMBAT_STATES,
    ),
    "combat_select_card": ActionSchema(
        name="combat_select_card",
        description=(
            "Toggle a card in the in-combat selection prompt "
            "(exhaust / discard / upgrade / etc.)."
        ),
        params={"card_index": ParamSpec("int", "Zero-based index into the prompt list.")},
        applicable_state_types=frozenset({"hand_select"}),
    ),
    "combat_confirm_selection": ActionSchema(
        name="combat_confirm_selection",
        description="Confirm the in-combat hand-select choice.",
        applicable_state_types=frozenset({"hand_select"}),
    ),
    "claim_reward": ActionSchema(
        name="claim_reward",
        description=(
            "Claim a post-combat / event reward by index. Card rewards open "
            "the card_reward screen; other rewards apply immediately."
        ),
        params={"index": ParamSpec("int", "Zero-based index into rewards.items.")},
        applicable_state_types=frozenset({"rewards"}),
    ),
    "proceed": ActionSchema(
        name="proceed",
        description=(
            "Leave the current screen (rewards / rest_site / shop / "
            "fake_merchant / treasure)."
        ),
        applicable_state_types=_PROCEED_STATES,
    ),
    "select_card_reward": ActionSchema(
        name="select_card_reward",
        description="Pick a card to add to the deck from the card reward screen.",
        params={"card_index": ParamSpec("int", "Zero-based index into the offered cards.")},
        applicable_state_types=frozenset({"card_reward"}),
    ),
    "skip_card_reward": ActionSchema(
        name="skip_card_reward",
        description="Skip the card reward when allowed.",
        applicable_state_types=frozenset({"card_reward"}),
    ),
    "choose_map_node": ActionSchema(
        name="choose_map_node",
        description="Travel to a node from the map's current next_options.",
        params={
            "index": ParamSpec("int", "Zero-based index into map.next_options."),
        },
        applicable_state_types=frozenset({"map"}),
    ),
    "choose_event_option": ActionSchema(
        name="choose_event_option",
        description=(
            "Pick an event option by index. Locked options return an error. "
            "Also used for 'Proceed' style options in events."
        ),
        params={"index": ParamSpec("int", "Zero-based index into event.options.")},
        applicable_state_types=frozenset({"event"}),
    ),
    "advance_dialogue": ActionSchema(
        name="advance_dialogue",
        description="Click through Ancient dialogue until in_dialogue becomes false.",
        applicable_state_types=frozenset({"event"}),
    ),
    "choose_rest_option": ActionSchema(
        name="choose_rest_option",
        description="Choose a rest, smith, or other rest-site option.",
        params={
            "index": ParamSpec("int", "Zero-based index into rest_site.options."),
        },
        applicable_state_types=frozenset({"rest_site"}),
    ),
    "shop_purchase": ActionSchema(
        name="shop_purchase",
        description="Buy an item by index. Must be stocked and affordable.",
        params={"index": ParamSpec("int", "Zero-based index into the shop inventory.")},
        applicable_state_types=frozenset({"shop", "fake_merchant"}),
    ),
    "claim_treasure_relic": ActionSchema(
        name="claim_treasure_relic",
        description="Claim a relic from the opened treasure chest.",
        params={"index": ParamSpec("int", "Zero-based index into treasure.relics.")},
        applicable_state_types=frozenset({"treasure"}),
    ),
    "select_card": ActionSchema(
        name="select_card",
        description=(
            "Card-select overlay: toggle in grid screens, or pick immediately "
            "in choose-a-card screens."
        ),
        params={"index": ParamSpec("int", "Zero-based index into card_select.cards.")},
        applicable_state_types=frozenset({"card_select"}),
    ),
    "confirm_selection": ActionSchema(
        name="confirm_selection",
        description=(
            "Confirm the previewed grid card-select. Not used by choose-a-card."
        ),
        applicable_state_types=frozenset({"card_select"}),
    ),
    "cancel_selection": ActionSchema(
        name="cancel_selection",
        description=(
            "Cancel preview, skip a choose-a-card screen, or close the overlay."
        ),
        applicable_state_types=frozenset({"card_select"}),
    ),
    "select_bundle": ActionSchema(
        name="select_bundle",
        description="Open a bundle preview in the bundle-select overlay.",
        params={"index": ParamSpec("int", "Zero-based index into bundle_select.bundles.")},
        applicable_state_types=frozenset({"bundle_select"}),
    ),
    "confirm_bundle_selection": ActionSchema(
        name="confirm_bundle_selection",
        description="Confirm the previewed bundle.",
        applicable_state_types=frozenset({"bundle_select"}),
    ),
    "cancel_bundle_selection": ActionSchema(
        name="cancel_bundle_selection",
        description="Cancel the bundle preview.",
        applicable_state_types=frozenset({"bundle_select"}),
    ),
    "select_relic": ActionSchema(
        name="select_relic",
        description="Pick a relic from the relic-select overlay (immediate effect).",
        params={"index": ParamSpec("int", "Zero-based index into relic_select.relics.")},
        applicable_state_types=frozenset({"relic_select"}),
    ),
    "skip_relic_selection": ActionSchema(
        name="skip_relic_selection",
        description="Skip the relic choice when allowed.",
        applicable_state_types=frozenset({"relic_select"}),
    ),
    "crystal_sphere_set_tool": ActionSchema(
        name="crystal_sphere_set_tool",
        description="Switch the divination tool in the Crystal Sphere minigame.",
        params={
            "tool": ParamSpec(
                "str",
                "Divination tool to switch to.",
                enum=("big", "small"),
            ),
        },
        applicable_state_types=frozenset({"crystal_sphere"}),
    ),
    "crystal_sphere_click_cell": ActionSchema(
        name="crystal_sphere_click_cell",
        description="Reveal a cell at (x, y) in the Crystal Sphere grid.",
        params={
            "x": ParamSpec("int", "Zero-based grid column."),
            "y": ParamSpec("int", "Zero-based grid row."),
        },
        applicable_state_types=frozenset({"crystal_sphere"}),
    ),
    "crystal_sphere_proceed": ActionSchema(
        name="crystal_sphere_proceed",
        description="Finish the Crystal Sphere minigame.",
        applicable_state_types=frozenset({"crystal_sphere"}),
    ),
}


def dispatch(client: GameClient, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run an action by name and return the post-settle game state.

    ``args`` may contain ``None`` values for declared optional params; the
    underlying client drops them so STS2MCP only sees actual values.
    """
    if name not in ACTION_SCHEMAS:
        raise KeyError(f"unknown STS2MCP action: {name!r}")
    return client.post_action_and_settle(name, **(args or {}))


def actions_for_state(state_type: str) -> list[ActionSchema]:
    """Subset of action schemas applicable to ``state_type``.

    Order is the table's insertion order so the LLM sees a stable list.
    """
    return [s for s in ACTION_SCHEMAS.values() if state_type in s.applicable_state_types]


def to_tool_schema(action: ActionSchema) -> ToolSchema:
    """Render an ``ActionSchema`` into the canonical LLM ``ToolSchema``."""
    properties: dict[str, dict[str, Any]] = {}
    required: list[str] = []
    for pname, spec in action.params.items():
        prop: dict[str, Any] = {
            "type": _JSON_TYPE[spec.type],
            "description": spec.description,
        }
        if spec.enum is not None:
            prop["enum"] = list(spec.enum)
        properties[pname] = prop
        if spec.required:
            required.append(pname)

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        parameters["required"] = required

    return ToolSchema(
        name=action.name,
        description=action.description,
        parameters=parameters,
    )
