"""Tests for F-012: L0 compaction sub-agent.

Covers:
- Threshold trigger: compaction fires only when len(l0) > threshold AND len(l0) > keep.
- Disabled path: compaction is skipped when l0_compact_enabled=False.
- Structure: result is [summary_msg] + recent K messages.
- Failure resilience: on LLM error, original l0 is returned unchanged.
- Token accounting: usage is recorded under role="compactor".
- Trace written to subagent.jsonl on success and on failure.
- run_l0_compaction is not called when disabled via config.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from slay2agent.agent.compactor import run_l0_compaction
from slay2agent.agent.trace import TraceWriter
from slay2agent.llm.protocol import LLMResponse, Message, Usage
from slay2agent.llm.usage import UsageTracker


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_usage(inp: int = 20, out: int = 10) -> Usage:
    return Usage(input_tokens=inp, output_tokens=out)


def _make_text_response(text: str = "The agent fought well.") -> LLMResponse:
    msg = Message(role="assistant", content=text)
    return LLMResponse(
        message=msg,
        usage=_make_usage(),
        stop_reason="stop",
        model="mock-model",
        raw={},
    )


def _make_l0(n: int) -> list[Message]:
    """Build an L0 of n messages alternating assistant/tool pairs."""
    msgs = []
    for i in range(n // 2):
        msgs.append(Message(role="assistant", content=f"assistant msg {i}"))
        msgs.append(Message(
            role="tool",
            content=f"tool result {i}",
            tool_call_id=f"tc{i}",
        ))
    # If n is odd, add one more assistant message.
    if n % 2:
        msgs.append(Message(role="assistant", content=f"assistant msg extra"))
    return msgs


def _run_compaction(
    tmp_path: Path,
    l0: list[Message],
    compact_keep: int = 6,
    adapter_response: LLMResponse | None = None,
    adapter_raises: Exception | None = None,
) -> tuple[list[Message], TraceWriter, UsageTracker]:
    trace = TraceWriter(tmp_path / "runs" / "run_test")
    tracker = UsageTracker()

    adapter = MagicMock()
    if adapter_raises is not None:
        adapter.chat.side_effect = adapter_raises
    else:
        resp = adapter_response or _make_text_response()
        adapter.chat.return_value = resp

    result = run_l0_compaction(
        l0,
        compact_keep=compact_keep,
        adapter=adapter,
        tracker=tracker,
        trace=trace,
        model="mock-model",
        step=42,
        state_type="monster",
    )
    return result, trace, tracker


# ── Structure tests ────────────────────────────────────────────────────────────


def test_compaction_returns_summary_plus_recent(tmp_path: Path) -> None:
    """Result is [summary_user_msg] + last K original messages."""
    l0 = _make_l0(32)
    result, _, _ = _run_compaction(tmp_path, l0, compact_keep=6)

    assert len(result) == 7  # 1 summary + 6 recent
    # First message is the summary: role=user.
    assert result[0].role == "user"
    assert "Compacted context" in (result[0].content or "")
    assert "32" in (result[0].content or "") or "26" in (result[0].content or "")

    # Remaining messages are the verbatim tail of the original l0.
    for i, msg in enumerate(result[1:]):
        assert msg is l0[32 - 6 + i]


def test_compaction_summary_contains_state_type(tmp_path: Path) -> None:
    """Summary message header includes the state_type."""
    l0 = _make_l0(30)
    result, _, _ = _run_compaction(tmp_path, l0, compact_keep=4)
    assert "monster" in (result[0].content or "")


def test_compaction_summary_contains_llm_text(tmp_path: Path) -> None:
    """Summary message body includes the LLM's prose output."""
    l0 = _make_l0(28)
    custom_resp = _make_text_response("Strategically destroyed the goblin.")
    result, _, _ = _run_compaction(tmp_path, l0, compact_keep=6, adapter_response=custom_resp)
    assert "Strategically destroyed the goblin." in (result[0].content or "")


def test_compaction_keep_all_when_keep_equals_l0_length(tmp_path: Path) -> None:
    """When compact_keep >= len(l0), old is empty — no messages are compacted."""
    # Actually in this case we'd only compact when len(l0) > threshold AND len(l0) > keep.
    # Here we test the edge case in the compactor itself: old = l0[:-keep]
    l0 = _make_l0(6)
    result, _, _ = _run_compaction(tmp_path, l0, compact_keep=6)
    # old = l0[:-6] = [] — empty transcript sent to LLM
    # summary message is still prepended, with empty old content
    assert len(result) == 7  # 1 summary + 6 recent


# ── Failure resilience ─────────────────────────────────────────────────────────


def test_compaction_failure_returns_original_l0(tmp_path: Path) -> None:
    """On LLM error, the original l0 is returned unchanged."""
    l0 = _make_l0(32)
    result, _, _ = _run_compaction(
        tmp_path, l0, compact_keep=6,
        adapter_raises=RuntimeError("API error"),
    )
    # Result is the same list object as input.
    assert result is l0


def test_compaction_failure_writes_error_trace(tmp_path: Path) -> None:
    """Failed compaction still writes a subagent trace entry."""
    l0 = _make_l0(32)
    _, trace, _ = _run_compaction(
        tmp_path, l0, compact_keep=6,
        adapter_raises=RuntimeError("connection refused"),
    )
    subagent_path = trace.run_dir / "subagent.jsonl"
    assert subagent_path.exists()
    entry = json.loads(subagent_path.read_text().strip())
    assert entry["agent_role"] == "compactor"
    assert "ERROR" in entry["file_diff_summary"]


