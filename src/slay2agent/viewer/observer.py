"""RunObserver protocol and NoOpObserver (F-009a).

The loop calls observer methods at key points.  When --live is not used,
a NoOpObserver is injected so there is zero overhead.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class RunObserver(Protocol):
    """Observer protocol for the agent loop.

    Implementations MUST NOT raise — failures in the viewer layer must not
    interfere with agent execution.
    """

    def on_step_start(
        self,
        step: int,
        state_type: str,
        user_message: str,
        system_summary: str,
    ) -> None: ...

    def on_llm_response(
        self,
        tool_call_name: str | None,
        tool_call_args: dict[str, Any] | None,
        usage: dict[str, int],
    ) -> None: ...

    def on_tool_result(
        self,
        action: str,
        result_summary: str,
    ) -> None: ...

    def on_memory_event(
        self,
        event_type: str,
        detail: str,
    ) -> None: ...

    def on_context_update(
        self,
        oracle_content: str,
        skill_summaries: list[dict[str, str]],
    ) -> None: ...

    def on_run_end(
        self,
        termination_reason: str,
        total_steps: int,
    ) -> None: ...


class NoOpObserver:
    """Default observer that does nothing (zero overhead when --live is off)."""

    def on_step_start(self, step: int, state_type: str, user_message: str, system_summary: str) -> None:
        pass

    def on_llm_response(self, tool_call_name: str | None, tool_call_args: dict[str, Any] | None, usage: dict[str, int]) -> None:
        pass

    def on_tool_result(self, action: str, result_summary: str) -> None:
        pass

    def on_memory_event(self, event_type: str, detail: str) -> None:
        pass

    def on_context_update(self, oracle_content: str, skill_summaries: list[dict[str, str]]) -> None:
        pass

    def on_run_end(self, termination_reason: str, total_steps: int) -> None:
        pass
