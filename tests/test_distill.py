"""Tests for F-013 phase 2 distillation (clustering + per-cluster decisions)."""

from __future__ import annotations

import json
from pathlib import Path

from unittest.mock import MagicMock

from slay2agent.llm.protocol import LLMResponse, Message, ToolCall, Usage
from slay2agent.llm.usage import UsageTracker
from slay2agent.maintenance.distill import (
    DistillResult,
    _apply_decision,
    cluster_failures,
    collect_failures,
    distill_runs,
    resolve_cluster,
)
from slay2agent.maintenance.report import read_report, write_report
from slay2agent.memory.skill_registry import SkillRegistry


def _tool_resp(name: str, args: dict) -> LLMResponse:
    tc = ToolCall(id="tc1", name=name, arguments=args)
    return LLMResponse(
        message=Message(role="assistant", tool_calls=[tc]),
        usage=Usage(input_tokens=8, output_tokens=3),
        stop_reason="tool_calls",
        model="mock-model",
        raw={},
    )


def _adapter(*responses) -> MagicMock:
    a = MagicMock()
    a.chat.side_effect = list(responses)
    return a


def _make_report_run(runs_dir: Path, name: str, failures: list[dict]) -> Path:
    d = runs_dir / name
    d.mkdir(parents=True)
    (d / "steps.jsonl").write_text('{"step": 0}\n', encoding="utf-8")
    write_report(d, {"run_id": name, "failures": failures})
    return d


def _failure(summary: str, detail: str = "d", excerpt: str = "e") -> dict:
    return {"summary": summary, "detail": detail, "step_range": [0, 1], "excerpt": excerpt}


# ── collect_failures ──────────────────────────────────────────────────────────


def test_collect_failures_flattens_with_run_id(tmp_path):
    a = _make_report_run(tmp_path, "a", [_failure("x"), _failure("y")])
    b = _make_report_run(tmp_path, "b", [_failure("z")])
    failures = collect_failures([a, b])
    assert [f["summary"] for f in failures] == ["x", "y", "z"]
    assert failures[0]["run_id"] == "a"
    assert failures[2]["run_id"] == "b"


# ── cluster_failures ──────────────────────────────────────────────────────────


def test_cluster_failures_groups_and_filters_bad_indices():
    failures = [_failure("a"), _failure("b"), _failure("c")]
    for i, f in enumerate(failures):
        f["run_id"] = f"r{i}"
    resp = _tool_resp("submit_clusters", {
        "clusters": [
            {"failure_reason": "root1", "member_indices": [0, 1, 99]},  # 99 dropped
            {"failure_reason": "empty", "member_indices": [42]},          # all invalid → dropped
        ]
    })
    clusters = cluster_failures(failures, _adapter(resp), UsageTracker())
    assert len(clusters) == 1
    assert clusters[0]["failure_reason"] == "root1"
    assert len(clusters[0]["members"]) == 2


def test_cluster_failures_empty_input_no_llm_call():
    adapter = _adapter()
    assert cluster_failures([], adapter, UsageTracker()) == []
    adapter.chat.assert_not_called()


# ── resolve_cluster ───────────────────────────────────────────────────────────


def _cluster(reason: str, n: int = 2) -> dict:
    return {
        "failure_reason": reason,
        "members": [{"run_id": f"r{i}", "summary": "s", "detail": "d", "excerpt": "e"} for i in range(n)],
    }


def test_resolve_cluster_create_decision(tmp_path):
    reg = SkillRegistry(tmp_path / "skills")
    decision_resp = _tool_resp("submit_decision", {
        "action": "create", "skill_id": "block_first", "name": "Block First",
        "failure_reason": "dies to aoe", "description": "Use vs multi-hit", "body": "# Block First\nblock",
    })
    out = resolve_cluster(_cluster("dies to aoe"), reg, _adapter(decision_resp), UsageTracker())
    assert out["action"] == "create"
    assert out["skill_id"] == "block_first"


def test_resolve_cluster_reads_skill_before_deciding(tmp_path):
    reg = SkillRegistry(tmp_path / "skills")
    reg.write_skill("combat", "Combat", "Use in combat", "# Combat\nold body", failure_reason="reckless")
    reg.reload()

    read_resp = _tool_resp("read_skill", {"skill_id": "combat"})
    decide_resp = _tool_resp("submit_decision", {
        "action": "improve", "skill_id": "combat", "name": "Combat",
        "failure_reason": "reckless", "description": "Use in combat", "body": "# Combat\nnew body",
    })
    adapter = _adapter(read_resp, decide_resp)
    out = resolve_cluster(_cluster("reckless"), reg, adapter, UsageTracker())
    assert out["action"] == "improve"
    assert adapter.chat.call_count == 2  # read_skill round, then decision


def test_resolve_cluster_no_decision_returns_none(tmp_path):
    reg = SkillRegistry(tmp_path / "skills")
    # Always asks to read_skill, never decides → exhausts max steps.
    adapter = MagicMock()
    adapter.chat.return_value = _tool_resp("read_skill", {"skill_id": "ghost"})
    out = resolve_cluster(_cluster("x"), reg, adapter, UsageTracker())
    assert out is None


# ── _apply_decision ───────────────────────────────────────────────────────────


def test_apply_decision_create_writes_skill(tmp_path):
    reg = SkillRegistry(tmp_path / "skills")
    result = DistillResult()
    _apply_decision(
        {"action": "create", "skill_id": "s1", "name": "S1", "failure_reason": "fr",
         "description": "desc", "body": "# S1\nbody"},
        reg, result,
    )
    assert result.created == ["s1"]
    reg.reload()
    skill = reg.read_skill("s1")
    assert skill is not None
    assert skill.failure_reason == "fr"


