"""Tests for the demo loop's enemy-turn poll-wait gate.

The main loop must NOT prompt the LLM during the combat enemy turn (no legal
player action exists then — every combat action is player-only and gated out).
``_is_enemy_turn`` is the predicate driving that wait branch.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from slay2agent.agent.loop import RunConfig, _is_enemy_turn
from slay2agent.game.schema import parse

REAL_DIR = Path(__file__).parent / "fixtures" / "real"


def _load(name: str) -> dict[str, Any]:
    with (REAL_DIR / name).open("r", encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.parametrize(
    "fixture, expected",
    [
        ("state_elite.json", True),       # combat, is_play_phase=false → enemy turn
        ("state_monster.json", False),    # combat, is_play_phase=true → player turn
        ("state_map.json", False),        # non-combat
        ("state_menu_main.json", False),  # non-combat
        ("state_game_over.json", False),  # non-combat
    ],
)
def test_is_enemy_turn(fixture: str, expected: bool) -> None:
    assert _is_enemy_turn(parse(_load(fixture))) is expected


def test_run_config_enemy_turn_defaults() -> None:
    cfg = RunConfig()
    assert cfg.enemy_turn_poll_interval > 0
    assert cfg.enemy_turn_wait_limit > 0
