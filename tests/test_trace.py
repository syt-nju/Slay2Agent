"""Tests for F-007: TraceWriter, StepRecord, summary.json structure."""
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

import pytest

from slay2agent.agent.trace import (
    StepRecord,
    SubagentRecord,
    TerminationReason,
    TraceWriter,
    new_run_id,
)
from slay2agent.llm.protocol import Usage
from slay2agent.llm.usage import UsageTracker


# ── new_run_id ─────────────────────────────────────────────────────────────


def test_run_id_format() -> None:
    run_id = new_run_id()
    assert re.fullmatch(r"\d{8}T\d{6}_[0-9a-f]{8}", run_id), run_id


def test_run_id_unique() -> None:
    ids = {new_run_id() for _ in range(50)}
    # The UUID suffix makes collisions vanishingly unlikely
    assert len(ids) == 50


# ── TraceWriter: directory creation ───────────────────────────────────────


def test_trace_writer_creates_directory() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "runs" / "20260510T000000_abcd1234"
        assert not run_dir.exists()
        TraceWriter(run_dir)
        assert run_dir.is_dir()


# ── TraceWriter: write_step ────────────────────────────────────────────────


def _make_step(step: int = 0) -> StepRecord:
    return StepRecord(
        step=step,
        timestamp="2026-05-10T14:23:00",
        state_type="menu",
        l0_cleared=False,
        skill_metadata_ids=[],
        oracle_version=None,
        llm_request_messages=[{"role": "user", "content": "hello"}],
        llm_response_message={"role": "assistant", "content": "hi"},
        llm_usage={"input_tokens": 10, "output_tokens": 5},
        llm_stop_reason="stop",
        tool_name="menu_select",
        tool_args={"option": "singleplayer"},
        tool_result_state_type="menu",
        settled_state_summary="menu: main",
    )


def test_write_step_creates_steps_jsonl() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "run"
        tw = TraceWriter(run_dir)
        tw.write_step(_make_step(0))
        steps_file = run_dir / "steps.jsonl"
        assert steps_file.exists()
        lines = steps_file.read_text().strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["step"] == 0
        assert record["state_type"] == "menu"


def test_write_step_appends_multiple() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "run"
        tw = TraceWriter(run_dir)
        for i in range(3):
            tw.write_step(_make_step(i))
        lines = (run_dir / "steps.jsonl").read_text().strip().splitlines()
        assert len(lines) == 3
        assert json.loads(lines[2])["step"] == 2


# ── TraceWriter: write_subagent ────────────────────────────────────────────


def _make_subagent_record() -> SubagentRecord:
    return SubagentRecord(
        agent_role="skill_creator",
        timestamp="2026-05-10T14:24:00",
        trigger_reason="state_type_transition",
        input_summary="left combat",
        llm_request_messages=[],
        llm_response_message={},
        llm_usage={"input_tokens": 20, "output_tokens": 10},
        file_diff_summary="created skill ironclad_combat.md",
    )


def test_write_subagent_creates_subagent_jsonl() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "run"
        tw = TraceWriter(run_dir)
        tw.write_subagent(_make_subagent_record())
        sub_file = run_dir / "subagent.jsonl"
        assert sub_file.exists()
        record = json.loads(sub_file.read_text().strip())
        assert record["agent_role"] == "skill_creator"
        assert record["file_diff_summary"] == "created skill ironclad_combat.md"


# ── TraceWriter: write_summary ─────────────────────────────────────────────


def _make_tracker_with_main(input_tokens: int = 100, output_tokens: int = 50) -> UsageTracker:
    tracker = UsageTracker()
    tracker.record("main", "google/gemini-2.5-flash", Usage(input_tokens=input_tokens, output_tokens=output_tokens))
    tracker.record("main", "google/gemini-2.5-flash", Usage(input_tokens=10, output_tokens=5))
    return tracker


@pytest.mark.parametrize("reason", ["game_over", "loop_terminated", "error"])
def test_summary_has_all_three_roles(reason: TerminationReason) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "run"
        tw = TraceWriter(run_dir)
        tracker = _make_tracker_with_main()
        tw.write_summary(termination_reason=reason, tracker=tracker)
        summary = json.loads((run_dir / "summary.json").read_text())
        assert summary["termination_reason"] == reason
        tokens = summary["tokens"]
        for role in ("main", "skill_creator", "oracle_updater"):
            assert role in tokens, f"role {role!r} missing from tokens"
            assert "input_tokens" in tokens[role]
            assert "output_tokens" in tokens[role]
            assert "calls" in tokens[role]


def test_summary_unused_roles_have_zero_tokens() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "run"
        tw = TraceWriter(run_dir)
        tracker = _make_tracker_with_main()
        tw.write_summary(termination_reason="game_over", tracker=tracker)
        summary = json.loads((run_dir / "summary.json").read_text())
        tokens = summary["tokens"]
        assert tokens["skill_creator"]["input_tokens"] == 0
        assert tokens["skill_creator"]["output_tokens"] == 0
        assert tokens["skill_creator"]["calls"] == 0
        assert tokens["oracle_updater"]["calls"] == 0


def test_summary_main_tokens_accumulate_correctly() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "run"
        tw = TraceWriter(run_dir)
        tracker = UsageTracker()
        tracker.record("main", "google/gemini-2.5-flash", Usage(input_tokens=100, output_tokens=50))
        tracker.record("main", "google/gemini-2.5-flash", Usage(input_tokens=200, output_tokens=100))
        tw.write_summary(termination_reason="game_over", tracker=tracker)
        summary = json.loads((run_dir / "summary.json").read_text())
        main = summary["tokens"]["main"]
        assert main["input_tokens"] == 300
        assert main["output_tokens"] == 150
        assert main["calls"] == 2


def test_summary_call_counts_by_role() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "run"
        tw = TraceWriter(run_dir)
        tracker = UsageTracker()
        for _ in range(5):
            tracker.record("main", "google/gemini-2.5-flash", Usage(input_tokens=10, output_tokens=5))
        tw.write_summary(termination_reason="game_over", tracker=tracker)
        summary = json.loads((run_dir / "summary.json").read_text())
        assert summary["tokens"]["main"]["calls"] == 5


def test_summary_extra_fields_merged() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "run"
        tw = TraceWriter(run_dir)
        tracker = UsageTracker()
        tw.write_summary(
            termination_reason="loop_terminated",
            tracker=tracker,
            extra={"loop_detail": {"action": "end_turn", "count": 4}},
        )
        summary = json.loads((run_dir / "summary.json").read_text())
        assert "loop_detail" in summary
        assert summary["loop_detail"]["count"] == 4


def test_summary_has_usage_detail() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "run"
        tw = TraceWriter(run_dir)
        tracker = _make_tracker_with_main()
        tw.write_summary(termination_reason="game_over", tracker=tracker)
        summary = json.loads((run_dir / "summary.json").read_text())
        assert "usage_detail" in summary
