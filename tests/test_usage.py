"""UsageTracker tests (§9.3)."""

from __future__ import annotations

from slay2agent.llm.protocol import Usage
from slay2agent.llm.usage import UsageTracker


def test_record_accumulates_per_bucket():
    t = UsageTracker()
    t.record("a/model-x", Usage(input_tokens=10, output_tokens=3))
    t.record("a/model-x", Usage(input_tokens=5, output_tokens=7))
    t.record("b/model-y", Usage(input_tokens=1, output_tokens=1))

    snap = t.snapshot()
    assert snap["a/model-x"] == {"input": 15, "output": 10, "total": 25}
    assert snap["b/model-y"] == {"input": 1, "output": 1, "total": 2}


def test_total_aggregates_all_buckets():
    t = UsageTracker()
    t.record("m1", Usage(input_tokens=10, output_tokens=5))
    t.record("m2", Usage(input_tokens=2, output_tokens=8))

    total = t.total()
    assert total.input_tokens == 12
    assert total.output_tokens == 13
    assert total.total == 25


def test_empty_tracker_totals_zero():
    total = UsageTracker().total()
    assert total.input_tokens == 0
    assert total.output_tokens == 0
    assert UsageTracker().snapshot() == {}
