"""OpenRouter adapter (OpenAI-compatible endpoint).

Translation surface area is intentionally thin — the OpenAI schema is also
our canonical shape, so the only real work is:
  * arguments: dict <-> JSON string at message/response boundaries
  * tool schema: wrap in ``{"type": "function", "function": {...}}``
  * stop_reason: collapse to {stop, tool_calls, length, error}
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

_BASE_URL = "https://openrouter.ai/api/v1"
_DEFAULT_HEADERS = {
    "HTTP-Referer": "https://github.com/slay2agent/slay2agent",
    "X-Title": "slay2agent",
}


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

    return LLMResponse(
        message=Message(
            role="assistant",
            content=msg.content,
            tool_calls=tool_calls,
        ),
        usage=usage,
        stop_reason=stop_reason,
        model=getattr(resp, "model", "") or "",
        raw=resp,
    )


class OpenRouterAdapter(LLMAdapter):
    def __init__(self, model: str, api_key: str, timeout: float = 120.0):
        super().__init__(model)
        self._client = openai.OpenAI(
            api_key=api_key,
            base_url=_BASE_URL,
            timeout=timeout,
            default_headers=_DEFAULT_HEADERS,
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
