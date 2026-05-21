"""Tests for skill_librarian sub-agent (run-end library merge pass)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from slay2agent.agent.skill_librarian import run_skill_librarian
from slay2agent.agent.trace import TraceWriter
from slay2agent.llm.protocol import LLMResponse, Message, ToolCall, Usage
from slay2agent.llm.usage import UsageTracker
from slay2agent.memory.skill_registry import SkillRegistry


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


def _make_text_response(text: str = "no merges needed") -> LLMResponse:
    """Fake LLM response that returns plain text (implicit done)."""
    msg = Message(role="assistant", content=text)
    return LLMResponse(
        message=msg,
        usage=_make_usage(),
        stop_reason="stop",
        model="mock-model",
        raw={},
    )


def _setup_registry(tmp_path: Path, skills: dict[str, tuple[str, str, str]]) -> SkillRegistry:
    """Create a SkillRegistry with pre-populated skills.

    skills: {skill_id: (name, description, body)}
    """
    skills_dir = tmp_path / "skills"
    reg = SkillRegistry(skills_dir)
    for skill_id, (name, desc, body) in skills.items():
        reg.write_skill(skill_id, name, desc, body)
    return reg


def _run(
    tmp_path: Path,
    adapter_responses: list[LLMResponse],
    skills: dict[str, tuple[str, str, str]] | None = None,
    max_steps: int = 100,
) -> tuple[SkillRegistry, UsageTracker, Path]:
    """Helper: wire up minimal infra and call run_skill_librarian."""
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)

    reg = _setup_registry(tmp_path, skills or {})
    tracker = UsageTracker()
    trace = TraceWriter(run_dir)

    adapter = MagicMock()
    adapter.chat.side_effect = adapter_responses

    run_skill_librarian(
        skill_registry=reg,
        adapter=adapter,
        tracker=tracker,
        trace=trace,
        model="mock-model",
        max_steps=max_steps,
    )

    return reg, tracker, run_dir


# ── Tests: done tool (explicit termination) ──────────────────────────────────


def test_done_no_merges_needed(tmp_path):
    """LLM calls done immediately — no merges needed."""
    skills = {
        "combat": ("Combat", "Combat strategies. Use in combat.", "# Combat\nFight."),
        "map": ("Map", "Map navigation. Use at map.", "# Map\nNavigate."),
    }
    responses = [
        _make_tool_response("done", {"summary": "no merges needed"}, "tc1"),
    ]
    reg, tracker, run_dir = _run(tmp_path, responses, skills)

    # Both skills still exist, unchanged.
    metas = reg.list_skills()
    assert len(metas) == 2
    assert {m.skill_id for m in metas} == {"combat", "map"}

    # Trace was written.
    subagent_path = run_dir / "subagent.jsonl"
    assert subagent_path.exists()
    lines = subagent_path.read_text().strip().split("\n")
    record = json.loads(lines[0])
    assert record["agent_role"] == "skill_librarian"
    assert record["file_diff_summary"] == "no merges"


def test_done_after_merge(tmp_path):
    """LLM reads two skills, merges them, then calls done."""
    skills = {
        "combat_basics": ("Combat Basics", "Basic combat. Use in early fights.", "# Combat Basics\nDo basic."),
        "early_combat": ("Early Combat", "Early combat tips. Use in act 1.", "# Early Combat\nAct 1 tip."),
        "map_nav": ("Map Nav", "Map navigation. Use at map.", "# Map Nav\nNavigate."),
    }
    responses = [
        # Read both candidates
        _make_tool_response("read_skill", {"skill_id": "combat_basics"}, "tc1"),
        _make_tool_response("read_skill", {"skill_id": "early_combat"}, "tc2"),
        # Merge: write to target
        _make_tool_response(
            "write_skill",
            {
                "skill_id": "combat_basics",
                "name": "Combat Basics",
                "description": "Combat strategies for early fights. Use in act 1 combat encounters.",
                "body": "# Combat Basics\n\nDo basic plus act 1 tips.",
            },
            "tc3",
        ),
        # Delete source
        _make_tool_response("delete_skill", {"skill_id": "early_combat"}, "tc4"),
        # Done
        _make_tool_response("done", {"summary": "Merged early_combat into combat_basics"}, "tc5"),
    ]
    reg, tracker, run_dir = _run(tmp_path, responses, skills)

    # early_combat should be gone, combat_basics should have merged content.
    metas = reg.list_skills()
    skill_ids = {m.skill_id for m in metas}
    assert "early_combat" not in skill_ids
    assert "combat_basics" in skill_ids
    assert "map_nav" in skill_ids
    assert len(metas) == 2

    # Verify merged content.
    skill = reg.read_skill("combat_basics")
    assert skill is not None
    assert "act 1" in skill.description.lower()

    # Trace recorded file changes.
    subagent_path = run_dir / "subagent.jsonl"
    record = json.loads(subagent_path.read_text().strip().split("\n")[0])
    assert "write_skill(combat_basics)" in record["file_diff_summary"]
    assert "delete_skill(early_combat)" in record["file_diff_summary"]


# ── Tests: text-only response (implicit done) ────────────────────────────────


def test_text_response_treated_as_done(tmp_path):
    """LLM returns text without calling done — treated as implicit completion."""
    skills = {
        "a": ("A", "A skill. Use when A.", "# A\nContent A."),
    }
    responses = [
        _make_text_response("no merges needed"),
    ]
    reg, tracker, run_dir = _run(tmp_path, responses, skills)

    # Skill unchanged.
    assert len(reg.list_skills()) == 1

    # Trace written.
    subagent_path = run_dir / "subagent.jsonl"
    assert subagent_path.exists()


# ── Tests: max_steps safety limit ────────────────────────────────────────────


def test_max_steps_reached_without_done(tmp_path):
    """If max_steps is hit without done, terminates gracefully."""
    skills = {
        "x": ("X", "X skill. Use when X.", "# X\nX body."),
        "y": ("Y", "Y skill. Use when Y.", "# Y\nY body."),
    }
    # Return read_skill responses forever (never calls done).
    responses = [
        _make_tool_response("read_skill", {"skill_id": "x"}, f"tc{i}")
        for i in range(5)
    ]
    reg, tracker, run_dir = _run(tmp_path, responses, skills, max_steps=5)

    # Both skills should still exist (no merge happened).
    assert len(reg.list_skills()) == 2

    # Trace still written.
    subagent_path = run_dir / "subagent.jsonl"
    assert subagent_path.exists()


# ── Tests: fire-and-forget (exception safety) ────────────────────────────────


def test_exception_does_not_propagate(tmp_path):
    """Any exception inside run_skill_librarian is swallowed."""
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)

    skills_dir = tmp_path / "skills"
    reg = SkillRegistry(skills_dir)
    reg.write_skill("s1", "S1", "S1 desc. Use always.", "# S1\nBody.")
    tracker = UsageTracker()
    trace = TraceWriter(run_dir)

    adapter = MagicMock()
    adapter.chat.side_effect = RuntimeError("LLM is down")

    # Should NOT raise.
    run_skill_librarian(
        skill_registry=reg,
        adapter=adapter,
        tracker=tracker,
        trace=trace,
        model="mock-model",
    )

    # Skill unchanged.
    assert len(reg.list_skills()) == 1


# ── Tests: token tracking ─────────────────────────────────────────────────────


def test_tokens_recorded_under_skill_librarian_role(tmp_path):
    """Usage is recorded under the 'skill_librarian' role."""
    skills = {
        "a": ("A", "Desc A. Use when A.", "# A\nA body."),
    }
    responses = [
        _make_tool_response("done", {"summary": "no merges needed"}, "tc1"),
    ]
    reg, tracker, run_dir = _run(tmp_path, responses, skills)

    totals = tracker.role_totals()
    assert "skill_librarian" in totals
    u = totals["skill_librarian"]
    assert u.input_tokens == 10
    assert u.output_tokens == 5
