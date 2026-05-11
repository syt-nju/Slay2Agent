"""Live smoke test against OpenRouter.

Usage:
    OPENROUTER_API_KEY=sk-or-... python -m slay2agent.llm.smoke
    OPENROUTER_API_KEY=sk-or-... python -m slay2agent.llm.smoke --model=xiaomi/mimo-v2-flash
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from dotenv import load_dotenv

from slay2agent.llm import (
    Message,
    OpenRouterAdapter,
    ToolSchema,
    UsageTracker,
    call_with_retry,
)

DEFAULT_MODEL = "xiaomi/mimo-v2-flash"

_ECHO_SCHEMA = ToolSchema(
    name="echo",
    description="Echo the given text back verbatim.",
    parameters={
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    },
)


def _scenario_plain_text(adapter: OpenRouterAdapter, tracker: UsageTracker) -> bool:
    print("\n── scenario 1: plain text ──")
    resp = call_with_retry(
        lambda: adapter.chat(
            [
                Message(role="system", content="You are terse."),
                Message(
                    role="user",
                    content='Reply with exactly this JSON and nothing else: {"ok": true}',
                ),
            ]
        )
    )
    tracker.record("main", resp.model, resp.usage)
    print(f"  model       = {resp.model}")
    print(f"  stop_reason = {resp.stop_reason}")
    print(f"  usage       = in={resp.usage.input_tokens} out={resp.usage.output_tokens}")
    print(f"  content     = {resp.message.content!r}")
    ok = resp.stop_reason == "stop" and "ok" in (resp.message.content or "").lower()
    print(f"  PASS        = {ok}")
    return ok


def _scenario_tool_call(adapter: OpenRouterAdapter, tracker: UsageTracker) -> bool:
    print("\n── scenario 2: tool call ──")
    resp = call_with_retry(
        lambda: adapter.chat(
            [
                Message(
                    role="system",
                    content=(
                        "You MUST call the echo tool. Do not reply with text. "
                        "Call echo(text='hi')."
                    ),
                ),
                Message(role="user", content="Please call echo with text 'hi'."),
            ],
            tools=[_ECHO_SCHEMA],
            tool_choice="required",
        )
    )
    tracker.record("main", resp.model, resp.usage)
    print(f"  model       = {resp.model}")
    print(f"  stop_reason = {resp.stop_reason}")
    print(f"  usage       = in={resp.usage.input_tokens} out={resp.usage.output_tokens}")
    print(f"  tool_calls  = {resp.message.tool_calls}")
    ok = (
        resp.stop_reason == "tool_calls"
        and resp.message.tool_calls is not None
        and len(resp.message.tool_calls) >= 1
        and resp.message.tool_calls[0].name == "echo"
        and resp.message.tool_calls[0].arguments.get("text") == "hi"
    )
    print(f"  PASS        = {ok}")
    return ok


def main() -> int:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=os.getenv("OPENROUTER_MODEL", DEFAULT_MODEL))
    args = parser.parse_args()

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("OPENROUTER_API_KEY not set", file=sys.stderr)
        return 2

    adapter = OpenRouterAdapter(model=args.model, api_key=api_key)
    tracker = UsageTracker()

    results = [
        _scenario_plain_text(adapter, tracker),
        _scenario_tool_call(adapter, tracker),
    ]

    print("\n── summary ──")
    print(f"  usage snapshot = {tracker.snapshot()}")
    print(f"  all pass       = {all(results)}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
