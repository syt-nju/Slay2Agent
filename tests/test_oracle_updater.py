"""Tests for F-008c: oracle_updater sub-agent runner."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from slay2agent.agent.oracle_updater import _write_oracle_safe, run_oracle_updater
from slay2agent.agent.trace import TraceWriter
from slay2agent.llm.protocol import LLMResponse, Message, ToolCall, Usage
from slay2agent.llm.usage import UsageTracker
from slay2agent.memory.skill_registry import SkillRegistry


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_usage(inp: int = 10, out: int = 5) -> Usage:
    return Usage(input_tokens=inp, output_tokens=out)


def _make_tool_response(name: str, args: dict, call_id: str = "tc1") -> LLMResponse:
    tc = ToolCall(id=call_id, name=name, arguments=args)
    msg = Message(role="assistant", content=None, tool_calls=[tc])
    return LLMResponse(
        message=msg,
        usage=_make_usage(),
        stop_reason="tool_use",
        model="mock-model",
        raw={},
    )


def _make_text_response(text: str = "# Oracle\nBe strategic.") -> LLMResponse:
    msg = Message(role="assistant", content=text)
    return LLMResponse(
        message=msg,
        usage=_make_usage(),
        stop_reason="stop",
        model="mock-model",
        raw={},
    )


def _run(
    tmp_path: Path,
    adapter_responses: list[LLMResponse],
    existing_oracle: str | None = None,
    oracle_max_tokens: int = 4000,
) -> tuple[Path, UsageTracker, Path]:
    """Wire up minimal infra and call run_oracle_updater."""
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    oracle_path = tmp_path / "oracle.md"

    if existing_oracle is not None:
        oracle_path.write_text(existing_oracle, encoding="utf-8")

    skills_dir = tmp_path / "skills"
    reg = SkillRegistry(skills_dir)
    tracker = UsageTracker()
    trace = TraceWriter(run_dir)

    adapter = MagicMock()
    adapter.chat.side_effect = adapter_responses

    run_oracle_updater(
        run_trace_summary="Total steps: 5\nTermination: game_over",
        skill_registry=reg,
        oracle_path=oracle_path,
        adapter=adapter,
        tracker=tracker,
        trace=trace,
        model="mock-model",
        termination_reason="game_over",
        oracle_max_tokens=oracle_max_tokens,
    )

    return oracle_path, tracker, run_dir


# ── _write_oracle_safe unit tests ─────────────────────────────────────────────


def test_write_oracle_safe_writes_content(tmp_path):
    oracle_path = tmp_path / "oracle.md"
    result = _write_oracle_safe(oracle_path, "# Strategy\nDo X.", max_chars=10000)
    assert result is True
    assert oracle_path.exists()
    assert "Do X." in oracle_path.read_text(encoding="utf-8")


def test_write_oracle_safe_empty_content_skips(tmp_path):
    oracle_path = tmp_path / "oracle.md"
    oracle_path.write_text("original content", encoding="utf-8")
    result = _write_oracle_safe(oracle_path, "   ", max_chars=10000)
    assert result is False
    # Original file untouched.
    assert oracle_path.read_text(encoding="utf-8") == "original content"


def test_write_oracle_safe_truncates_overlong(tmp_path):
    oracle_path = tmp_path / "oracle.md"
    long_content = "x" * 200
    result = _write_oracle_safe(oracle_path, long_content, max_chars=50)
    assert result is True
    written = oracle_path.read_text(encoding="utf-8")
    assert "_(truncated by oracle_updater)_" in written
    # Actual content part should be 50 chars.
    assert written.startswith("x" * 50)


def test_write_oracle_safe_creates_parent_dir(tmp_path):
    oracle_path = tmp_path / "nested" / "dir" / "oracle.md"
    result = _write_oracle_safe(oracle_path, "content", max_chars=1000)
    assert result is True
    assert oracle_path.exists()


# ── Scenario 1: normal flow (tool call + text response) ───────────────────────


def test_normal_flow_tool_then_text(tmp_path):
    """list_skills → text response → oracle.md written."""
    responses = [
        _make_tool_response("list_skills", {}, "tc1"),
        _make_text_response("# Global Strategy\nAlways look for synergy."),
    ]

    oracle_path, tracker, run_dir = _run(tmp_path, responses)

    assert oracle_path.exists()
    content = oracle_path.read_text(encoding="utf-8")
    assert "Always look for synergy." in content

    # SubagentRecord written.
    subagent_log = run_dir / "subagent.jsonl"
    assert subagent_log.exists()
    record = json.loads(subagent_log.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert record["agent_role"] == "oracle_updater"
    assert record["file_diff_summary"] == "wrote oracle.md"


# ── Scenario 2: direct text (no tool call) ────────────────────────────────────


def test_direct_text_no_tool_call(tmp_path):
    """LLM returns text immediately → oracle.md written correctly."""
    responses = [_make_text_response("# Oracle\nPrioritize defense early.")]

    oracle_path, _, _ = _run(tmp_path, responses)

    assert oracle_path.exists()
    assert "Prioritize defense early." in oracle_path.read_text(encoding="utf-8")


# ── Scenario 3: empty content → skip write ────────────────────────────────────


def test_empty_content_skips_write(tmp_path):
    """LLM returns empty string → oracle.md original preserved; no changes."""
    original = "# Original Oracle\nKeep this."
    oracle_path = tmp_path / "oracle.md"
    oracle_path.write_text(original, encoding="utf-8")

    # Provide LLM adapter that returns empty text
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    skills_dir = tmp_path / "skills"
    reg = SkillRegistry(skills_dir)
    tracker = UsageTracker()
    trace = TraceWriter(run_dir)

    adapter = MagicMock()
    # LLM returns empty content
    msg = Message(role="assistant", content="   ")
    adapter.chat.return_value = LLMResponse(
        message=msg,
        usage=_make_usage(),
        stop_reason="stop",
        model="mock-model",
        raw={},
    )

    run_oracle_updater(
        run_trace_summary="steps: 1",
        skill_registry=reg,
        oracle_path=oracle_path,
        adapter=adapter,
        tracker=tracker,
        trace=trace,
        model="mock-model",
        termination_reason="game_over",
    )

    # Original oracle preserved.
    assert oracle_path.read_text(encoding="utf-8") == original

    # SubagentRecord says no changes.
    subagent_log = run_dir / "subagent.jsonl"
    record = json.loads(subagent_log.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert record["file_diff_summary"] == "no changes"


# ── Scenario 4: overlong content → truncated ─────────────────────────────────


def test_overlong_content_truncated(tmp_path):
    """LLM returns content exceeding max_chars → truncated with marker."""
    long_text = "# Oracle\n" + "strategy line\n" * 500  # definitely > 200 chars
    responses = [_make_text_response(long_text)]

    # Use very small limit to force truncation.
    oracle_path, _, _ = _run(tmp_path, responses, oracle_max_tokens=5)  # 5*4=20 chars

    content = oracle_path.read_text(encoding="utf-8")
    assert "_(truncated by oracle_updater)_" in content
    # Content before truncation marker should be at most 20 chars.
    truncation_marker = "\n\n_(truncated by oracle_updater)_"
    idx = content.find(truncation_marker)
    assert idx <= 20


# ── Scenario 5: failure does not propagate ────────────────────────────────────


def test_failure_does_not_propagate(tmp_path):
    """Adapter raises RuntimeError → run_oracle_updater does not re-raise."""
    original = "# Original\nKeep me."
    oracle_path = tmp_path / "oracle.md"
    oracle_path.write_text(original, encoding="utf-8")

    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    trace = TraceWriter(run_dir)
    reg = SkillRegistry(tmp_path / "skills")
    tracker = UsageTracker()

    adapter = MagicMock()
    adapter.chat.side_effect = RuntimeError("LLM exploded")

    # Must not raise.
    run_oracle_updater(
        run_trace_summary="steps: 3",
        skill_registry=reg,
        oracle_path=oracle_path,
        adapter=adapter,
        tracker=tracker,
        trace=trace,
        model="mock-model",
        termination_reason="error",
    )

    # oracle.md original preserved.
    assert oracle_path.read_text(encoding="utf-8") == original

    # Error trace written.
    subagent_log = run_dir / "subagent.jsonl"
    assert subagent_log.exists()
    record = json.loads(subagent_log.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert "ERROR" in record["file_diff_summary"]


# ── Scenario 6: max_steps protection ─────────────────────────────────────────


def test_max_steps_protection(tmp_path):
    """LLM perpetually returns tool_calls → loop stops after max_steps; oracle not written."""
    infinite_tc = _make_tool_response("list_skills", {}, "tc1")
    responses = [infinite_tc] * 20  # more than max_steps

    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    oracle_path = tmp_path / "oracle.md"
    reg = SkillRegistry(tmp_path / "skills")
    tracker = UsageTracker()
    trace = TraceWriter(run_dir)

    adapter = MagicMock()
    adapter.chat.side_effect = responses

    run_oracle_updater(
        run_trace_summary="steps: 10",
        skill_registry=reg,
        oracle_path=oracle_path,
        adapter=adapter,
        tracker=tracker,
        trace=trace,
        model="mock-model",
        termination_reason="loop_terminated",
        max_steps=3,
    )

    # Exactly max_steps adapter calls made.
    assert adapter.chat.call_count == 3
    # oracle.md not written (no text response).
    assert not oracle_path.exists()


# ── Scenario 7: UsageTracker records oracle_updater role ──────────────────────


def test_usage_tracker_records_oracle_updater_role(tmp_path):
    """All LLM calls are bucketed under 'oracle_updater'."""
    responses = [
        _make_tool_response("list_skills", {}, "tc1"),
        _make_text_response("# Updated Oracle"),
    ]

    _, tracker, _ = _run(tmp_path, responses)

    totals = tracker.role_totals()
    assert "oracle_updater" in totals
    # Two calls: each with 10 input + 5 output = 20 + 10 total.
    assert totals["oracle_updater"].input_tokens == 20
    assert totals["oracle_updater"].output_tokens == 10


# ── Scenario 8: SubagentRecord always written ─────────────────────────────────


def test_subagent_record_always_written_on_success(tmp_path):
    """SubagentRecord written to subagent.jsonl on successful run."""
    responses = [_make_text_response("# Oracle v2")]
    oracle_path, _, run_dir = _run(tmp_path, responses)

    subagent_log = run_dir / "subagent.jsonl"
    assert subagent_log.exists()
    record = json.loads(subagent_log.read_text(encoding="utf-8").strip())
    assert record["agent_role"] == "oracle_updater"
    assert "run_end:" in record["trigger_reason"]


def test_subagent_record_written_on_failure(tmp_path):
    """SubagentRecord with ERROR marker written even on adapter failure."""
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    trace = TraceWriter(run_dir)
    reg = SkillRegistry(tmp_path / "skills")
    tracker = UsageTracker()

    adapter = MagicMock()
    adapter.chat.side_effect = RuntimeError("boom")

    run_oracle_updater(
        run_trace_summary="steps: 0",
        skill_registry=reg,
        oracle_path=tmp_path / "oracle.md",
        adapter=adapter,
        tracker=tracker,
        trace=trace,
        model="mock-model",
        termination_reason="error",
    )

    subagent_log = run_dir / "subagent.jsonl"
    assert subagent_log.exists()
    record = json.loads(subagent_log.read_text(encoding="utf-8").strip())
    assert record["agent_role"] == "oracle_updater"
    assert "ERROR" in record["file_diff_summary"]
