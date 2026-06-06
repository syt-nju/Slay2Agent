"""slay2agent unified CLI.

Subcommands:
    smoke    Run the live LLM smoke test (F-002).
    inspect  Print current STS2MCP state via the game REST client (F-003).
    play     Run the Phase 1 demo loop (F-005).
    analyze  Offline F-013 phase 1: review runs → failure_report.json.
    distill  Offline F-013 phase 2: cluster failures → create/improve skills.
    config   Print effective configuration (with secrets masked).
"""

from __future__ import annotations

import argparse
import dataclasses
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
    print(f"  provider = {cfg.llm.provider}")
    print(f"  model    = {cfg.llm.model}")
    print(f"  base_url = {cfg.llm.base_url}")
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
    if args.model:
        cfg = dataclasses.replace(cfg, llm=dataclasses.replace(cfg.llm, model=args.model))
    run_cfg = RunConfig(
        character=args.character.upper(),
        ascension=args.ascension,
        runs_dir=Path(args.runs_dir),
        window_size=args.window_size,
        repeat_threshold=args.repeat_threshold,
    )

    observer = None
    live_server = None
    shared_tracker = None
    if args.live:
        from slay2agent.llm.usage import UsageTracker
        from slay2agent.viewer.server import LiveServer, WebObserver

        shared_tracker = UsageTracker()
        observer = WebObserver(shared_tracker)
        live_server = LiveServer(observer, shared_tracker, port=args.live_port)
        url = live_server.start()
        print(f"Live viewer: {url}")

    try:
        import asyncio
        run_dir = asyncio.run(run_demo_loop(cfg, run_cfg, observer=observer, tracker=shared_tracker))
        print(f"Run complete. Trace written to: {run_dir}")
        return 0
    except Exception as exc:
        print(f"play: fatal error — {exc}", file=sys.stderr)
        return 1
    finally:
        if live_server:
            import time
            print("Live viewer will remain active for 30s for final review...")
            time.sleep(30)
            live_server.stop()


def _resolve_model(cfg: Config, override: str | None) -> Config:
    if override:
        return dataclasses.replace(cfg, llm=dataclasses.replace(cfg.llm, model=override))
    return cfg


def _print_role_usage(tracker, roles: Sequence[str]) -> None:
    totals = tracker.role_totals()
    for role in roles:
        u = totals.get(role)
        if u:
            print(f"  {role}: in={u.input_tokens} out={u.output_tokens}")


def _cmd_analyze(args: argparse.Namespace) -> int:
    """Offline F-013 phase 1: review runs and write failure_report.json."""
    from pathlib import Path

    from slay2agent.llm.factory import build_llm_adapter
    from slay2agent.llm.usage import UsageTracker
    from slay2agent.maintenance.analyze import analyze_runs

    cfg = _resolve_model(Config.load(), args.model)
    adapter = build_llm_adapter(cfg.llm)
    tracker = UsageTracker()
    try:
        min_steps = (
            args.min_steps
            if args.min_steps is not None
            else cfg.memory.maintenance_min_steps
        )
        result = analyze_runs(
            Path(args.runs_dir),
            adapter,
            tracker,
            model=cfg.llm.model,
            force=args.force,
            min_steps=min_steps,
            extra_body=cfg.llm.subagent_extra_body,
        )
    except Exception as exc:
        print(f"analyze: fatal error — {exc}", file=sys.stderr)
        return 1

    print(
        f"analyze: reviewed={len(result.analyzed)} "
        f"failed={len(result.failed)} skipped={result.skipped} "
        f"skipped_short={result.skipped_short}"
    )
    if result.failed:
        print(f"  failed runs: {', '.join(result.failed)}", file=sys.stderr)
    _print_role_usage(tracker, ["failure_analyzer"])
    return 1 if result.failed and not result.analyzed else 0


def _cmd_distill(args: argparse.Namespace) -> int:
    """Offline F-013 phase 2: cluster failures and create/improve skills."""
    from pathlib import Path

    from slay2agent.llm.factory import build_llm_adapter
    from slay2agent.llm.usage import UsageTracker
    from slay2agent.maintenance.distill import distill_runs

    cfg = _resolve_model(Config.load(), args.model)
    adapter = build_llm_adapter(cfg.llm)
    tracker = UsageTracker()
    try:
        min_steps = (
            args.min_steps
            if args.min_steps is not None
            else cfg.memory.maintenance_min_steps
        )
        result = distill_runs(
            Path(args.runs_dir),
            cfg.memory.skills_dir,
            adapter,
            tracker,
            model=cfg.llm.model,
            min_cluster_size=args.min_cluster_size,
            min_steps=min_steps,
            extra_body=cfg.llm.subagent_extra_body,
        )
    except Exception as exc:
        print(f"distill: fatal error — {exc}", file=sys.stderr)
        return 1

    print(
        f"distill: reports={result.reports_consumed} "
        f"skipped_short={result.reports_skipped_short} clusters={result.clusters} "
        f"created={len(result.created)} improved={len(result.improved)} skipped={result.skipped}"
    )
    for action, sid in [("created", s) for s in result.created] + [("improved", s) for s in result.improved]:
        print(f"  {action}: {sid}")
    _print_role_usage(tracker, ["distiller_cluster", "distiller"])
    return 0


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

    p_smoke = sub.add_parser("smoke", help="Live LLM smoke test (needs LLM_API_KEY).")
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
        "--model", default=None,
        help="Override LLM_MODEL for this run (default: env / config).",
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
        "--window-size", type=int, default=12,
        help="Loop detector window size (default: 12).",
    )
    p_play.add_argument(
        "--repeat-threshold", type=int, default=6,
        help="Loop detector repeat threshold (default: 6).",
    )
    p_play.add_argument(
        "--live", action="store_true",
        help="Start live context viewer (browser SSE) on a local port.",
    )
    p_play.add_argument(
        "--live-port", type=int, default=8765,
        help="Port for the live viewer HTTP server (default: 8765).",
    )
    p_play.set_defaults(func=_cmd_play)

    p_analyze = sub.add_parser(
        "analyze",
        help="Offline F-013 phase 1: review runs → failure_report.json.",
    )
    p_analyze.add_argument(
        "--model", default=None, help="Override LLM_MODEL for analysis (default: env / config)."
    )
    p_analyze.add_argument(
        "--runs-dir", default="runs", help="Directory holding run traces (default: runs/)."
    )
    p_analyze.add_argument(
        "--force", action="store_true",
        help="Re-analyze every run, overwriting existing failure_report.json.",
    )
    p_analyze.add_argument(
        "--min-steps", type=int, default=None,
        help="Only analyze runs with at least this many steps (default: MAINTENANCE_MIN_STEPS / 10).",
    )
    p_analyze.set_defaults(func=_cmd_analyze)

    p_distill = sub.add_parser(
        "distill",
        help="Offline F-013 phase 2: cluster failures → create/improve skills.",
    )
    p_distill.add_argument(
        "--model", default=None, help="Override LLM_MODEL for distillation (default: env / config)."
    )
    p_distill.add_argument(
        "--runs-dir", default="runs", help="Directory holding run traces (default: runs/)."
    )
    p_distill.add_argument(
        "--min-cluster-size", type=int, default=2,
        help="Minimum failures in a cluster before it earns a skill (default: 2).",
    )
    p_distill.add_argument(
        "--min-steps", type=int, default=None,
        help="Only distill reports from runs with at least this many steps (default: MAINTENANCE_MIN_STEPS / 10).",
    )
    p_distill.set_defaults(func=_cmd_distill)

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
