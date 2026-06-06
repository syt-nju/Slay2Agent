"""Phase 2 of the F-013 pipeline — distill failures into skills.

Two LLM stages with **deliberate context isolation** (the key design choice):

  Stage 2a — cluster (role ``distiller_cluster``):
      Sees ONLY the pooled failure reasons from undistilled reports and groups
      similar, recurring ones. It knows nothing about the skill library, so
      clustering is driven purely by failure similarity / frequency.

  Stage 2b — decide (role ``distiller``), one ISOLATED conversation per cluster:
      Sees ONE cluster plus the current skill library headers (id / name /
      failure_reason / description). It may ``read_skill`` to inspect a body,
      then ``submit_decision`` to create a new skill, improve (overwrite) an
      existing one, or skip. No other cluster leaks into this context.

Isolating the two decisions stops "what should be grouped" from contaminating
"does a skill already cover this" — which is what keeps the library small and
the skills coherent.

Skill writes go through ``SkillRegistry.write_skill`` (full-file overwrite).
``failure_reason`` is stored for future dedup; ``description`` is the play-time
trigger.  The LLM owns every create/improve/skip decision.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from slay2agent.llm.protocol import LLMAdapter, Message, ToolSchema
from slay2agent.llm.retry import call_with_retry
from slay2agent.llm.usage import UsageTracker
from slay2agent.maintenance.report import (
    DISTILLED_KEY,
    find_undistilled_reports,
    meets_min_steps,
    read_report,
    write_report,
)
from slay2agent.memory.skill_registry import SkillRegistry

logger = logging.getLogger(__name__)

_ROLE_CLUSTER = "distiller_cluster"
_ROLE_DECIDE = "distiller"

# ── Stage 2a: clustering ──────────────────────────────────────────────────────

_CLUSTER_SYSTEM = """\
You are triaging post-run failure reports for a Slay the Spire 2 AI agent.
You are given a flat, numbered list of failures pooled across many runs.

Group failures that share the SAME underlying root cause into clusters. Two
failures belong together when fixing one would fix the other — judge by root
cause, not surface wording. A failure that stands alone is its own cluster.

For each cluster produce one canonical ``failure_reason`` that captures the
shared root cause. Reference members by their input index.

Respond by calling submit_clusters exactly once. Consider ONLY the failures
shown — do not speculate about skills or fixes.
"""

_CLUSTER_TOOL = ToolSchema(
    name="submit_clusters",
    description="Submit the grouping of failures into root-cause clusters.",
    parameters={
        "type": "object",
        "properties": {
            "clusters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "failure_reason": {
                            "type": "string",
                            "description": "Canonical root cause shared by the cluster's members.",
                        },
                        "member_indices": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "description": "Indices (from the input list) of failures in this cluster.",
                        },
                    },
                    "required": ["failure_reason", "member_indices"],
                },
            },
        },
        "required": ["clusters"],
    },
)

# ── Stage 2b: per-cluster decision ────────────────────────────────────────────

_DECIDE_SYSTEM = """\
You curate the skill library for a Slay the Spire 2 AI agent. You are given ONE
recurring failure cluster and the headers of every existing skill.

Decide how to fold this failure into the library:
- improve: an existing skill already covers this failure's situation. Call
  read_skill on it first, then rewrite the WHOLE body to also handle this
  failure. Reuse its skill_id. Your body OVERWRITES the file entirely.
- create: no existing skill fits. Invent a new snake_case skill_id.
- skip: this failure is not a generalizable strategy lesson (e.g. an engine /
  UI glitch, or a one-off). Explain why in reason.

Field rules for create / improve:
- failure_reason: the failure(s) this skill prevents (stored for future dedup;
  NOT shown to the play-time agent).
- description: the play-time trigger — describe BOTH what the skill covers AND
  exactly WHEN to apply it. This is the ONLY text the live agent sees.
- body: actionable markdown guidance. Keep it focused and generalizable.

