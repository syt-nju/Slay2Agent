"""UsageTracker tests — covers F-002 acceptance #4 (role + model bucketing)."""

from __future__ import annotations

from slay2agent.llm.protocol import Usage
from slay2agent.llm.usage import UsageTracker


def test_record_buckets_by_role_and_model():
    t = UsageTracker()
    t.record("main", "openai/gpt-4o-mini", Usage(input_tokens=10, output_tokens=3))
    t.record("main", "openai/gpt-4o-mini", Usage(input_tokens=5, output_tokens=7))
    t.record(
        "compactor",
        "openai/gpt-4o-mini",
        Usage(input_tokens=4, output_tokens=2),
    )
    t.record(
        "oracle_updater",
        "anthropic/claude-sonnet-4.5",
        Usage(input_tokens=20, output_tokens=8),
    )

    snap = t.snapshot()
    assert snap["main"]["openai/gpt-4o-mini"] == {
        "input": 15,
        "output": 10,
        "total": 25,
    }
    assert snap["compactor"]["openai/gpt-4o-mini"] == {
        "input": 4,
        "output": 2,
        "total": 6,
    }
    assert snap["oracle_updater"]["anthropic/claude-sonnet-4.5"] == {
        "input": 20,
        "output": 8,
        "total": 28,
    }


def test_same_model_different_roles_stay_separate():
    t = UsageTracker()
    t.record("main", "m1", Usage(input_tokens=10, output_tokens=1))
    t.record("compactor", "m1", Usage(input_tokens=3, output_tokens=2))

    snap = t.snapshot()
    assert snap["main"]["m1"]["total"] == 11
    assert snap["compactor"]["m1"]["total"] == 5
    assert "compactor" not in snap["main"]
    assert "main" not in snap["compactor"]


def test_role_totals_aggregates_across_models_per_role():
    t = UsageTracker()
    t.record("main", "m1", Usage(input_tokens=10, output_tokens=2))
    t.record("main", "m2", Usage(input_tokens=5, output_tokens=1))
    t.record("compactor", "m1", Usage(input_tokens=3, output_tokens=4))

    totals = t.role_totals()
    assert totals["main"].input_tokens == 15
    assert totals["main"].output_tokens == 3
    assert totals["compactor"].input_tokens == 3
    assert totals["compactor"].output_tokens == 4
    assert "oracle_updater" not in totals


def test_total_aggregates_all_buckets_globally():
    t = UsageTracker()
    t.record("main", "m1", Usage(input_tokens=10, output_tokens=5))
    t.record("oracle_updater", "m2", Usage(input_tokens=2, output_tokens=8))

    total = t.total()
    assert total.input_tokens == 12
    assert total.output_tokens == 13
    assert total.total == 25


def test_empty_tracker_is_zeroed():
    t = UsageTracker()
    assert t.snapshot() == {}
    assert t.role_totals() == {}
    assert t.total().input_tokens == 0
    assert t.total().output_tokens == 0
