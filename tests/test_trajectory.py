"""Tests for F-013 deterministic trajectory reconstruction."""

from __future__ import annotations

import json
from pathlib import Path

from slay2agent.memory.trajectory import (
    load_steps,
    reconstruct_trajectory,
)


def _write_steps(path: Path, records: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )


def _step(step: int, **over) -> dict:
    base = {
        "step": step,
        "state_type": "combat",
        "l0_cleared": False,
        "tool_name": "play_card",
        "tool_args": {"index": 0},
        "action_feedback": None,
        "tool_result_state_type": "combat",
        "settled_state_summary": f"state after {step}",
    }
    base.update(over)
    return base


# ── load_steps ──────────────────────────────────────────────────────────────


def test_load_steps_missing_file(tmp_path):
    assert load_steps(tmp_path / "nope.jsonl") == []


def test_load_steps_projects_fields(tmp_path):
    p = tmp_path / "steps.jsonl"
    _write_steps(p, [_step(0, tool_name="menu_select", tool_args={"option": "IRONCLAD"})])
    steps = load_steps(p)
    assert len(steps) == 1
    s = steps[0]
    assert s.step == 0
    assert s.tool_name == "menu_select"
    assert s.tool_args == {"option": "IRONCLAD"}
    assert s.settled_state_summary == "state after 0"


def test_load_steps_tolerates_missing_action_feedback(tmp_path):
    """Traces written before F-013 have no action_feedback key."""
    p = tmp_path / "steps.jsonl"
    legacy = _step(0)
    del legacy["action_feedback"]
    _write_steps(p, [legacy])
    steps = load_steps(p)
    assert steps[0].action_feedback is None


def test_load_steps_skips_malformed_lines(tmp_path):
    p = tmp_path / "steps.jsonl"
    good = json.dumps(_step(0), ensure_ascii=False)
    p.write_text(good + "\n{not valid json\n" + json.dumps(_step(1)) + "\n", encoding="utf-8")
    steps = load_steps(p)
    # Both valid lines survive; the broken one is skipped.
    assert [s.step for s in steps] == [0, 1]


# ── reconstruct_trajectory ────────────────────────────────────────────────────


def test_reconstruct_empty_file_returns_empty(tmp_path):
    p = tmp_path / "steps.jsonl"
    p.write_text("", encoding="utf-8")
    assert reconstruct_trajectory(p) == ""


def test_reconstruct_contains_action_result_and_state(tmp_path):
    p = tmp_path / "steps.jsonl"
    _write_steps(p, [_step(0, tool_name="menu_select", tool_args={"option": "IRONCLAD"})])
    out = reconstruct_trajectory(p)
    assert "step 0" in out
    assert 'menu_select({"option": "IRONCLAD"})' in out
    assert "RESULT → state=combat" in out
    assert "state after 0" in out


def test_reconstruct_shows_feedback_only_when_present(tmp_path):
    p = tmp_path / "steps.jsonl"
    _write_steps(p, [
        _step(0),  # no feedback
        _step(1, action_feedback="ERROR: rejected: not in play phase"),
    ])
    out = reconstruct_trajectory(p)
    # exactly one FEEDBACK line (step 1 only)
    assert out.count("FEEDBACK:") == 1
    assert "not in play phase" in out


def test_reconstruct_marks_screen_boundary(tmp_path):
    p = tmp_path / "steps.jsonl"
    _write_steps(p, [_step(0), _step(1, l0_cleared=True, state_type="map")])
    out = reconstruct_trajectory(p)
    assert "[screen boundary]" in out
    # boundary only on the cleared step
    assert out.count("[screen boundary]") == 1


def test_reconstruct_handles_no_action_step(tmp_path):
    p = tmp_path / "steps.jsonl"
    _write_steps(p, [_step(0, tool_name=None, tool_args=None, state_type="game_over")])
    out = reconstruct_trajectory(p)
    assert "(no action)" in out


def test_reconstruct_is_deterministic(tmp_path):
    p = tmp_path / "steps.jsonl"
    records = [_step(0), _step(1, action_feedback="ERROR: x"), _step(2, l0_cleared=True)]
    _write_steps(p, records)
    assert reconstruct_trajectory(p) == reconstruct_trajectory(p)
