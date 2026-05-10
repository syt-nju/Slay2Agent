"""slay2agent unified CLI.

Subcommands:
    smoke    Run the OpenRouter live smoke test (F-002).
    inspect  Print current STS2MCP state via the game REST client (F-003).
    play     Run the Phase 1 demo loop (F-005).
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
    print(f"  base_url = {cfg.game.base_url}")
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
    import json as _json

    from slay2agent.game import GameClient, GameClientError

    cfg = Config.load()
    try:
        with GameClient(cfg.game.base_url, timeout=cfg.game.timeout) as client:
            if args.health:
                payload = client.health()
            else:
                payload = client.get_state()
    except GameClientError as exc:
        print(f"inspect failed: {exc}", file=sys.stderr)
        return 1
    print(_json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _cmd_play(args: argparse.Namespace) -> int:
    """Phase 1 demo loop: main menu → game_over (F-005)."""
    from pathlib import Path

    from slay2agent.agent.loop import RunConfig, run_demo_loop

    cfg = Config.load()
    run_cfg = RunConfig(
        character=args.character.upper(),
        ascension=args.ascension,
        runs_dir=Path(args.runs_dir),
        window_size=args.window_size,
        repeat_threshold=args.repeat_threshold,
    )
    try:
        run_dir = run_demo_loop(cfg, run_cfg)
        print(f"Run complete. Trace written to: {run_dir}")
        return 0
    except Exception as exc:
        print(f"play: fatal error — {exc}", file=sys.stderr)
        return 1


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
        "inspect", help="Print current STS2MCP state (needs the mod running)."
    )
    p_inspect.add_argument(
        "--health",
        action="store_true",
        help="Hit GET / instead of /api/v1/singleplayer (mod reachability check).",
    )
    p_inspect.set_defaults(func=_cmd_inspect)

    p_play = sub.add_parser(
        "play", help="Run the Phase 1 demo loop (main menu → game_over)."
    )
    p_play.add_argument(
        "--character", default="IRONCLAD", help="Character id (default: IRONCLAD)."
    )
    p_play.add_argument(
        "--ascension", type=int, default=0, help="Ascension level (default: 0)."
    )
    p_play.add_argument(
        "--runs-dir", default="runs", help="Directory to write run traces (default: runs/)."
    )
    p_play.add_argument(
        "--window-size", type=int, default=10,
        help="Loop detector window size (default: 10).",
    )
    p_play.add_argument(
        "--repeat-threshold", type=int, default=4,
        help="Loop detector repeat threshold (default: 4).",
    )
    p_play.set_defaults(func=_cmd_play)

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
