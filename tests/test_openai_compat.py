"""OpenAI-compatible adapter tests (§9.4 / F-010).

Covers the three translation functions and the full ``chat()`` round-trip
with a mocked OpenAI SDK client.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from slay2agent.llm.openai_compat import (
    OpenAICompatibleAdapter,
    _from_openai_response,
    _to_openai_messages,
    _to_openai_tools,
)
from slay2agent.llm.protocol import Message, ToolCall, ToolSchema


# ── _to_openai_messages ──────────────────────────────────────────────────


def test_plain_text_messages_roundtrip_shape():
    msgs = _to_openai_messages(
        [
            Message(role="system", content="you are helpful"),
            Message(role="user", content="hi"),
        ]
    )
    assert msgs == [
        {"role": "system", "content": "you are helpful"},
        {"role": "user", "content": "hi"},
    ]


def test_assistant_tool_calls_arguments_serialised_to_json_string():
    msgs = _to_openai_messages(
        [
            Message(
                role="assistant",
                content=None,
                tool_calls=[
                    ToolCall(id="call_1", name="echo", arguments={"text": "hi"}),
                ],
            )
        ]
    )
    assert msgs[0]["role"] == "assistant"
    assert "content" not in msgs[0]  # None content is dropped
    tc = msgs[0]["tool_calls"][0]
    assert tc["id"] == "call_1"
    assert tc["type"] == "function"
    assert tc["function"]["name"] == "echo"
    assert json.loads(tc["function"]["arguments"]) == {"text": "hi"}


def test_tool_result_message_carries_tool_call_id():
    msgs = _to_openai_messages(
        [Message(role="tool", content='{"ok": true}', tool_call_id="call_1")]
    )
    assert msgs == [
        {"role": "tool", "content": '{"ok": true}', "tool_call_id": "call_1"}
    ]


# ── _to_openai_tools ─────────────────────────────────────────────────────


def test_tool_schema_wrapped_as_function_type():
    out = _to_openai_tools(
        [
            ToolSchema(
                name="echo",
                description="Echo text",
                parameters={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            )
        ]
    )
    assert out == [
        {
            "type": "function",
            "function": {
                "name": "echo",
                "description": "Echo text",
                "parameters": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            },
        }
    ]


# ── _from_openai_response ────────────────────────────────────────────────


def _fake_completion(
    *,
    content: str | None,
    tool_calls: list | None = None,
    finish_reason: str = "stop",
    model: str = "anthropic/claude-sonnet-4",
    prompt_tokens: int = 11,
    completion_tokens: int = 5,
):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    usage = SimpleNamespace(
        prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
    )
    return SimpleNamespace(choices=[choice], usage=usage, model=model)


def test_text_response_maps_to_stop():
    resp = _from_openai_response(
        _fake_completion(content="hello", finish_reason="stop")
    )
    assert resp.message.content == "hello"
    assert resp.message.tool_calls is None
    assert resp.stop_reason == "stop"
    assert resp.usage.input_tokens == 11
    assert resp.usage.output_tokens == 5
    assert resp.model == "anthropic/claude-sonnet-4"


def test_tool_call_response_parses_arguments_to_dict():
    fake_tc = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="echo", arguments='{"text": "hi"}'),
    )
    resp = _from_openai_response(
        _fake_completion(
            content=None, tool_calls=[fake_tc], finish_reason="tool_calls"
        )
    )
    assert resp.stop_reason == "tool_calls"
    assert resp.message.tool_calls is not None
    assert len(resp.message.tool_calls) == 1
    tc = resp.message.tool_calls[0]
    assert tc.id == "call_1"
    assert tc.name == "echo"
    assert tc.arguments == {"text": "hi"}  # dict, not str


def test_malformed_tool_arguments_logged_and_preserved(caplog):
    fake_tc = SimpleNamespace(
        id="call_bad",
        function=SimpleNamespace(name="echo", arguments="not-json"),
    )
    with caplog.at_level("ERROR", logger="slay2agent.llm.openai_compat"):
        resp = _from_openai_response(
            _fake_completion(
                content=None, tool_calls=[fake_tc], finish_reason="tool_calls"
            )
        )
    assert resp.message.tool_calls[0].arguments == {"__raw__": "not-json"}
    assert any("not valid JSON" in r.message for r in caplog.records)


def test_length_finish_reason_passthrough():
    resp = _from_openai_response(
        _fake_completion(content="truncated", finish_reason="length")
    )
    assert resp.stop_reason == "length"


def test_unknown_finish_reason_defaults_to_stop():
    resp = _from_openai_response(
        _fake_completion(content="x", finish_reason="content_filter")
    )
    assert resp.stop_reason == "stop"


# ── OpenAICompatibleAdapter.chat() ──────────────────────────────────────


@pytest.fixture
def adapter(monkeypatch):
    mock_client = MagicMock()
    monkeypatch.setattr(
        "slay2agent.llm.openai_compat.openai.OpenAI", lambda **_kw: mock_client
    )
    adapter = OpenAICompatibleAdapter(
        model="anthropic/claude-sonnet-4",
        api_key="sk-test",
        base_url="https://openrouter.ai/api/v1",
    )
    return adapter, mock_client


def test_chat_plain_text(adapter):
    ad, mock_client = adapter
    mock_client.chat.completions.create.return_value = _fake_completion(
        content="pong", finish_reason="stop"
    )

    resp = ad.chat([Message(role="user", content="ping")])

    assert resp.message.content == "pong"
    assert resp.stop_reason == "stop"
    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "anthropic/claude-sonnet-4"
    assert call_kwargs["messages"] == [{"role": "user", "content": "ping"}]
    assert "tools" not in call_kwargs


def test_chat_passes_tools_when_tool_choice_not_none(adapter):
    ad, mock_client = adapter
    mock_client.chat.completions.create.return_value = _fake_completion(
        content="ok", finish_reason="stop"
    )
    schema = ToolSchema(
        name="echo",
        description="",
        parameters={"type": "object", "properties": {}},
    )

    ad.chat([Message(role="user", content="x")], tools=[schema])

    kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert kwargs["tool_choice"] == "auto"
    assert kwargs["tools"][0]["type"] == "function"


def test_chat_drops_tools_when_tool_choice_none(adapter):
    ad, mock_client = adapter
    mock_client.chat.completions.create.return_value = _fake_completion(
        content="ok", finish_reason="stop"
    )
    schema = ToolSchema(name="echo", description="", parameters={})

    ad.chat([Message(role="user", content="x")], tools=[schema], tool_choice="none")

    kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert "tools" not in kwargs
    assert "tool_choice" not in kwargs


def test_chat_forwards_optional_params(adapter):
    ad, mock_client = adapter
    mock_client.chat.completions.create.return_value = _fake_completion(
        content="ok", finish_reason="stop"
    )

    ad.chat(
        [Message(role="user", content="x")],
        max_output_tokens=256,
        temperature=0.3,
    )

    kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert kwargs["max_tokens"] == 256
    assert kwargs["temperature"] == 0.3
