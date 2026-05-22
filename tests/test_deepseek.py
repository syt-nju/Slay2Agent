from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from slay2agent.config import (
    DEFAULT_DEEPSEEK_LLM_BASE_URL,
    DEFAULT_DEEPSEEK_LLM_MODEL,
    LLMConfig,
)
from slay2agent.llm import build_llm_adapter
from slay2agent.llm.deepseek import DeepSeekAdapter
from slay2agent.llm.openai_compat import OpenAICompatibleAdapter
from slay2agent.llm.protocol import Message, ToolSchema


@pytest.fixture
def deepseek_adapter(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}
    mock_client = MagicMock()

    def fake_openai(**kwargs):
        captured.update(kwargs)
        return mock_client

    monkeypatch.setattr("slay2agent.llm.deepseek.openai.OpenAI", fake_openai)
    adapter = DeepSeekAdapter(
        model=DEFAULT_DEEPSEEK_LLM_MODEL,
        api_key="sk-ds-test",
        timeout=45.0,
    )
    return adapter, mock_client, captured


def _fake_completion(*, content: str | None = "ok", reasoning_content: str | None = None):
    message = SimpleNamespace(
        content=content,
        tool_calls=None,
        reasoning_content=reasoning_content,
    )
    choice = SimpleNamespace(message=message, finish_reason="stop")
    usage = SimpleNamespace(prompt_tokens=3, completion_tokens=2)
    return SimpleNamespace(
        choices=[choice],
        usage=usage,
        model=DEFAULT_DEEPSEEK_LLM_MODEL,
    )


def test_deepseek_adapter_initializes_official_endpoint_without_openrouter_headers(
    deepseek_adapter,
) -> None:
    _adapter, _mock_client, captured = deepseek_adapter
    assert captured["api_key"] == "sk-ds-test"
    assert captured["base_url"] == DEFAULT_DEEPSEEK_LLM_BASE_URL
    assert captured["timeout"] == 45.0
    assert captured.get("default_headers") is None


def test_deepseek_chat_forwards_tools_and_optional_params(deepseek_adapter) -> None:
    adapter, mock_client, _captured = deepseek_adapter
    mock_client.chat.completions.create.return_value = _fake_completion()
    schema = ToolSchema(
        name="echo",
        description="Echo text",
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    )

    adapter.chat(
        [Message(role="user", content="call echo")],
        tools=[schema],
        tool_choice="required",
        max_output_tokens=128,
        temperature=0.2,
        extra_body={"thinking": {"type": "enabled"}},
    )

    kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == DEFAULT_DEEPSEEK_LLM_MODEL
    assert kwargs["tool_choice"] == "required"
    assert kwargs["tools"][0]["function"]["name"] == "echo"
    assert kwargs["parallel_tool_calls"] is False
    assert kwargs["max_tokens"] == 128
    assert kwargs["temperature"] == 0.2
    assert kwargs["extra_body"] == {"thinking": {"type": "enabled"}}


def test_deepseek_response_reasoning_content_is_preserved(deepseek_adapter) -> None:
    adapter, mock_client, _captured = deepseek_adapter
    mock_client.chat.completions.create.return_value = _fake_completion(
        content="answer",
        reasoning_content="private reasoning trace",
    )

    resp = adapter.chat([Message(role="user", content="think")])

    assert resp.message.content == "answer"
    assert resp.message.reasoning_content == "private reasoning trace"


def test_build_llm_adapter_returns_deepseek_adapter_for_deepseek_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "slay2agent.llm.deepseek.openai.OpenAI",
        lambda **_kwargs: MagicMock(),
    )
    cfg = LLMConfig(provider="deepseek", api_key="sk-ds-test")
    adapter = build_llm_adapter(cfg)
    assert isinstance(adapter, DeepSeekAdapter)


def test_build_llm_adapter_keeps_openai_compat_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "slay2agent.llm.openai_compat.openai.OpenAI",
        lambda **_kwargs: MagicMock(),
    )
    cfg = LLMConfig(api_key="sk-or-test")
    adapter = build_llm_adapter(cfg)
    assert isinstance(adapter, OpenAICompatibleAdapter)