def test_compaction_empty_llm_response_returns_original(tmp_path: Path) -> None:
    """Empty LLM text response → ValueError → original l0 returned."""
    l0 = _make_l0(32)
    empty_resp = LLMResponse(
        message=Message(role="assistant", content=""),
        usage=_make_usage(),
        stop_reason="stop",
        model="mock-model",
        raw={},
    )
    result, _, _ = _run_compaction(tmp_path, l0, compact_keep=6, adapter_response=empty_resp)
    assert result is l0


# ── Token accounting ───────────────────────────────────────────────────────────


def test_compaction_records_tokens_under_compactor_role(tmp_path: Path) -> None:
    """LLM usage is recorded with role='compactor'."""
    l0 = _make_l0(32)
    resp = _make_text_response()
    _, _, tracker = _run_compaction(tmp_path, l0, compact_keep=6, adapter_response=resp)

    totals = tracker.role_totals()
    assert "compactor" in totals
    assert totals["compactor"].input_tokens == 20
    assert totals["compactor"].output_tokens == 10


def test_compaction_failure_does_not_record_tokens_on_adapter_error(tmp_path: Path) -> None:
    """When adapter raises before returning usage, no tokens are recorded."""
    l0 = _make_l0(32)
    _, _, tracker = _run_compaction(
        tmp_path, l0, compact_keep=6,
        adapter_raises=RuntimeError("network error"),
    )
    totals = tracker.role_totals()
    # "compactor" should either be absent or have zero tokens.
    compactor_usage = totals.get("compactor")
    if compactor_usage is not None:
        assert compactor_usage.input_tokens == 0
        assert compactor_usage.output_tokens == 0


# ── Trace written ──────────────────────────────────────────────────────────────


def test_compaction_writes_subagent_trace_on_success(tmp_path: Path) -> None:
    """Successful compaction writes a well-formed subagent trace entry."""
    l0 = _make_l0(32)
    _, trace, _ = _run_compaction(tmp_path, l0, compact_keep=6)

    subagent_path = trace.run_dir / "subagent.jsonl"
    assert subagent_path.exists()
    entry = json.loads(subagent_path.read_text().strip())
    assert entry["agent_role"] == "compactor"
    assert "compacted" in entry["file_diff_summary"]
    assert "trigger_reason" in entry
    assert "l0_threshold" in entry["trigger_reason"]
    assert entry["llm_usage"]["input_tokens"] == 20
    assert entry["llm_usage"]["output_tokens"] == 10


# ── Config-level disable (integration) ────────────────────────────────────────


def test_compaction_not_triggered_when_disabled_in_config() -> None:
    """When l0_compact_enabled=False, run_l0_compaction is never called from loop logic."""
    # We test the guard condition logic directly (not the full loop).
    # The loop guard is: mem_cfg.l0_compact_enabled and len(l0) > threshold and len(l0) > keep
    from slay2agent.config import MemoryConfig

    cfg = MemoryConfig(l0_compact_enabled=False, l0_compact_threshold=30, l0_compact_keep=6)
    l0 = _make_l0(40)

    # The guard must be False when disabled.
    guard = (
        cfg.l0_compact_enabled
        and len(l0) > cfg.l0_compact_threshold
        and len(l0) > cfg.l0_compact_keep
    )
    assert guard is False


def test_compaction_triggered_when_enabled_and_above_threshold() -> None:
    """Guard is True when enabled and l0 exceeds threshold."""
    from slay2agent.config import MemoryConfig

    cfg = MemoryConfig(l0_compact_enabled=True, l0_compact_threshold=30, l0_compact_keep=6)
    l0 = _make_l0(32)

    guard = (
        cfg.l0_compact_enabled
        and len(l0) > cfg.l0_compact_threshold
        and len(l0) > cfg.l0_compact_keep
    )
    assert guard is True


def test_compaction_not_triggered_below_threshold() -> None:
    """Guard is False when l0 <= threshold."""
    from slay2agent.config import MemoryConfig

    cfg = MemoryConfig(l0_compact_enabled=True, l0_compact_threshold=30, l0_compact_keep=6)
    l0 = _make_l0(30)  # exactly at threshold, NOT above

    guard = (
        cfg.l0_compact_enabled
        and len(l0) > cfg.l0_compact_threshold
        and len(l0) > cfg.l0_compact_keep
    )
    assert guard is False


# ── Config env-var loading ─────────────────────────────────────────────────────


def test_memory_config_default_values() -> None:
    """MemoryConfig defaults match the F-012 acceptance criteria."""
    from slay2agent.config import MemoryConfig

    cfg = MemoryConfig()
    assert cfg.l0_compact_enabled is True
    assert cfg.l0_compact_threshold == 30
    assert cfg.l0_compact_keep == 6


def test_memory_config_from_env_reads_l0_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """from_env() reads L0_COMPACT_* environment variables."""
    from slay2agent.config import MemoryConfig

    monkeypatch.setenv("L0_COMPACT_ENABLED", "false")
    monkeypatch.setenv("L0_COMPACT_THRESHOLD", "50")
    monkeypatch.setenv("L0_COMPACT_KEEP", "10")

    cfg = MemoryConfig.from_env()
    assert cfg.l0_compact_enabled is False
    assert cfg.l0_compact_threshold == 50
    assert cfg.l0_compact_keep == 10


def test_memory_config_compact_enabled_true_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """L0_COMPACT_ENABLED=true keeps enabled."""
    from slay2agent.config import MemoryConfig

    monkeypatch.setenv("L0_COMPACT_ENABLED", "true")
    cfg = MemoryConfig.from_env()
    assert cfg.l0_compact_enabled is True
