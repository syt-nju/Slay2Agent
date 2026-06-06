"""Tests for F-013 phase 1 orchestration (analyze_runs)."""

from __future__ import annotations

import json
from pathlib import Path

from unittest.mock import MagicMock

from slay2agent.llm.protocol import LLMResponse, Message, ToolCall, Usage
from slay2agent.llm.usage import UsageTracker
from slay2agent.maintenance.analyze import analyze_runs
from slay2agent.maintenance.report import has_report, read_report


def _tool_response(args: dict) -> LLMResponse:
    tc = ToolCall(id="tc1", name="submit_failure_report", arguments=args)
    return LLMResponse(
        message=Message(role="assistant", tool_calls=[tc]),
        usage=Usage(input_tokens=10, output_tokens=4),
        stop_reason="tool_calls",
        model="mock-model",
        raw={},
    )


def _make_run(runs_dir: Path, name: str, *, steps: int = 2, termination: str = "game_over") -> Path:
    d = runs_dir / name
    d.mkdir(parents=True)
    lines = [
        json.dumps({
            "step": i,
            "state_type": "combat",
            "l0_cleared": False,
            "tool_name": "play_card",
            "tool_args": {"index": i},
            "action_feedback": None,
            "tool_result_state_type": "combat",
            "settled_state_summary": f"state {i}",
        })
        for i in range(steps)
    ]
    (d / "steps.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (d / "summary.json").write_text(json.dumps({"termination_reason": termination}), encoding="utf-8")
    return d


def _default_report() -> dict:
    return {"overall_review": "ok", "failures": [{"summary": "s", "detail": "d", "step_range": [0, 1], "excerpt": "e"}]}


def test_analyze_runs_writes_reports_for_unanalyzed(tmp_path):
    runs = tmp_path / "runs"
    _make_run(runs, "run_a")
    _make_run(runs, "run_b")
    adapter = MagicMock()
    adapter.chat.side_effect = [_tool_response(_default_report()), _tool_response(_default_report())]
    tracker = UsageTracker()

    result = analyze_runs(runs, adapter, tracker, model="m", min_steps=0)

    assert sorted(result.analyzed) == ["run_a", "run_b"]
    for name in ("run_a", "run_b"):
        report = read_report(runs / name)
        assert report["run_id"] == name
        assert report["total_steps"] == 2
        assert report["termination_reason"] == "game_over"


def test_analyze_runs_skips_already_analyzed(tmp_path):
    runs = tmp_path / "runs"
    a = _make_run(runs, "run_a")
    (a / "failure_report.json").write_text(json.dumps({"run_id": "run_a"}), encoding="utf-8")
    _make_run(runs, "run_b")
    adapter = MagicMock()
    adapter.chat.side_effect = [_tool_response(_default_report())]
    tracker = UsageTracker()

    result = analyze_runs(runs, adapter, tracker, model="m", min_steps=0)

    assert result.analyzed == ["run_b"]
    assert adapter.chat.call_count == 1  # run_a untouched


def test_analyze_runs_force_reanalyzes_all(tmp_path):
    runs = tmp_path / "runs"
    a = _make_run(runs, "run_a")
    (a / "failure_report.json").write_text(json.dumps({"run_id": "stale"}), encoding="utf-8")
    adapter = MagicMock()
    adapter.chat.side_effect = [_tool_response(_default_report())]
    tracker = UsageTracker()

    result = analyze_runs(runs, adapter, tracker, model="m", force=True, min_steps=0)

    assert result.analyzed == ["run_a"]
    # overwritten with fresh content
    assert read_report(a)["run_id"] == "run_a"


def test_analyze_runs_one_failure_does_not_abort_batch(tmp_path):
    runs = tmp_path / "runs"
    _make_run(runs, "run_a")
    _make_run(runs, "run_b")

    # First run raises inside analyze_run (no valid tool call), second succeeds.
    bad = LLMResponse(
        message=Message(role="assistant", tool_calls=[ToolCall(id="x", name="wrong", arguments={})]),
        usage=Usage(), stop_reason="tool_calls", model="m", raw={},
    )
    adapter = MagicMock()
    adapter.chat.side_effect = [bad, _tool_response(_default_report())]
    tracker = UsageTracker()

    result = analyze_runs(runs, adapter, tracker, model="m", min_steps=0)

    assert result.analyzed == ["run_b"]
    assert result.failed == ["run_a"]
    assert not has_report(runs / "run_a")
    assert has_report(runs / "run_b")


def test_analyze_runs_skips_short_runs(tmp_path):
    runs = tmp_path / "runs"
    _make_run(runs, "short", steps=3)
    _make_run(runs, "long", steps=12)
    adapter = MagicMock()
    adapter.chat.side_effect = [_tool_response(_default_report())]
    tracker = UsageTracker()

    result = analyze_runs(runs, adapter, tracker, model="m", min_steps=10)

    assert result.analyzed == ["long"]
    assert result.skipped_short == 1
    assert not has_report(runs / "short")
    assert has_report(runs / "long")
    assert adapter.chat.call_count == 1
