from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest

from slay2agent.game.client import (
    ActionError,
    GameClient,
    GameHTTPError,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict[str, Any]:
    with (FIXTURE_DIR / name).open("r", encoding="utf-8") as f:
        return json.load(f)


def _make_client(
    handler: Callable[[httpx.Request], httpx.Response],
    base_url: str = "http://test-mod",
) -> GameClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(base_url=base_url, transport=transport)
    return GameClient(base_url, client=http_client)


def test_get_state_returns_combat_fixture() -> None:
    state = _load("state_combat.json")
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        return httpx.Response(200, json=state)

    with _make_client(handler) as client:
        got = client.get_state()

    assert got == state
    assert captured["method"] == "GET"
    assert "/api/v1/singleplayer" in captured["url"]
    assert "format=json" in captured["url"]


def test_health_hits_root() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        return httpx.Response(
            200, json={"message": "Hello from STS2 MCP v0.3.4", "status": "ok"}
        )

    with _make_client(handler) as client:
        body = client.health()

    assert body["status"] == "ok"
    assert captured["path"] == "/"


def test_post_action_sends_json_body_and_drops_none_params() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json=_load("action_ok.json"))

    with _make_client(handler) as client:
        resp = client.post_action("play_card", card_index=2, target=None)

    assert resp["status"] == "ok"
    assert captured["body"] == {"action": "play_card", "card_index": 2}


def test_post_action_raises_action_error_on_status_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_load("action_error.json"))

    caplog.set_level("ERROR", logger="slay2agent.game.client")
    with _make_client(handler) as client:
        with pytest.raises(ActionError) as exc_info:
            client.post_action("play_card", card_index=99)

    err = exc_info.value
    assert err.action == "play_card"
    assert err.params == {"card_index": 99}
    assert "out of range" in str(err)
    assert any(
        "STS2MCP rejected action play_card" in rec.getMessage()
        for rec in caplog.records
    ), "ActionError path must logger.error per F-003 acceptance"


def test_get_state_raises_http_error_on_500() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with _make_client(handler) as client:
        with pytest.raises(GameHTTPError) as exc_info:
            client.get_state()

    assert exc_info.value.status_code == 500


def test_get_state_raises_http_error_on_invalid_json() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json")

    with _make_client(handler) as client:
        with pytest.raises(GameHTTPError, match="invalid JSON"):
            client.get_state()


def test_post_action_and_settle_returns_fresh_state() -> None:
    state_before = _load("state_combat.json")
    state_after = dict(state_before)
    state_after["state_type"] = "rewards"

    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(200, json=_load("action_ok.json"))
        return httpx.Response(200, json=state_after)

    with _make_client(handler) as client:
        result = client.post_action_and_settle(
            "end_turn", settle_delay=0
        )

    assert result["state_type"] == "rewards"
    assert calls[0] == ("POST", "/api/v1/singleplayer")
    assert calls[1] == ("GET", "/api/v1/singleplayer")
