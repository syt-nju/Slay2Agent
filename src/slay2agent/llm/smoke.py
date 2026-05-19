"""Live smoke test against any OpenAI-compatible LLM endpoint.

Usage:
    LLM_API_KEY=sk-... python -m slay2agent.llm.smoke
    LLM_API_KEY=sk-... LLM_BASE_URL=https://openrouter.ai/api/v1 python -m slay2agent.llm.smoke --model=openai/gpt-4.1-mini
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from dotenv import load_dotenv

from slay2agent.llm import (
    LLMAdapter,
    Message,
    OpenAICompatibleAdapter,
    ToolSchema,
    UsageTracker,
    call_with_retry,
)

DEFAULT_MODEL = "openai/gpt-4.1-mini"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

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


def _scenario_plain_text(adapter: LLMAdapter, tracker: UsageTracker) -> bool:
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


def _scenario_tool_call(adapter: LLMAdapter, tracker: UsageTracker) -> bool:
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
    parser.add_argument("--model", default=os.getenv("LLM_MODEL", DEFAULT_MODEL))
    args = parser.parse_args()

    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        print("LLM_API_KEY not set", file=sys.stderr)
        return 2

    base_url = os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL)
    adapter = OpenAICompatibleAdapter(model=args.model, api_key=api_key, base_url=base_url)
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
