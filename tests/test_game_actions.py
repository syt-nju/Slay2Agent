"""F-003 action layer tests.

Covers the declarative ``ACTION_SCHEMAS`` table, the generic ``dispatch``
runner, the F-006-bound ``actions_for_state`` gate input, and the canonical
``to_tool_schema`` adapter.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from slay2agent.game import (
    ACTION_SCHEMAS,
    GameClient,
    actions_for_state,
    dispatch,
    to_tool_schema,
)
from slay2agent.game.client import ActionError

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict[str, Any]:
    with (FIXTURE_DIR / name).open("r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def recorder():
    """Build a GameClient that records every request and replies with JSON."""

    def _build(
        get_state_payload: dict[str, Any] | None = None,
        action_response: dict[str, Any] | None = None,
    ):
        records: list[dict[str, Any]] = []
        get_payload = get_state_payload or _load("state_combat.json")
        post_payload = action_response or _load("action_ok.json")

        def handler(request: httpx.Request) -> httpx.Response:
            entry: dict[str, Any] = {
                "method": request.method,
                "path": request.url.path,
            }
            if request.content:
                entry["json"] = json.loads(request.content.decode("utf-8"))
            records.append(entry)
            if request.method == "POST":
                return httpx.Response(200, json=post_payload)
            return httpx.Response(200, json=get_payload)

        transport = httpx.MockTransport(handler)
        http_client = httpx.Client(base_url="http://test-mod", transport=transport)
        client = GameClient("http://test-mod", client=http_client)
        return client, records

    return _build


def _last_post(records: list[dict[str, Any]]) -> dict[str, Any]:
    posts = [r for r in records if r["method"] == "POST"]
    assert posts, "expected at least one POST request"
    return posts[-1]


# ── ACTION_SCHEMAS coverage ─────────────────────────────────────────────

# Pulled verbatim from vendor/sts2mcp-docs/raw-simplified.md singleplayer
# section. Update together with ACTION_SCHEMAS when STS2MCP adds actions.
EXPECTED_SP_ACTIONS = {
    "menu_select",
    "play_card",
    "use_potion",
    "discard_potion",
    "end_turn",
    "combat_select_card",
    "combat_confirm_selection",
    "claim_reward",
    "proceed",
    "select_card_reward",
    "skip_card_reward",
    "choose_map_node",
    "choose_event_option",
    "advance_dialogue",
    "choose_rest_option",
    "shop_purchase",
    "claim_treasure_relic",
    "select_card",
    "confirm_selection",
    "cancel_selection",
    "select_bundle",
    "confirm_bundle_selection",
    "cancel_bundle_selection",
    "select_relic",
    "skip_relic_selection",
    "crystal_sphere_set_tool",
    "crystal_sphere_click_cell",
    "crystal_sphere_proceed",
}


def test_action_schemas_cover_all_singleplayer_actions():
    assert set(ACTION_SCHEMAS) == EXPECTED_SP_ACTIONS


def test_every_action_has_at_least_one_applicable_state_type():
    missing = [
        name for name, s in ACTION_SCHEMAS.items() if not s.applicable_state_types
    ]
    assert missing == [], f"actions without state_type gate: {missing}"


def test_schema_self_consistency_action_name_matches_key():
    for name, schema in ACTION_SCHEMAS.items():
        assert schema.name == name


# ── dispatch ─────────────────────────────────────────────────────────────


def test_dispatch_play_card_with_target(recorder):
    client, records = recorder()
    with client:
        dispatch(client, "play_card", {"card_index": 2, "target": "JAW_WORM_0"})
    assert _last_post(records)["json"] == {
        "action": "play_card",
        "card_index": 2,
        "target": "JAW_WORM_0",
    }


def test_dispatch_drops_none_optional_params(recorder):
    client, records = recorder()
    with client:
        dispatch(client, "play_card", {"card_index": 0, "target": None})
    body = _last_post(records)["json"]
    assert body == {"action": "play_card", "card_index": 0}
    assert "target" not in body


def test_dispatch_no_args_action(recorder):
    client, records = recorder()
    with client:
        dispatch(client, "end_turn")
    assert _last_post(records)["json"] == {"action": "end_turn"}


def test_dispatch_returns_settled_state(recorder):
    client, records = recorder(get_state_payload=_load("state_rewards.json"))
    with client:
        result = dispatch(client, "claim_reward", {"index": 0})
    assert result["state_type"] == "rewards"
    assert _last_post(records)["json"] == {"action": "claim_reward", "index": 0}


def test_dispatch_unknown_action_raises_keyerror(recorder):
    client, _ = recorder()
    with client, pytest.raises(KeyError, match="not_a_real_action"):
        dispatch(client, "not_a_real_action", {})


def test_dispatch_propagates_action_error(recorder):
    client, _ = recorder(action_response=_load("action_error.json"))
    with client, pytest.raises(ActionError):
        dispatch(client, "play_card", {"card_index": 99})


@pytest.mark.parametrize(
    "name, args, expected_body",
    [
        (
            "menu_select",
            {"option": "ironclad", "seed": "ABC123"},
            {"action": "menu_select", "option": "ironclad", "seed": "ABC123"},
        ),
        (
            "menu_select",
            {"option": "back", "seed": None},
            {"action": "menu_select", "option": "back"},
        ),
        (
            "use_potion",
            {"slot": 1, "target": "JAW_WORM_0"},
            {"action": "use_potion", "slot": 1, "target": "JAW_WORM_0"},
        ),
        (
            "choose_map_node",
            {"index": 2},
            {"action": "choose_map_node", "index": 2},
        ),
        (
            "choose_event_option",
            {"index": 0},
            {"action": "choose_event_option", "index": 0},
        ),
        (
            "select_card_reward",
            {"card_index": 1},
            {"action": "select_card_reward", "card_index": 1},
        ),
        (
            "crystal_sphere_set_tool",
            {"tool": "big"},
            {"action": "crystal_sphere_set_tool", "tool": "big"},
        ),
        (
            "crystal_sphere_click_cell",
            {"x": 3, "y": 4},
            {"action": "crystal_sphere_click_cell", "x": 3, "y": 4},
        ),
    ],
)
def test_dispatch_round_trip_matrix(recorder, name, args, expected_body):
    client, records = recorder()
    with client:
        dispatch(client, name, args)
    assert _last_post(records)["json"] == expected_body


# ── actions_for_state (F-006 gate input) ─────────────────────────────────


@pytest.mark.parametrize(
    "state_type, must_include, must_exclude",
    [
        ("monster", {"play_card", "end_turn", "use_potion"}, {"choose_map_node", "menu_select"}),
        ("map", {"choose_map_node", "use_potion"}, {"play_card", "end_turn", "menu_select"}),
        ("event", {"choose_event_option", "advance_dialogue"}, {"play_card", "menu_select"}),
        ("rewards", {"claim_reward", "proceed"}, {"play_card", "menu_select"}),
        ("card_reward", {"select_card_reward", "skip_card_reward"}, {"claim_reward"}),
        ("rest_site", {"choose_rest_option", "proceed"}, {"shop_purchase"}),
        ("shop", {"shop_purchase", "proceed"}, {"choose_rest_option"}),
        ("treasure", {"claim_treasure_relic", "proceed"}, {"shop_purchase"}),
        (
            "card_select",
            {"select_card", "confirm_selection", "cancel_selection"},
            {"select_bundle", "play_card"},
        ),
        (
            "bundle_select",
            {"select_bundle", "confirm_bundle_selection", "cancel_bundle_selection"},
            {"select_card"},
        ),
        ("relic_select", {"select_relic", "skip_relic_selection"}, {"select_card"}),
        (
            "crystal_sphere",
            {"crystal_sphere_set_tool", "crystal_sphere_click_cell", "crystal_sphere_proceed"},
            {"play_card", "menu_select"},
        ),
        ("hand_select", {"combat_select_card", "combat_confirm_selection"}, {"play_card"}),
        ("menu", {"menu_select"}, {"play_card", "choose_map_node"}),
        ("game_over", {"menu_select"}, {"play_card"}),
    ],
)
def test_actions_for_state_gate(state_type, must_include, must_exclude):
    visible = {s.name for s in actions_for_state(state_type)}
    assert must_include.issubset(visible), f"{state_type} missing {must_include - visible}"
    assert visible.isdisjoint(must_exclude), (
        f"{state_type} leaks {visible & must_exclude}"
    )


def test_actions_for_state_unknown_returns_empty():
    assert actions_for_state("overlay") == []
    assert actions_for_state("unknown") == []
    assert actions_for_state("definitely-not-a-state") == []


# ── to_tool_schema ───────────────────────────────────────────────────────


def test_to_tool_schema_required_and_optional_params():
    schema = to_tool_schema(ACTION_SCHEMAS["play_card"])
    assert schema.name == "play_card"
    params = schema.parameters
    assert params["type"] == "object"
    assert params["additionalProperties"] is False
    assert params["required"] == ["card_index"]
    assert params["properties"]["card_index"]["type"] == "integer"
    assert params["properties"]["target"]["type"] == "string"


def test_to_tool_schema_no_args_action_has_no_required_field():
    schema = to_tool_schema(ACTION_SCHEMAS["end_turn"])
    assert schema.parameters["properties"] == {}
    assert "required" not in schema.parameters


def test_to_tool_schema_enum_param_carries_values():
    schema = to_tool_schema(ACTION_SCHEMAS["crystal_sphere_set_tool"])
    tool = schema.parameters["properties"]["tool"]
    assert tool["enum"] == ["big", "small"]
    assert tool["type"] == "string"


def test_every_schema_renders_to_valid_tool_schema():
    for name, action in ACTION_SCHEMAS.items():
        ts = to_tool_schema(action)
        assert ts.name == name
        assert ts.parameters["type"] == "object"
        assert isinstance(ts.parameters["properties"], dict)
