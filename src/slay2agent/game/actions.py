"""Typed Python wrappers for STS2MCP singleplayer actions.

Each wrapper:

* matches the action name and parameter names exposed by STS2MCP
  (https://github.com/Gennadiyev/STS2MCP/blob/main/docs/raw-simplified.md);
* has a docstring suitable as an LLM tool description (kept short, factual,
  no chain-of-thought);
* delegates POST + settle to ``GameClient.post_action_and_settle``;
* returns the freshest game state dict.

Allow-listing per ``state_type`` is the tool bridge's job, not this layer's.
"""

from __future__ import annotations

from typing import Any

from slay2agent.game.client import GameClient


def play_card(
    client: GameClient, card_index: int, target: str | None = None
) -> dict[str, Any]:
    """Play a card from hand by its index.

    Args:
        card_index: Zero-based index into the hand.
        target: Enemy ``entity_id`` (e.g. ``"JAW_WORM_0"``) for single-target
            cards. Omit for non-targeted cards.

    Available during ``state_type`` in ``{monster, elite, boss}``.
    """
    return client.post_action_and_settle(
        "play_card", card_index=card_index, target=target
    )


def end_turn(client: GameClient) -> dict[str, Any]:
    """End the player's turn.

    Available during ``state_type`` in ``{monster, elite, boss}``.
    """
    return client.post_action_and_settle("end_turn")


def use_potion(
    client: GameClient, slot: int, target: str | None = None
) -> dict[str, Any]:
    """Drink the potion in ``slot``.

    Args:
        slot: Zero-based potion belt slot.
        target: Enemy ``entity_id`` for enemy-targeting potions. Omit otherwise.

    Available whenever potions are accessible (combat, map, events).
    """
    return client.post_action_and_settle("use_potion", slot=slot, target=target)


def choose_map_node(client: GameClient, index: int) -> dict[str, Any]:
    """Travel to a node from the map's current ``next_options``.

    Args:
        index: Zero-based index into ``next_options`` exposed by the map state.

    Available during ``state_type == "map"``.
    """
    return client.post_action_and_settle("choose_map_node", index=index)


def choose_event_option(client: GameClient, index: int) -> dict[str, Any]:
    """Pick an event option by index.

    Args:
        index: Zero-based index of the option in the event state.

    Available during ``state_type == "event"``. Locked options return an error.
    """
    return client.post_action_and_settle("choose_event_option", index=index)


def claim_reward(client: GameClient, index: int) -> dict[str, Any]:
    """Claim a post-combat / event reward by index.

    Args:
        index: Zero-based index into the rewards list. Card rewards open
            the ``card_reward`` screen; other rewards apply immediately.

    Available during ``state_type == "rewards"``.
    """
    return client.post_action_and_settle("claim_reward", index=index)


def proceed(client: GameClient) -> dict[str, Any]:
    """Leave the current screen (rewards / shop / rest / treasure).

    Common ``proceed`` action shared across several state types.
    """
    return client.post_action_and_settle("proceed")


def select_card_reward(client: GameClient, card_index: int) -> dict[str, Any]:
    """Pick a card from the card reward screen.

    Args:
        card_index: Zero-based index into the offered cards.

    Available during ``state_type == "card_reward"``.
    """
    return client.post_action_and_settle("select_card_reward", card_index=card_index)


def menu_select(
    client: GameClient, option: str, seed: str | None = None
) -> dict[str, Any]:
    """Click a menu option by its advertised label.

    Args:
        option: Case-insensitive label exposed by the current menu state.
        seed: Optional seed for character-select; rejected outside contexts
            that accept it.

    Available during ``state_type == "menu"`` (also used to dismiss
    ``game_over`` via ``"main_menu"``).
    """
    return client.post_action_and_settle("menu_select", option=option, seed=seed)
