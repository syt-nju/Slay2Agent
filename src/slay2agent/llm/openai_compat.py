"""OpenAI-compatible LLM adapter.

Works against any OpenAI-compatible endpoint — OpenAI native, OpenRouter,
vLLM, DeepSeek, Together AI, etc. — by accepting ``base_url`` as a config
parameter.

Translation surface area is intentionally thin — the OpenAI schema is also
our canonical shape, so the only real work is:
  * arguments: dict <-> JSON string at message/response boundaries
  * tool schema: wrap in ``{"type": "function", "function": {...}}``
  * stop_reason: collapse to {stop, tool_calls, length, error}

OpenRouter-specific headers (HTTP-Referer, X-Title) are injected automatically
when ``base_url`` contains ``openrouter.ai``; all other providers are
unaffected.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import openai

from slay2agent.llm.protocol import (
    LLMAdapter,
    LLMResponse,
    Message,
    StopReason,
    ToolCall,
    ToolChoice,
    ToolSchema,
    Usage,
)

logger = logging.getLogger(__name__)

_OPENROUTER_HEADERS = {
    "HTTP-Referer": "https://github.com/slay2agent/slay2agent",
    "X-Title": "slay2agent",
}


def _openrouter_headers_for(base_url: str) -> dict[str, str]:
    """Return OpenRouter-specific headers if the base URL points to openrouter.ai."""
    if "openrouter.ai" in base_url:
        return _OPENROUTER_HEADERS
    return {}


def _to_openai_messages(messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        d: dict[str, Any] = {"role": m.role}
        if m.content is not None:
            d["content"] = m.content
        if m.tool_call_id is not None:
            d["tool_call_id"] = m.tool_call_id
        if m.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                    },
                }
                for tc in m.tool_calls
            ]
        # Reasoning models (e.g. MiMo, DeepSeek-R1) require the chain-of-thought
        # to be round-tripped back in every subsequent assistant message.
        if m.role == "assistant" and m.reasoning_content is not None:
            d["reasoning_content"] = m.reasoning_content
        out.append(d)
    return out


def _to_openai_tools(tools: list[ToolSchema]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in tools
    ]


_STOP_REASON_MAP: dict[str, StopReason] = {
    "stop": "stop",
    "tool_calls": "tool_calls",
    "length": "length",
    "function_call": "tool_calls",
}


def _from_openai_response(resp: Any) -> LLMResponse:
    choice = resp.choices[0]
    msg = choice.message

    tool_calls: list[ToolCall] | None = None
    if getattr(msg, "tool_calls", None):
        tool_calls = []
        for tc in msg.tool_calls:
            fn = tc.function
            raw_args = fn.arguments or "{}"
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError:
                logger.error(
                    "tool_call arguments were not valid JSON, keeping raw: %r",
                    raw_args,
                )
                args = {"__raw__": raw_args}
            tool_calls.append(ToolCall(id=tc.id, name=fn.name, arguments=args))

    finish = choice.finish_reason or "stop"
    stop_reason = _STOP_REASON_MAP.get(finish, "stop")

    usage_raw = getattr(resp, "usage", None)
    usage = Usage(
        input_tokens=int(getattr(usage_raw, "prompt_tokens", 0) or 0),
        output_tokens=int(getattr(usage_raw, "completion_tokens", 0) or 0),
    )

    reasoning_content = getattr(msg, "reasoning_content", None) or None

    return LLMResponse(
        message=Message(
            role="assistant",
            content=msg.content,
            tool_calls=tool_calls,
            reasoning_content=reasoning_content,
        ),
        usage=usage,
        stop_reason=stop_reason,
        model=getattr(resp, "model", "") or "",
        raw=resp,
    )


class OpenAICompatibleAdapter(LLMAdapter):
    """LLM adapter for any OpenAI-compatible endpoint.

    Args:
        model:        Model slug to pass to the API.
        api_key:      API key for the provider.
        base_url:     Base URL of the OpenAI-compatible endpoint.
                      Examples:
                        - OpenAI:     ``https://api.openai.com/v1``
                        - OpenRouter: ``https://openrouter.ai/api/v1``
                        - vLLM:       ``http://localhost:8000/v1``
        extra_headers: Additional HTTP headers to send with every request.
                       Headers for ``openrouter.ai`` are injected automatically;
                       set this for any other provider-specific headers.
        timeout:      HTTP timeout in seconds.
    """

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str,
        *,
        extra_headers: dict[str, str] | None = None,
        timeout: float = 120.0,
    ):
        super().__init__(model)
        headers = {**_openrouter_headers_for(base_url), **(extra_headers or {})}
        self._client = openai.OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            default_headers=headers if headers else None,
        )

    def chat(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        *,
        tool_choice: ToolChoice = "auto",
        max_output_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": _to_openai_messages(messages),
        }
        # OpenRouter rejects tool_choice="none"; we drop the tools list instead.
        if tools and tool_choice != "none":
            kwargs["tools"] = _to_openai_tools(tools)
            kwargs["tool_choice"] = tool_choice
            kwargs["parallel_tool_calls"] = False
        if max_output_tokens is not None:
            kwargs["max_tokens"] = max_output_tokens
        if temperature is not None:
            kwargs["temperature"] = temperature

        resp = self._client.chat.completions.create(**kwargs)
        return _from_openai_response(resp)
