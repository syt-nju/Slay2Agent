"""Issue logger — records loop-warning events for post-run analysis.

Appends structured entries to ``agent_state/issues.jsonl`` whenever the
loop detector fires a soft warning and raw MCP state is injected.

Each entry records enough context to locate the exact trace step and
understand the compact-prompt gap that caused the loop:

    {
        "timestamp": "2026-05-20T01:00:00",
        "run_dir": "runs/20260520T005248_0b46d673",
        "step": 6,
        "state_type": "card_select",
        "repeated_action": "select_card",
        "repeated_args": {"index": 0},
        "repeat_count": 3,
        "compact_prompt_snippet": "## CardSelect — screen: select ..."
    }

The file is append-only and safe to delete (it's a debug artifact, not
consumed by the agent at runtime).
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def log_loop_issue(
    *,
    issues_path: Path,
    run_dir: Path,
    step: int,
    state_type: str,
    repeated_action: str,
    repeated_args: dict[str, Any] | None,
    repeat_count: int,
    compact_prompt_snippet: str,
) -> None:
    """Append one loop-warning issue entry to ``issues_path``.

    Never raises — any I/O error is logged and swallowed so the main
    loop is never interrupted by issue logging.
    """
    entry = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "run_dir": str(run_dir),
        "step": step,
        "state_type": state_type,
        "repeated_action": repeated_action,
        "repeated_args": repeated_args or {},
        "repeat_count": repeat_count,
        "compact_prompt_snippet": compact_prompt_snippet[:300],
    }
    try:
        issues_path.parent.mkdir(parents=True, exist_ok=True)
        with issues_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        logger.info(
            "issue_logger: recorded loop issue at step %d (%s %s × %d)",
            step, repeated_action, repeated_args, repeat_count,
        )
    except Exception as exc:
        logger.error("issue_logger: failed to write issue: %s", exc)
