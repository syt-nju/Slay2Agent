"""slay2agent.agent — main loop, tool bridge, and trace writer."""

from slay2agent.agent.loop import RunConfig, run_demo_loop
from slay2agent.agent.tool_bridge import LoopDetected, LoopDetector, ToolBridge
from slay2agent.agent.trace import TraceWriter, new_run_id

__all__ = [
    "LoopDetected",
    "LoopDetector",
    "RunConfig",
    "ToolBridge",
    "TraceWriter",
    "new_run_id",
    "run_demo_loop",
]
