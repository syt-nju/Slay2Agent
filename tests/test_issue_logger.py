"""Tests for issue_logger — F-011 UnknownView issue logging.

Covers:
- log_loop_issue writes a well-formed entry (regression guard).
- log_unknown_view_issue writes a well-formed entry with correct fields.
- Both functions are silent on I/O errors (never raise).
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from slay2agent.agent.issue_logger import log_loop_issue, log_unknown_view_issue


# ── log_unknown_view_issue ──────────────────────────────────────────────


def test_log_unknown_view_issue_writes_entry(tmp_path: Path) -> None:
    """Entry is written with correct fields and issue_type."""
    issues = tmp_path / "issues.jsonl"
    log_unknown_view_issue(
        issues_path=issues,
        run_id="run_abc123",
        step=42,
        state_type="crystal_sphere",
        payload_keys=["grid", "selected", "time_limit"],
    )
    assert issues.exists()
    lines = issues.read_text().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["issue_type"] == "unknown_view"
    assert entry["run_id"] == "run_abc123"
    assert entry["step"] == 42
    assert entry["state_type"] == "crystal_sphere"
    # Keys should be sorted.
    assert entry["payload_keys"] == sorted(["grid", "selected", "time_limit"])
    assert "timestamp" in entry


def test_log_unknown_view_issue_creates_parent_dirs(tmp_path: Path) -> None:
    """Parent directories are created if they don't exist."""
    issues = tmp_path / "nested" / "deep" / "issues.jsonl"
    log_unknown_view_issue(
        issues_path=issues,
        run_id="run_xyz",
        step=0,
        state_type="weird_state",
        payload_keys=["a", "b"],
    )
    assert issues.exists()


def test_log_unknown_view_issue_appends(tmp_path: Path) -> None:
    """Multiple calls append separate lines."""
    issues = tmp_path / "issues.jsonl"
    for i in range(3):
        log_unknown_view_issue(
            issues_path=issues,
            run_id="run_test",
            step=i,
            state_type=f"state_{i}",
            payload_keys=["x"],
        )
    lines = issues.read_text().splitlines()
    assert len(lines) == 3
    for i, line in enumerate(lines):
        e = json.loads(line)
        assert e["state_type"] == f"state_{i}"


def test_log_unknown_view_issue_silent_on_io_error(tmp_path: Path) -> None:
    """I/O errors are swallowed — function never raises."""
    read_only_dir = tmp_path / "ro"
    read_only_dir.mkdir()
    read_only_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)  # no write
    issues = read_only_dir / "issues.jsonl"
    try:
        # Should not raise.
        log_unknown_view_issue(
            issues_path=issues,
            run_id="run",
            step=0,
            state_type="s",
            payload_keys=[],
        )
    finally:
        # Restore permissions so tmp_path cleanup works.
        read_only_dir.chmod(0o755)


# ── log_loop_issue (regression guard) ──────────────────────────────────


def test_log_loop_issue_writes_entry(tmp_path: Path) -> None:
    """log_loop_issue still writes a well-formed entry after F-011 changes."""
    issues = tmp_path / "issues.jsonl"
    log_loop_issue(
        issues_path=issues,
        run_dir=tmp_path / "runs" / "run_001",
        step=5,
        state_type="card_select",
        repeated_action="select_card",
        repeated_args={"index": 0},
        repeat_count=3,
        compact_prompt_snippet="## CardSelect ...",
    )
    lines = issues.read_text().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["state_type"] == "card_select"
    assert entry["repeat_count"] == 3
    assert "timestamp" in entry