def test_apply_decision_improve_overwrites(tmp_path):
    reg = SkillRegistry(tmp_path / "skills")
    reg.write_skill("s1", "S1", "desc", "# S1\nold", failure_reason="fr")
    reg.reload()
    result = DistillResult()
    _apply_decision(
        {"action": "improve", "skill_id": "s1", "name": "S1", "failure_reason": "fr2",
         "description": "desc2", "body": "# S1\nNEW"},
        reg, result,
    )
    assert result.improved == ["s1"]
    reg.reload()
    skill = reg.read_skill("s1")
    assert "NEW" in skill.body
    assert skill.failure_reason == "fr2"


def test_apply_decision_skip_increments(tmp_path):
    reg = SkillRegistry(tmp_path / "skills")
    result = DistillResult()
    _apply_decision({"action": "skip", "reason": "engine glitch"}, reg, result)
    assert result.skipped == 1
    assert result.created == [] and result.improved == []


def test_apply_decision_missing_fields_dropped(tmp_path):
    reg = SkillRegistry(tmp_path / "skills")
    result = DistillResult()
    _apply_decision({"action": "create", "skill_id": "s1"}, reg, result)  # no body/description
    assert result.skipped == 1
    assert result.created == []


# ── distill_runs end-to-end ───────────────────────────────────────────────────


def test_distill_runs_creates_skill_for_recurring_cluster(tmp_path):
    runs = tmp_path / "runs"
    _make_report_run(runs, "a", [_failure("overcommits attacks")])
    _make_report_run(runs, "b", [_failure("overcommits attacks again")])
    skills_dir = tmp_path / "skills"

    cluster_resp = _tool_resp("submit_clusters", {
        "clusters": [{"failure_reason": "no block before attacking", "member_indices": [0, 1]}]
    })
    decision_resp = _tool_resp("submit_decision", {
        "action": "create", "skill_id": "block_first", "name": "Block First",
        "failure_reason": "no block before attacking",
        "description": "Use in multi-hit fights", "body": "# Block First\nBlock before you swing.",
    })
    adapter = _adapter(cluster_resp, decision_resp)
    tracker = UsageTracker()

    result = distill_runs(runs, skills_dir, adapter, tracker, model="m", min_cluster_size=2, min_steps=0)

    assert result.reports_consumed == 2
    assert result.clusters == 1
    assert result.created == ["block_first"]
    # skill landed on disk
    assert (skills_dir / "block_first.md").exists()
    # reports marked distilled → second pass is a no-op
    assert read_report(runs / "a")["distilled_at"]
    result2 = distill_runs(runs, skills_dir, _adapter(), tracker, model="m")
    assert result2.reports_consumed == 0


def test_distill_runs_ignores_subthreshold_clusters(tmp_path):
    runs = tmp_path / "runs"
    _make_report_run(runs, "a", [_failure("rare one-off")])
    skills_dir = tmp_path / "skills"

    cluster_resp = _tool_resp("submit_clusters", {
        "clusters": [{"failure_reason": "rare", "member_indices": [0]}]
    })
    adapter = _adapter(cluster_resp)  # only clustering call; no decision expected

    result = distill_runs(runs, skills_dir, adapter, UsageTracker(), model="m", min_cluster_size=2, min_steps=0)

    assert result.clusters == 0
    assert result.created == []
    assert adapter.chat.call_count == 1  # no per-cluster decision call
    # still marked distilled so it won't be reconsidered forever
    assert read_report(runs / "a")["distilled_at"]


def test_distill_runs_no_reports_is_noop(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    result = distill_runs(runs, tmp_path / "skills", _adapter(), UsageTracker(), model="m", min_steps=0)
    assert result.reports_consumed == 0
    assert result.created == []


def test_distill_runs_reports_without_failures_marks_consumed(tmp_path):
    runs = tmp_path / "runs"
    _make_report_run(runs, "a", [])  # analyzed, no failures
    adapter = _adapter()  # no LLM call expected

    result = distill_runs(runs, tmp_path / "skills", adapter, UsageTracker(), model="m", min_steps=0)

    assert result.reports_consumed == 1
    adapter.chat.assert_not_called()
    assert read_report(runs / "a")["distilled_at"]


def test_distill_runs_skips_short_run_reports(tmp_path):
    runs = tmp_path / "runs"
    short = runs / "short"
    short.mkdir(parents=True)
    (short / "steps.jsonl").write_text('{"step": 0}\n', encoding="utf-8")
    write_report(short, {"run_id": "short", "failures": [_failure("x")]})
    long_run = runs / "long"
    long_run.mkdir(parents=True)
    lines = [
        '{"step": %d, "state_type": "combat", "tool_name": "play_card", "tool_args": {}, "settled_state_summary": "s"}'
        % i for i in range(12)
    ]
    (long_run / "steps.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_report(long_run, {"run_id": "long", "failures": [_failure("y"), _failure("z")]})

    cluster_resp = _tool_resp("submit_clusters", {
        "clusters": [{"failure_reason": "root", "member_indices": [0, 1]}]
    })
    decision_resp = _tool_resp("submit_decision", {
        "action": "create", "skill_id": "s1", "name": "S1",
        "failure_reason": "root", "description": "d", "body": "# S1\nb",
    })
    adapter = _adapter(cluster_resp, decision_resp)
    skills_dir = tmp_path / "skills"

    result = distill_runs(runs, skills_dir, adapter, UsageTracker(), model="m", min_steps=10)

    assert result.reports_consumed == 1
    assert result.reports_skipped_short == 1
    assert "distilled_at" not in read_report(short)
    assert read_report(long_run)["distilled_at"]
