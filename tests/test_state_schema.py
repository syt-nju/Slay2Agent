"""F-004 state parser + compact prompt tests.

Drives parsing through every real fixture under ``tests/fixtures/real/`` so a
schema regression in any known ``state_type`` shows up loudly. Also pins the
unknown-state fallback behaviour required by the acceptance criteria.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from slay2agent.game.schema import (
    CardRewardView,
    CardSelectView,
    CombatView,
    EventView,
    GameOverView,
    HandSelectView,
    MapView,
    MenuView,
    ParsedState,
    RewardsView,
    UnknownView,
    parse,
    to_compact_prompt,
)

REAL_DIR = Path(__file__).parent / "fixtures" / "real"


def _load(name: str) -> dict[str, Any]:
    with (REAL_DIR / name).open("r", encoding="utf-8") as f:
        return json.load(f)


# ── parse() — fixture-driven ────────────────────────────────────────────


@pytest.mark.parametrize(
    "fixture, expected_state_type, expected_view",
    [
        ("state_menu_main.json", "menu", MenuView),
        ("state_menu_singleplayer.json", "menu", MenuView),
        ("state_menu_tutorial_prompt.json", "menu", MenuView),
        ("state_menu_character_select.json", "menu", MenuView),
        ("state_monster.json", "monster", CombatView),
        ("state_elite.json", "elite", CombatView),
        ("state_hand_select_simple_select.json", "hand_select", HandSelectView),
        ("state_map.json", "map", MapView),
        ("state_event.json", "event", EventView),
        ("state_rewards.json", "rewards", RewardsView),
        ("state_card_reward.json", "card_reward", CardRewardView),
        ("state_card_select_default.json", "card_select", CardSelectView),
        ("state_game_over.json", "game_over", GameOverView),
        ("state_unknown.json", "unknown", UnknownView),
    ],
)
def test_parse_dispatches_view_per_state_type(
    fixture: str, expected_state_type: str, expected_view: type
) -> None:
    parsed = parse(_load(fixture))
    assert parsed.state_type == expected_state_type
    assert isinstance(parsed.view, expected_view), (
        f"{fixture}: got {type(parsed.view).__name__}, want {expected_view.__name__}"
    )


def test_parse_menu_main_keeps_string_options() -> None:
    parsed = parse(_load("state_menu_main.json"))
    assert isinstance(parsed.view, MenuView)
    assert parsed.view.menu_screen == "main"
    names = [o.name for o in parsed.view.options]
    assert names == ["singleplayer", "multiplayer", "settings", "quit"]
    assert all(o.enabled for o in parsed.view.options)
    assert parsed.run is None
    assert parsed.player is None


def test_parse_menu_character_select_normalizes_options_and_lifts_characters() -> None:
    parsed = parse(_load("state_menu_character_select.json"))
    assert isinstance(parsed.view, MenuView)
    assert parsed.view.menu_screen == "character_select"

    # Mixed-shape option dicts get normalized to MenuOption(name, enabled).
    enabled = {o.name: o.enabled for o in parsed.view.options}
    assert enabled["IRONCLAD"] is True
    assert enabled["SILENT"] is False
    assert enabled["confirm"] is True

    ironclad = next(c for c in parsed.view.characters if c.id == "IRONCLAD")
    assert ironclad.locked is False
    assert ironclad.starting_relic == "Burning Blood"
    silent = next(c for c in parsed.view.characters if c.id == "SILENT")
    assert silent.locked is True


def test_parse_monster_lifts_combat_player_and_hand() -> None:
    parsed = parse(_load("state_monster.json"))
    assert parsed.state_type == "monster"
    assert isinstance(parsed.view, CombatView)
    assert parsed.run is not None and parsed.run.floor == 1

    p = parsed.player
    assert p is not None
    assert p.character == "The Ironclad"
    assert p.energy == 3 and p.max_energy == 3
    assert p.hand is not None and len(p.hand) == 5
    bash = next(c for c in p.hand if c.name == "Bash")
    assert bash.target_type == "AnyEnemy"
    assert bash.can_play is True
    assert p.draw_pile_count == 5

    enemies = parsed.view.enemies
    assert len(enemies) == 1
    nibbit = enemies[0]
    assert nibbit.entity_id == "NIBBIT_0"
    assert nibbit.intents and nibbit.intents[0].type == "Attack"


def test_parse_elite_carries_status_on_enemy() -> None:
    parsed = parse(_load("state_elite.json"))
    assert isinstance(parsed.view, CombatView)
    byrdonis = parsed.view.enemies[0]
    assert byrdonis.name == "Byrdonis"
    assert byrdonis.status and byrdonis.status[0].name == "Territorial"


def test_parse_hand_select_carries_unplayable_reason() -> None:
    parsed = parse(_load("state_hand_select_simple_select.json"))
    assert isinstance(parsed.view, HandSelectView)
    assert parsed.view.mode == "simple_select"
    assert parsed.view.cards
    # Player-side hand also carries unplayable reasons (energy 0 in this fixture).
    assert parsed.player is not None and parsed.player.hand is not None
    unplayables = [c for c in parsed.player.hand if c.can_play is False]
    assert unplayables and unplayables[0].unplayable_reason == "EnergyCostTooHigh"


def test_parse_map_only_surfaces_next_options() -> None:
    parsed = parse(_load("state_map.json"))
    assert isinstance(parsed.view, MapView)
    assert parsed.view.next_options
    assert parsed.view.next_options[0].leads_to
    assert parsed.view.boss_pos == (3, 16)
    assert parsed.view.total_nodes > parsed.view.visited_count


def test_parse_event_keeps_options_and_dialogue_flag() -> None:
    parsed = parse(_load("state_event.json"))
    assert isinstance(parsed.view, EventView)
    assert parsed.view.event_id == "NEOW"
    assert parsed.view.is_ancient is True
    assert len(parsed.view.options) == 3
    assert parsed.view.options[0].title == "Lead Paperweight"


def test_parse_rewards_lists_items() -> None:
    parsed = parse(_load("state_rewards.json"))
    assert isinstance(parsed.view, RewardsView)
    types = [it.type for it in parsed.view.items]
    assert "gold" in types and "card" in types
    assert parsed.view.can_proceed is True


def test_parse_card_reward_items_have_rarity() -> None:
    parsed = parse(_load("state_card_reward.json"))
    assert isinstance(parsed.view, CardRewardView)
    assert parsed.view.can_skip is True
    rarities = {c.rarity for c in parsed.view.cards}
    assert rarities == {"Uncommon", "Common"}


def test_parse_card_select_keeps_screen_type() -> None:
    parsed = parse(_load("state_card_select_default.json"))
    assert isinstance(parsed.view, CardSelectView)
    assert parsed.view.screen_type == "choose"
    assert parsed.view.can_skip is True


def test_parse_game_over_exposes_main_menu_option() -> None:
    parsed = parse(_load("state_game_over.json"))
    assert isinstance(parsed.view, GameOverView)
    assert "main_menu" in parsed.view.options
    assert parsed.player is not None and parsed.player.hp == 0


# ── Unknown / fallback ──────────────────────────────────────────────────


def test_parse_unknown_state_type_falls_back_without_crashing() -> None:
    parsed = parse(_load("state_unknown.json"))
    assert isinstance(parsed.view, UnknownView)
    assert parsed.player is not None  # still lifts shared structure
    assert parsed.run is not None
    # Residual payload doesn't include keys already lifted to ParsedState.
    for k in {"state_type", "run", "player"}:
        assert k not in parsed.view.payload


def test_parse_completely_synthetic_unknown_state_type() -> None:
    fake: dict[str, Any] = {"state_type": "totally-made-up", "extra": [1, 2, 3]}
    parsed = parse(fake)
    assert parsed.state_type == "totally-made-up"
    assert isinstance(parsed.view, UnknownView)
    assert parsed.view.payload == {"extra": [1, 2, 3]}


def test_parse_missing_state_type_defaults_to_unknown() -> None:
    parsed = parse({})
    assert parsed.state_type == "unknown"
    assert isinstance(parsed.view, UnknownView)


# ── to_compact_prompt() ─────────────────────────────────────────────────


_FIXTURES = [
    "state_menu_main.json",
    "state_menu_singleplayer.json",
    "state_menu_tutorial_prompt.json",
    "state_menu_character_select.json",
    "state_monster.json",
    "state_elite.json",
    "state_hand_select_simple_select.json",
    "state_map.json",
    "state_event.json",
    "state_rewards.json",
    "state_card_reward.json",
    "state_card_select_default.json",
    "state_game_over.json",
    "state_unknown.json",
]


@pytest.mark.parametrize("fixture", _FIXTURES)
def test_compact_prompt_is_nonempty_and_bounded(fixture: str) -> None:
    parsed = parse(_load(fixture))
    text = to_compact_prompt(parsed)
    assert text and not text.isspace()
    # Token-bound by design: we never dump full piles or full nodes; map.json is
    # the worst-case fixture (huge raw map) and must still come out compact.
    assert len(text) < 4000, f"{fixture}: prompt {len(text)} chars too long"


def test_combat_prompt_includes_hp_energy_and_each_hand_card() -> None:
    parsed = parse(_load("state_monster.json"))
    text = to_compact_prompt(parsed)
    assert "HP 80/80" in text
    assert "Energy 3/3" in text
    assert "Bash" in text
    assert "NIBBIT_0" in text


def test_map_prompt_does_not_dump_full_node_graph() -> None:
    parsed = parse(_load("state_map.json"))
    text = to_compact_prompt(parsed)
    # The raw map carries dozens of nodes; compact view should only show the
    # current next_options. Cap on "node lines" is one per next_option.
    node_lines = [ln for ln in text.splitlines() if ln.lstrip().startswith("- [")]
    assert 1 <= len(node_lines) <= 8


def test_event_prompt_skips_already_chosen_options() -> None:
    fake: dict[str, Any] = {
        "state_type": "event",
        "event": {
            "event_id": "FAKE",
            "event_name": "Fake",
            "is_ancient": False,
            "in_dialogue": False,
            "body": None,
            "options": [
                {
                    "index": 0,
                    "title": "Stay",
                    "description": "Do nothing.",
                    "is_locked": False,
                    "is_proceed": False,
                    "was_chosen": True,
                },
                {
                    "index": 1,
                    "title": "Leave",
                    "description": "Walk away.",
                    "is_locked": False,
                    "is_proceed": True,
                    "was_chosen": False,
                },
            ],
        },
    }
    text = to_compact_prompt(parse(fake))
    assert "Stay" not in text
    assert "Leave" in text
    assert "[PROCEED]" in text


def test_unknown_state_prompt_lists_residual_keys() -> None:
    parsed = parse({"state_type": "weird", "foo": 1, "bar": 2})
    text = to_compact_prompt(parsed)
    assert "weird" in text
    assert "bar" in text and "foo" in text


def test_to_compact_prompt_accepts_any_parsed_state() -> None:
    """All 14 real fixtures produce a prompt without raising; a smoke check."""
    for name in _FIXTURES:
        parsed: ParsedState = parse(_load(name))
        to_compact_prompt(parsed)
