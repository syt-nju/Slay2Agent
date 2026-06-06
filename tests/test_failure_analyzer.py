"""Tests for F-013 phase 1 failure analyzer."""

from __future__ import annotations

import json

import pytest
from unittest.mock import MagicMock

from slay2agent.llm.protocol import LLMResponse, Message, ToolCall, Usage
from slay2agent.llm.usage import UsageTracker
from slay2agent.maintenance.failure_analyzer import _normalize_failures, analyze_run


def _tool_response(args, *, name: str = "submit_failure_report") -> LLMResponse:
    tc = ToolCall(id="tc1", name=name, arguments=args)
    return LLMResponse(
        message=Message(role="assistant", tool_calls=[tc]),
        usage=Usage(input_tokens=30, output_tokens=12),
        stop_reason="tool_calls",
        model="mock-model",
        raw={},
    )


def _adapter(*responses) -> MagicMock:
    a = MagicMock()
    a.chat.side_effect = list(responses)
    return a


# ── _normalize_failures ───────────────────────────────────────────────────────


def test_normalize_failures_coerces_and_filters():
    raw = [
        {"summary": " too greedy ", "detail": "x", "step_range": [3, 5], "excerpt": "e"},
        {"summary": "bad range", "detail": "y", "step_range": ["a", 2]},  # invalid range → []
        "not a dict",  # dropped
    ]
    out = _normalize_failures(raw)
    assert len(out) == 2
    assert out[0] == {"summary": "too greedy", "detail": "x", "step_range": [3, 5], "excerpt": "e"}
    assert out[1]["step_range"] == []
    assert out[1]["excerpt"] == ""


def test_normalize_failures_non_list_returns_empty():
    assert _normalize_failures(None) == []
    assert _normalize_failures("x") == []


# ── analyze_run ───────────────────────────────────────────────────────────────


def test_analyze_run_returns_normalized_report():
    args = {
        "overall_review": "  decent but reckless  ",
        "failures": [
            {"summary": "Overcommitted attacks", "detail": "no block", "step_range": [4, 6], "excerpt": "..."},
        ],
    }
    tracker = UsageTracker()
    report = analyze_run(
        "TRAJECTORY",
        run_id="run42",
        termination_reason="game_over",
        total_steps=15,
        adapter=_adapter(_tool_response(args)),
        tracker=tracker,
        model="mock-model",
    )
    assert report["run_id"] == "run42"
    assert report["termination_reason"] == "game_over"
    assert report["total_steps"] == 15
    assert report["overall_review"] == "decent but reckless"
    assert report["failures"][0]["summary"] == "Overcommitted attacks"
    assert report["llm_usage"] == {"input_tokens": 30, "output_tokens": 12}
    # token usage recorded under the failure_analyzer role
    assert tracker.role_totals()["failure_analyzer"].input_tokens == 30


def test_analyze_run_accepts_json_string_arguments():
    args = {"overall_review": "ok", "failures": []}
    report = analyze_run(
        "T",
        run_id="r",
        termination_reason="unknown",
        total_steps=1,
        adapter=_adapter(_tool_response(json.dumps(args))),
        tracker=UsageTracker(),
        model="m",
    )
    assert report["failures"] == []
    assert report["overall_review"] == "ok"


def test_analyze_run_raises_when_no_report_tool_call():
    # Model called the wrong tool → no usable report.
    with pytest.raises(ValueError):
        analyze_run(
            "T",
            run_id="r",
            termination_reason="unknown",
            total_steps=1,
            adapter=_adapter(_tool_response({"x": 1}, name="some_other_tool")),
            tracker=UsageTracker(),
            model="m",
        )
