"""Tests for F-008b: SkillRegistry write/delete + skill_creator sub-agent runner."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from slay2agent.agent.skill_creator import run_skill_creator
from slay2agent.agent.trace import TraceWriter
from slay2agent.llm.protocol import LLMResponse, Message, ToolCall, Usage
from slay2agent.llm.usage import UsageTracker
from slay2agent.memory.skill_registry import SkillRegistry


# ── SkillRegistry.write_skill / delete_skill ──────────────────────────────────


def test_write_skill_creates_file(tmp_path):
    skills_dir = tmp_path / "skills"
    reg = SkillRegistry(skills_dir)

    reg.write_skill(
        skill_id="test_skill",
        name="Test Skill",
        description="A test skill. Use when testing.",
        body="# Test Skill\n\nDo the thing.",
    )

    path = skills_dir / "test_skill.md"
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "name: Test Skill" in content
    assert "description: A test skill. Use when testing." in content
    assert "Do the thing." in content


def test_write_skill_creates_parent_dir(tmp_path):
    skills_dir = tmp_path / "nested" / "skills"
    reg = SkillRegistry(skills_dir)
    reg.write_skill("x", "X", "X skill. Use always.", "body")
    assert (skills_dir / "x.md").exists()


def test_write_skill_overwrites_existing(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    reg = SkillRegistry(skills_dir)
    reg.write_skill("s", "Old name", "Old desc. Use always.", "old body")
    reg.write_skill("s", "New name", "New desc. Use always.", "new body")

    skill = reg.read_skill("s")
    assert skill is not None
    assert skill.name == "New name"
    assert skill.description == "New desc. Use always."
    assert "new body" in skill.body


def test_write_skill_invalidates_cache(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    reg = SkillRegistry(skills_dir)

    assert reg.list_skills() == []

    reg.write_skill("z", "Z", "Z skill. Use whenever.", "body z")

    metas = reg.list_skills()
    assert len(metas) == 1
    assert metas[0].skill_id == "z"


def test_delete_skill_removes_file(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    reg = SkillRegistry(skills_dir)
    reg.write_skill("del_me", "Del", "Desc. Use always.", "body")

    result = reg.delete_skill("del_me")
    assert result is True
    assert not (skills_dir / "del_me.md").exists()


def test_delete_skill_returns_false_if_not_found(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    reg = SkillRegistry(skills_dir)
    assert reg.delete_skill("ghost") is False


def test_delete_skill_invalidates_cache(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    reg = SkillRegistry(skills_dir)
    reg.write_skill("to_del", "D", "D skill. Use always.", "body")

    assert len(reg.list_skills()) == 1

    reg.delete_skill("to_del")

    assert reg.list_skills() == []


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_usage(inp: int = 10, out: int = 5) -> Usage:
    return Usage(input_tokens=inp, output_tokens=out)


def _make_tool_response(name: str, args: dict, call_id: str = "tc1") -> LLMResponse:
    """Fake LLM response that issues a single tool call."""
    tc = ToolCall(id=call_id, name=name, arguments=args)
    msg = Message(role="assistant", content=None, tool_calls=[tc])
    return LLMResponse(
        message=msg,
        usage=_make_usage(),
        stop_reason="tool_use",
        model="mock-model",
        raw={},
    )


def _make_text_response(text: str = "no changes needed") -> LLMResponse:
    """Fake LLM response that returns plain text (terminal state)."""
    msg = Message(role="assistant", content=text)
    return LLMResponse(
        message=msg,
        usage=_make_usage(),
        stop_reason="stop",
        model="mock-model",
        raw={},
    )


def _make_l0(n: int = 2) -> list[Message]:
    msgs = []
    for i in range(n):
        msgs.append(Message(role="user", content=f"step {i}"))
    return msgs


def _run(
    tmp_path: Path,
    adapter_responses: list[LLMResponse],
    l0: list[Message] | None = None,
    prev_state_type: str = "combat",
    new_state_type: str = "map",
) -> tuple[SkillRegistry, UsageTracker, Path]:
    """Helper: wire up minimal infra and call run_skill_creator."""
    skills_dir = tmp_path / "skills"
    oracle_path = tmp_path / "oracle.md"
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)

    reg = SkillRegistry(skills_dir)
    tracker = UsageTracker()
    trace = TraceWriter(run_dir)

    adapter = MagicMock()
    adapter.chat.side_effect = adapter_responses

    run_skill_creator(
        prev_l0=l0 or _make_l0(),
        skill_registry=reg,
        oracle_path=oracle_path,
        adapter=adapter,
        tracker=tracker,
        trace=trace,
        model="mock-model",
        prev_state_type=prev_state_type,
        new_state_type=new_state_type,
    )

    return reg, tracker, run_dir


# ── run_skill_creator: happy-path full flow ────────────────────────────────────


def test_full_flow_list_read_write_text(tmp_path):
    """LLM calls list_skills → read_skill → write_skill → text response."""
    responses = [
        _make_tool_response("list_skills", {}, "tc1"),
        _make_tool_response("read_skill", {"skill_id": "nonexistent"}, "tc2"),
        _make_tool_response(
            "write_skill",
            {
                "skill_id": "new_skill",
                "name": "New Skill",
                "description": "A new insight. Use at the start of combat.",
                "body": "# New Skill\n\nAlways do X.",
            },
            "tc3",
        ),
        _make_text_response("Created new_skill with combat tip."),
    ]

    reg, tracker, run_dir = _run(tmp_path, responses)

    # Skill was written.
    skill = reg.read_skill("new_skill")
    assert skill is not None
    assert "Always do X." in skill.body

    # SubagentRecord was written.
    subagent_log = run_dir / "subagent.jsonl"
    assert subagent_log.exists()
    record = json.loads(subagent_log.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert record["agent_role"] == "skill_creator"
    assert "write_skill(new_skill)" in record["file_diff_summary"]

    # UsageTracker has skill_creator entries.
    totals = tracker.role_totals()
    assert "skill_creator" in totals


def test_no_op_flow_text_only(tmp_path):
    """LLM calls list_skills then immediately returns text — no file changes."""
    responses = [
        _make_tool_response("list_skills", {}, "tc1"),
        _make_text_response("no changes needed"),
    ]

    reg, tracker, run_dir = _run(tmp_path, responses)

    # No skills written.
    assert reg.list_skills() == []

    # Record still written with "no changes".
    subagent_log = run_dir / "subagent.jsonl"
    assert subagent_log.exists()
    record = json.loads(subagent_log.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert record["file_diff_summary"] == "no changes"


def test_delete_skill_flow(tmp_path):
    """LLM deletes an existing skill."""
    # Pre-populate a skill file.
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    reg = SkillRegistry(skills_dir)
    reg.write_skill("old_skill", "Old", "Old skill. Use always.", "outdated body")

    responses = [
        _make_tool_response("list_skills", {}, "tc1"),
        _make_tool_response("delete_skill", {"skill_id": "old_skill"}, "tc2"),
        _make_text_response("Deleted old_skill."),
    ]

    tracker = UsageTracker()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    trace = TraceWriter(run_dir)
    adapter = MagicMock()
    adapter.chat.side_effect = responses

    run_skill_creator(
        prev_l0=_make_l0(),
        skill_registry=reg,
        oracle_path=tmp_path / "oracle.md",
        adapter=adapter,
        tracker=tracker,
        trace=trace,
        model="mock-model",
        prev_state_type="combat",
        new_state_type="map",
    )

    assert reg.read_skill("old_skill") is None
    subagent_log = run_dir / "subagent.jsonl"
    record = json.loads(subagent_log.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert "delete_skill(old_skill)" in record["file_diff_summary"]


# ── run_skill_creator: failure / safety ────────────────────────────────────────


def test_failure_does_not_propagate(tmp_path):
    """If the adapter raises, run_skill_creator swallows it and does not raise."""
    adapter = MagicMock()
    adapter.chat.side_effect = RuntimeError("LLM exploded")

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    trace = TraceWriter(run_dir)
    reg = SkillRegistry(tmp_path / "skills")
    tracker = UsageTracker()

    # Must not raise.
    run_skill_creator(
        prev_l0=_make_l0(),
        skill_registry=reg,
        oracle_path=tmp_path / "oracle.md",
        adapter=adapter,
        tracker=tracker,
        trace=trace,
        model="mock-model",
        prev_state_type="combat",
        new_state_type="map",
    )

    # Best-effort trace should still be written with ERROR marker.
    subagent_log = run_dir / "subagent.jsonl"
    assert subagent_log.exists()
    record = json.loads(subagent_log.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert "ERROR" in record["file_diff_summary"]


def test_max_steps_protection(tmp_path):
    """If LLM never returns text, loop stops after max_steps."""
    # Infinite list_skills tool calls.
    infinite_tc = _make_tool_response("list_skills", {}, "tc1")
    responses = [infinite_tc] * 20  # more than max_steps

    adapter = MagicMock()
    adapter.chat.side_effect = responses

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    trace = TraceWriter(run_dir)
    reg = SkillRegistry(tmp_path / "skills")
    tracker = UsageTracker()

    # Should return without raising even though LLM never terminates.
    run_skill_creator(
        prev_l0=_make_l0(),
        skill_registry=reg,
        oracle_path=tmp_path / "oracle.md",
        adapter=adapter,
        tracker=tracker,
        trace=trace,
        model="mock-model",
        prev_state_type="combat",
        new_state_type="map",
        max_steps=5,
    )

    # Only 5 adapter calls should have been made.
    assert adapter.chat.call_count == 5


# ── UsageTracker role accounting ───────────────────────────────────────────────


def test_usage_tracker_records_skill_creator_role(tmp_path):
    """All LLM calls are bucketed under 'skill_creator'."""
    responses = [
        _make_tool_response("list_skills", {}, "tc1"),
        _make_text_response("done"),
    ]

    _, tracker, _ = _run(tmp_path, responses)

    totals = tracker.role_totals()
    assert "skill_creator" in totals
    # Two calls: each with 10 input + 5 output = 20 + 10 total.
    assert totals["skill_creator"].input_tokens == 20
    assert totals["skill_creator"].output_tokens == 10


# ── Empty L0 edge-case ─────────────────────────────────────────────────────────


def test_empty_l0_still_runs(tmp_path):
    """run_skill_creator with an empty L0 should not crash."""
    responses = [_make_text_response("no changes needed")]
    _run(tmp_path, responses, l0=[])