Respond by calling read_skill (optional, repeatable) then submit_decision once.
"""

_READ_SKILL_TOOL = ToolSchema(
    name="read_skill",
    description="Read the full body of an existing skill before improving it.",
    parameters={
        "type": "object",
        "properties": {"skill_id": {"type": "string"}},
        "required": ["skill_id"],
    },
)

_DECISION_TOOL = ToolSchema(
    name="submit_decision",
    description="Decide how to fold this failure cluster into the skill library.",
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["create", "improve", "skip"]},
            "skill_id": {
                "type": "string",
                "description": "snake_case id; for improve it MUST match an existing skill.",
            },
            "name": {"type": "string", "description": "Human-readable skill name."},
            "failure_reason": {
                "type": "string",
                "description": "Failure(s) this skill prevents (frontmatter, offline-only).",
            },
            "description": {
                "type": "string",
                "description": "Play-time trigger: what it covers AND when to apply it.",
            },
            "body": {
                "type": "string",
                "description": "Full markdown body. For improve this OVERWRITES the whole file.",
            },
            "reason": {"type": "string", "description": "For skip: why no skill is warranted."},
        },
        "required": ["action"],
    },
)

_MAX_DECIDE_STEPS = 6


# ── Data structures ───────────────────────────────────────────────────────────


@dataclass
class DistillResult:
    reports_consumed: int = 0
    reports_skipped_short: int = 0
    clusters: int = 0
    created: list[str] = field(default_factory=list)
    improved: list[str] = field(default_factory=list)
    skipped: int = 0


# ── Helpers ───────────────────────────────────────────────────────────────────


def _tool_args(tool_call) -> dict:
    args = tool_call.arguments
    if isinstance(args, str):
        return json.loads(args)
    return args if isinstance(args, dict) else {}


def collect_failures(report_dirs: list[Path]) -> list[dict]:
    """Flatten failures across reports, tagging each with its source run_id."""
    failures: list[dict] = []
    for run_dir in report_dirs:
        report = read_report(run_dir)
        if not report:
            continue
        for f in report.get("failures", []):
            if not isinstance(f, dict):
                continue
            failures.append({
                "run_id": report.get("run_id", run_dir.name),
                "summary": f.get("summary", ""),
                "detail": f.get("detail", ""),
                "excerpt": f.get("excerpt", ""),
            })
    return failures


def _render_failures(failures: list[dict]) -> str:
    lines = []
    for i, f in enumerate(failures):
        lines.append(f"[{i}] (run {f['run_id']}) {f['summary']}\n    {f['detail']}")
    return "\n".join(lines)


def cluster_failures(
    failures: list[dict],
    adapter: LLMAdapter,
    tracker: UsageTracker,
    *,
    extra_body: dict | None = None,
) -> list[dict]:
    """Stage 2a — group pooled failures by root cause (skills not in context)."""
    if not failures:
        return []

    system_msg = Message(role="system", content=_CLUSTER_SYSTEM)
    user_msg = Message(
        role="user",
        content=f"## Pooled failures ({len(failures)})\n{_render_failures(failures)}",
    )
    resp = call_with_retry(
        lambda: adapter.chat(
            [system_msg, user_msg], [_CLUSTER_TOOL], tool_choice="required", extra_body=extra_body
        )
    )
    tracker.record(_ROLE_CLUSTER, resp.model, resp.usage)

    tool_calls = resp.message.tool_calls or []
    call = next((tc for tc in tool_calls if tc.name == "submit_clusters"), None)
    if call is None:
        logger.error("distill: clustering returned no submit_clusters call")
        return []

    out: list[dict] = []
    for c in _tool_args(call).get("clusters", []):
        if not isinstance(c, dict):
            continue
        idxs = [i for i in c.get("member_indices", []) if isinstance(i, int) and 0 <= i < len(failures)]
        if not idxs:
            continue
        out.append({
            "failure_reason": str(c.get("failure_reason", "")).strip(),
            "members": [failures[i] for i in idxs],
        })
    return out


def _render_skill_headers(registry: SkillRegistry) -> str:
    metas = registry.list_skills()
    if not metas:
        return "(the skill library is currently empty)"
    lines = []
    for m in metas:
        lines.append(
            f"- skill_id={m.skill_id}\n  name: {m.name}\n  failure_reason: {m.failure_reason}\n  description: {m.description}"
        )
    return "\n".join(lines)


def _render_cluster(cluster: dict) -> str:
    members = cluster["members"]
    lines = [f"failure_reason: {cluster['failure_reason']}", f"occurrences: {len(members)}", "evidence:"]
    for m in members:
        excerpt = f"\n    excerpt: {m['excerpt']}" if m.get("excerpt") else ""
        lines.append(f"  - (run {m['run_id']}) {m['summary']} — {m['detail']}{excerpt}")
    return "\n".join(lines)


def resolve_cluster(
    cluster: dict,
    registry: SkillRegistry,
    adapter: LLMAdapter,
    tracker: UsageTracker,
    *,
    extra_body: dict | None = None,
) -> dict | None:
    """Stage 2b — one isolated decision for a single cluster.

    Returns the decision dict (with at least ``action``) or ``None`` if the
    model never submitted one.
    """
    conversation = [
        Message(role="system", content=_DECIDE_SYSTEM),
        Message(
            role="user",
            content=(
                f"## Failure cluster\n{_render_cluster(cluster)}\n\n"
                f"## Existing skills\n{_render_skill_headers(registry)}"
            ),
        ),
    ]
    tools = [_READ_SKILL_TOOL, _DECISION_TOOL]

    for _ in range(_MAX_DECIDE_STEPS):
        resp = call_with_retry(
            lambda: adapter.chat(conversation, tools, tool_choice="required", extra_body=extra_body)
        )
        tracker.record(_ROLE_DECIDE, resp.model, resp.usage)
        conversation.append(resp.message)

        tool_calls = resp.message.tool_calls or []
        if not tool_calls:
            break
        call = tool_calls[0]

        if call.name == "submit_decision":
            return _tool_args(call)

        if call.name == "read_skill":
            sid = _tool_args(call).get("skill_id", "")
            result = registry.read_skill_response(sid)
            conversation.append(
                Message(role="tool", content=json.dumps(result, ensure_ascii=False), tool_call_id=call.id)
            )
            for extra in tool_calls[1:]:
                conversation.append(
                    Message(role="tool", content="(skipped — one tool call per step)", tool_call_id=extra.id)
                )
            continue

        # Unknown tool — feed an error and let it retry.
        conversation.append(
            Message(role="tool", content=f"ERROR: unknown tool {call.name!r}", tool_call_id=call.id)
        )

    logger.error("distill: cluster %r reached max steps without a decision", cluster["failure_reason"])
    return None


def _apply_decision(decision: dict, registry: SkillRegistry, result: DistillResult) -> None:
    action = decision.get("action")
    if action == "skip":
        logger.info("distill: skip — %s", decision.get("reason", ""))
        result.skipped += 1
        return

    skill_id = str(decision.get("skill_id", "")).strip()
    body = str(decision.get("body", "")).strip()
    description = str(decision.get("description", "")).strip()
    if not (skill_id and body and description):
        logger.error("distill: %s decision missing skill_id/description/body — dropping", action)
        result.skipped += 1
        return

    existed = registry.read_skill(skill_id) is not None
    registry.write_skill(
        skill_id=skill_id,
        name=str(decision.get("name", "")).strip() or skill_id,
        description=description,
        body=body,
        failure_reason=str(decision.get("failure_reason", "")).strip(),
    )
    registry.reload()

    if action == "improve" or existed:
        result.improved.append(skill_id)
        logger.info("distill: improved skill %r", skill_id)
    else:
        result.created.append(skill_id)
        logger.info("distill: created skill %r", skill_id)


def _mark_distilled(report_dirs: list[Path]) -> None:
    import time

    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    for run_dir in report_dirs:
        report = read_report(run_dir)
        if report is None:
            continue
        report[DISTILLED_KEY] = stamp
        write_report(run_dir, report)


def distill_runs(
    runs_dir: Path,
    skills_dir: Path,
    adapter: LLMAdapter,
    tracker: UsageTracker,
    *,
    model: str,
    min_cluster_size: int = 2,
    min_steps: int = 10,
    extra_body: dict | None = None,
) -> DistillResult:
    """Cluster undistilled failures, then create/improve skills per cluster.

    Reports from runs shorter than ``min_steps`` are ignored and left
    undistilled so a future lower threshold can pick them up.

    Only clusters with at least ``min_cluster_size`` occurrences are acted on
    ("recurring" failures). All consumed reports are marked distilled so they
    are not re-processed, even when their failures did not reach the threshold.
    """
    all_reports = find_undistilled_reports(runs_dir)
    report_dirs = [d for d in all_reports if meets_min_steps(d, min_steps)]
    skipped_short = len(all_reports) - len(report_dirs)
    result = DistillResult(
        reports_consumed=len(report_dirs),
        reports_skipped_short=skipped_short,
    )
    if skipped_short:
        logger.info(
            "distill: skipped %d report(s) with < %d steps",
            skipped_short, min_steps,
        )
    if not report_dirs:
        logger.info("distill: no eligible undistilled reports under %s", runs_dir)
        return result

    failures = collect_failures(report_dirs)
    if not failures:
        logger.info("distill: reports contain no failures — marking consumed")
        _mark_distilled(report_dirs)
        return result

    registry = SkillRegistry(skills_dir)
    clusters = cluster_failures(failures, adapter, tracker, extra_body=extra_body)
    qualifying = [c for c in clusters if len(c["members"]) >= min_cluster_size]
    result.clusters = len(qualifying)
    logger.info(
        "distill: %d failure(s) → %d cluster(s), %d recurring (>=%d)",
        len(failures), len(clusters), len(qualifying), min_cluster_size,
    )

    for cluster in qualifying:
        decision = resolve_cluster(cluster, registry, adapter, tracker, extra_body=extra_body)
        if decision is None:
            result.skipped += 1
            continue
        _apply_decision(decision, registry, result)

    _mark_distilled(report_dirs)
    return result
