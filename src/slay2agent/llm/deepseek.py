"""DeepSeek official OpenAI-compatible LLM adapter."""

from __future__ import annotations

from typing import Any

import openai

from slay2agent.config import DEFAULT_DEEPSEEK_LLM_BASE_URL
from slay2agent.llm.openai_compat import (
    _from_openai_response,
    _to_openai_messages,
    _to_openai_tools,
)
from slay2agent.llm.protocol import (
    LLMAdapter,
    LLMResponse,
    Message,
    ToolChoice,
    ToolSchema,
)


class DeepSeekAdapter(LLMAdapter):
    """LLM adapter for DeepSeek's official OpenAI-compatible API."""

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str = DEFAULT_DEEPSEEK_LLM_BASE_URL,
        *,
        timeout: float = 120.0,
    ):
        super().__init__(model)
        self._client = openai.OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            default_headers=None,
        )

    def chat(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        *,
        tool_choice: ToolChoice = "auto",
        max_output_tokens: int | None = None,
        temperature: float | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": _to_openai_messages(messages),
        }
        if tools and tool_choice != "none":
            kwargs["tools"] = _to_openai_tools(tools)
            kwargs["tool_choice"] = tool_choice
            kwargs["parallel_tool_calls"] = False
        if max_output_tokens is not None:
            kwargs["max_tokens"] = max_output_tokens
        if temperature is not None:
            kwargs["temperature"] = temperature
        if extra_body:
            kwargs["extra_body"] = extra_body

        resp = self._client.chat.completions.create(**kwargs)
        return _from_openai_response(resp)
