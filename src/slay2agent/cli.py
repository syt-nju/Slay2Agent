"""slay2agent unified CLI.

Subcommands:
    smoke    Run the OpenRouter live smoke test (existing F-002 entrypoint).
    inspect  Print current STS2MCP state (stub until F-003).
    run      Run the agent loop on the current game (stub until F-005).
    config   Print effective configuration (with secrets masked).
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Sequence

from slay2agent.config import Config


def _mask(secret: str | None) -> str:
    if not secret:
        return "<unset>"
    if len(secret) <= 8:
        return "***"
    return f"{secret[:4]}…{secret[-4:]}"


def _cmd_config(args: argparse.Namespace) -> int:
    cfg = Config.load()
    print("LLM:")
    print(f"  model    = {cfg.llm.model}")
    print(f"  api_key  = {_mask(cfg.llm.api_key)}")
    print(f"  timeout  = {cfg.llm.timeout}")
    print("Game (STS2MCP):")
    print(f"  base_url = {cfg.game.base_url or '<unset>'}")
    print(f"  timeout  = {cfg.game.timeout}")
    return 0


def _cmd_smoke(args: argparse.Namespace) -> int:
    # Defer import so `slay2agent --help` does not pay openai import cost.
    from slay2agent.llm import smoke as smoke_mod

    cfg = Config.load()
    argv = ["--model", args.model] if args.model else ["--model", cfg.llm.model]
    sys.argv = ["slay2agent.llm.smoke", *argv]
    return smoke_mod.main()


def _cmd_inspect(args: argparse.Namespace) -> int:
    print(
        "inspect: not implemented yet — pending F-003 (Game Communication Path).",
        file=sys.stderr,
    )
    return 2


def _cmd_run(args: argparse.Namespace) -> int:
    print(
        "run: not implemented yet — pending F-005 (Minimal Runnable Agent Loop).",
        file=sys.stderr,
    )
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="slay2agent",
        description="Train-free agent driving Slay the Spire 2 via cloud LLM + STS2MCP REST.",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable DEBUG logging."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_config = sub.add_parser("config", help="Print effective config (secrets masked).")
    p_config.set_defaults(func=_cmd_config)

    p_smoke = sub.add_parser("smoke", help="Live LLM smoke test (needs OPENROUTER_API_KEY).")
    p_smoke.add_argument("--model", default=None, help="Override model slug.")
    p_smoke.set_defaults(func=_cmd_smoke)

    p_inspect = sub.add_parser(
        "inspect", help="Print current STS2MCP state (stub until F-003)."
    )
    p_inspect.set_defaults(func=_cmd_inspect)

    p_run = sub.add_parser(
        "run", help="Run the agent loop on the current game (stub until F-005)."
    )
    p_run.set_defaults(func=_cmd_run)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
