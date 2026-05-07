from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from slay2agent.game import GameClient
from slay2agent.game.actions import (
    choose_event_option,
    choose_map_node,
    claim_reward,
    end_turn,
    menu_select,
    play_card,
    proceed,
    select_card_reward,
    use_potion,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict[str, Any]:
    with (FIXTURE_DIR / name).open("r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def recorder():
    """Make a GameClient that records every request and replies with a JSON dict.

    The reply for the GET (settle re-read) is configurable per-test.
    """

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
                "query": dict(request.url.params),
            }
            if request.content:
                entry["json"] = json.loads(request.content.decode("utf-8"))
            records.append(entry)
            if request.method == "POST":
                return httpx.Response(200, json=post_payload)
            return httpx.Response(200, json=get_payload)

        transport = httpx.MockTransport(handler)
        http_client = httpx.Client(
            base_url="http://test-mod", transport=transport
        )
        client = GameClient("http://test-mod", client=http_client)
        return client, records

    return _build


def _last_post(records: list[dict[str, Any]]) -> dict[str, Any]:
    posts = [r for r in records if r["method"] == "POST"]
    assert posts, "expected at least one POST request"
    return posts[-1]


def test_play_card_with_target(recorder) -> None:
    client, records = recorder()
    with client:
        play_card(client, card_index=2, target="JAW_WORM_0")
    body = _last_post(records)["json"]
    assert body == {"action": "play_card", "card_index": 2, "target": "JAW_WORM_0"}


def test_play_card_drops_none_target(recorder) -> None:
    client, records = recorder()
    with client:
        play_card(client, card_index=0)
    body = _last_post(records)["json"]
    assert body == {"action": "play_card", "card_index": 0}
    assert "target" not in body


def test_end_turn_has_no_params(recorder) -> None:
    client, records = recorder()
    with client:
        end_turn(client)
    body = _last_post(records)["json"]
    assert body == {"action": "end_turn"}


def test_use_potion_with_target(recorder) -> None:
    client, records = recorder()
    with client:
        use_potion(client, slot=1, target="JAW_WORM_0")
    body = _last_post(records)["json"]
    assert body == {"action": "use_potion", "slot": 1, "target": "JAW_WORM_0"}


def test_choose_map_node(recorder) -> None:
    client, records = recorder(get_state_payload=_load("state_map.json"))
    with client:
        result = choose_map_node(client, index=2)
    assert result["state_type"] == "map"
    assert _last_post(records)["json"] == {"action": "choose_map_node", "index": 2}


def test_choose_event_option(recorder) -> None:
    client, records = recorder(get_state_payload=_load("state_event.json"))
    with client:
        choose_event_option(client, index=0)
    assert _last_post(records)["json"] == {
        "action": "choose_event_option",
        "index": 0,
    }


def test_claim_reward_then_proceed(recorder) -> None:
    client, records = recorder(get_state_payload=_load("state_rewards.json"))
    with client:
        claim_reward(client, index=0)
        proceed(client)
    posts = [r for r in records if r["method"] == "POST"]
    assert posts[0]["json"] == {"action": "claim_reward", "index": 0}
    assert posts[1]["json"] == {"action": "proceed"}


def test_select_card_reward(recorder) -> None:
    client, records = recorder(get_state_payload=_load("state_card_reward.json"))
    with client:
        select_card_reward(client, card_index=1)
    assert _last_post(records)["json"] == {
        "action": "select_card_reward",
        "card_index": 1,
    }


def test_menu_select_with_seed(recorder) -> None:
    client, records = recorder()
    with client:
        menu_select(client, option="ironclad", seed="ABC123")
    assert _last_post(records)["json"] == {
        "action": "menu_select",
        "option": "ironclad",
        "seed": "ABC123",
    }


def test_menu_select_without_seed(recorder) -> None:
    client, records = recorder()
    with client:
        menu_select(client, option="back")
    body = _last_post(records)["json"]
    assert body == {"action": "menu_select", "option": "back"}
    assert "seed" not in body
