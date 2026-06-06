"""Canonical LLM types + adapter ABC.

All provider adapters consume and produce these types. Provider SDK objects
are only allowed to surface via ``LLMResponse.raw`` for tracing/debugging.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]
StopReason = Literal["stop", "tool_calls", "length", "error"]
ToolChoice = Literal["auto", "required", "none"]

AgentRole = Literal[
    "main",
    "oracle_updater",
    "compactor",
    # F-013 offline skill maintenance roles.
    "failure_analyzer",
    "distiller_cluster",
    "distiller",
]
"""Identifies which agent made an LLM call.

Used by ``UsageTracker`` to bucket tokens by ``(agent_role, model)`` so that
run summaries can report per-agent input/output totals even when all agents
share the same underlying model slug.
"""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Message:
    role: Role
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    reasoning_content: str | None = None
    """Chain-of-thought text returned by reasoning models (e.g. MiMo, DeepSeek-R1).

    Must be passed back verbatim in subsequent API calls to providers that
    require it (e.g. Xiaomi MiMo, any model using OpenAI-compatible
    ``reasoning_content`` round-tripping).  ``None`` for non-reasoning models.
    """


@dataclass
class ToolSchema:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class LLMResponse:
    message: Message
    usage: Usage
    stop_reason: StopReason
    model: str
    raw: Any = field(default=None, repr=False)


class LLMAdapter(ABC):
    """Provider-agnostic LLM adapter. Subclasses only implement ``chat()``."""

    def __init__(self, model: str):
        self.model = model

    @abstractmethod
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
        """Send one request, return canonical response.

        ``extra_body`` is merged into the JSON request body verbatim and is
        the escape hatch for provider-specific parameters that have no
        canonical equivalent (e.g. ``{"enable_thinking": true,
        "thinking_budget": 8192}`` for MiMo / DeepSeek-R1 reasoning models).

        Subclasses MUST NOT perform retry, usage accounting, or budget
        checks here — those are cross-cutting concerns handled by
        ``call_with_retry`` and ``UsageTracker``.
        """
