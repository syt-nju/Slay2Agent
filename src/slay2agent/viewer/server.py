"""Live Context Viewer: stdlib HTTP server + SSE (F-009b).

Starts a daemon thread serving:
- GET /           → index.html (static)
- GET /events     → SSE stream (text/event-stream)
- GET /usage      → JSON snapshot of current token usage

The WebObserver pushes events into a queue; the SSE handler drains it.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from functools import partial
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from slay2agent.llm.usage import UsageTracker
from slay2agent.viewer.observer import RunObserver

logger = logging.getLogger(__name__)

_HTML_PATH = Path(__file__).parent / "index.html"


class WebObserver:
    """RunObserver implementation that serialises events into a shared queue."""

    def __init__(self, tracker: UsageTracker) -> None:
        self._queue: queue.Queue[str] = queue.Queue(maxsize=2000)
        self._tracker = tracker

    @property
    def event_queue(self) -> queue.Queue[str]:
        return self._queue

    def _push(self, event_type: str, data: dict[str, Any]) -> None:
        payload = f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            pass  # drop oldest events silently rather than blocking the agent

    def on_step_start(self, step: int, state_type: str, user_message: str, system_summary: str) -> None:
        self._push("step_start", {
            "step": step,
            "state_type": state_type,
            "user_message": user_message,
            "system_summary": system_summary,
        })

    def on_llm_response(self, tool_call_name: str | None, tool_call_args: dict[str, Any] | None, usage: dict[str, int]) -> None:
        self._push("llm_response", {
            "tool_call_name": tool_call_name,
            "tool_call_args": tool_call_args,
            "usage": usage,
        })

    def on_tool_result(self, action: str, result_summary: str) -> None:
        self._push("tool_result", {
            "action": action,
            "result_summary": result_summary,
        })

    def on_memory_event(self, event_type: str, detail: str) -> None:
        self._push("memory_event", {
            "event_type": event_type,
            "detail": detail,
        })

    def on_run_end(self, termination_reason: str, total_steps: int) -> None:
        self._push("run_end", {
            "termination_reason": termination_reason,
            "total_steps": total_steps,
        })
        # Push usage snapshot as final event.
        self._push("usage_snapshot", self._tracker.snapshot())


def _make_handler(event_queue: queue.Queue[str], tracker: UsageTracker):
    """Factory that creates a request handler class with shared state."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            # Suppress default access logs to avoid cluttering agent output.
            pass

        def do_GET(self) -> None:
            if self.path == "/" or self.path == "/index.html":
                self._serve_html()
            elif self.path == "/events":
                self._serve_sse()
            elif self.path == "/usage":
                self._serve_usage()
            else:
                self.send_error(404)

        def _serve_html(self) -> None:
            try:
                content = _HTML_PATH.read_bytes()
            except FileNotFoundError:
                self.send_error(500, "index.html not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def _serve_sse(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            try:
                while True:
                    try:
                        payload = event_queue.get(timeout=1.0)
                        self.wfile.write(payload.encode())
                        self.wfile.flush()
                    except queue.Empty:
                        # Send keepalive comment to detect broken connections.
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

        def _serve_usage(self) -> None:
            data = json.dumps(tracker.snapshot(), ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


class LiveServer:
    """Manages the HTTP server daemon thread."""

    def __init__(self, observer: WebObserver, tracker: UsageTracker, port: int = 8765) -> None:
        self._observer = observer
        self._tracker = tracker
        self._port = port
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        """Start serving in a daemon thread. Returns the access URL."""
        handler_cls = _make_handler(self._observer.event_queue, self._tracker)
        self._server = HTTPServer(("0.0.0.0", self._port), handler_cls)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        url = f"http://localhost:{self._port}"
        logger.info("live viewer started at %s", url)
        return url

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server = None
