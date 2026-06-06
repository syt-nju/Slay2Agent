"""Tests for F-013 failure-report I/O and run discovery."""

from __future__ import annotations

import json
from pathlib import Path

from slay2agent.maintenance.report import (
    count_steps,
    find_unanalyzed_runs,
    find_undistilled_reports,
    has_report,
    iter_run_dirs,
    meets_min_steps,
    read_report,
    read_run_meta,
    report_path,
    write_report,
)


def _make_run(runs_dir: Path, name: str, *, with_steps: bool = True, summary: dict | None = None) -> Path:
    d = runs_dir / name
    d.mkdir(parents=True)
    if with_steps:
        (d / "steps.jsonl").write_text('{"step": 0}\n', encoding="utf-8")
    if summary is not None:
        (d / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    return d


def test_write_read_report_roundtrip(tmp_path):
    run_dir = tmp_path / "run1"
    run_dir.mkdir()
    report = {"run_id": "run1", "failures": [{"summary": "x"}]}
    write_report(run_dir, report)
    assert has_report(run_dir)
    assert report_path(run_dir).name == "failure_report.json"
    assert read_report(run_dir) == report


def test_read_report_missing_returns_none(tmp_path):
    assert read_report(tmp_path / "ghost") is None


def test_read_report_malformed_returns_none(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    report_path(run_dir).write_text("{bad json", encoding="utf-8")
    assert read_report(run_dir) is None


def test_read_run_meta_reads_termination(tmp_path):
    run_dir = _make_run(tmp_path, "r", summary={"termination_reason": "game_over"})
    assert read_run_meta(run_dir)["termination_reason"] == "game_over"


def test_read_run_meta_missing_summary(tmp_path):
    run_dir = _make_run(tmp_path, "r")
    assert read_run_meta(run_dir)["termination_reason"] == "unknown"


def test_iter_run_dirs_only_with_steps_sorted(tmp_path):
    _make_run(tmp_path, "b")
    _make_run(tmp_path, "a")
    _make_run(tmp_path, "no_steps", with_steps=False)
    (tmp_path / "loose_file.txt").write_text("x", encoding="utf-8")
    dirs = iter_run_dirs(tmp_path)
    assert [d.name for d in dirs] == ["a", "b"]


def test_find_unanalyzed_runs(tmp_path):
    a = _make_run(tmp_path, "a")
    _make_run(tmp_path, "b")
    write_report(a, {"run_id": "a", "failures": []})
    unanalyzed = find_unanalyzed_runs(tmp_path)
    assert [d.name for d in unanalyzed] == ["b"]


def test_count_steps_and_meets_min_steps(tmp_path):
    d = _make_run(tmp_path, "r")
    (d / "steps.jsonl").write_text(
        '{"step": 0}\n{"step": 1}\n{"step": 2}\n', encoding="utf-8"
    )
    assert count_steps(d) == 3
    assert meets_min_steps(d, 3)
    assert not meets_min_steps(d, 4)
    assert meets_min_steps(d, 0)


def test_find_undistilled_reports(tmp_path):
    a = _make_run(tmp_path, "a")
    b = _make_run(tmp_path, "b")
    _make_run(tmp_path, "c")  # no report at all → excluded
    write_report(a, {"run_id": "a", "failures": [], "distilled_at": "2026-06-06T00:00:00"})
    write_report(b, {"run_id": "b", "failures": []})
    undistilled = find_undistilled_reports(tmp_path)
    assert [d.name for d in undistilled] == ["b"]
