"""Oracle reader — L2 memory layer (F-008a).

``oracle.md`` is a free-form Markdown document that holds global meta-strategy.
It is injected in full into every system prompt and rewritten by the oracle
updater sub-agent at the end of each run (F-008c).

This module only *reads* the file.  Writing is the oracle updater's job.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Sentinel returned when oracle.md is absent or empty.
_EMPTY = ""


def read_oracle(oracle_path: Path) -> str:
    """Return the contents of oracle.md, or an empty string if missing/empty.

    Never raises — a missing oracle is normal at the start of a new project.
    """
    if not oracle_path.exists():
        logger.debug("oracle: %s not found — returning empty", oracle_path)
        return _EMPTY

    try:
        content = oracle_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        logger.error("oracle: cannot read %s: %s", oracle_path, exc)
        return _EMPTY

    if not content:
        logger.debug("oracle: %s is empty", oracle_path)
        return _EMPTY

    return content


def oracle_version(oracle_path: Path) -> str | None:
    """Return a simple version token (file mtime as ISO string) or None.

    Used by the trace writer to record which oracle version was injected at
    each step, without storing the full text in the trace.
    """
    if not oracle_path.exists():
        return None
    try:
        mtime = oracle_path.stat().st_mtime
        import time
        return time.strftime("%Y%m%dT%H%M%S", time.localtime(mtime))
    except OSError:
        return None
