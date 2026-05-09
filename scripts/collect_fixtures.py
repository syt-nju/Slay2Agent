"""Drive the live STS2MCP mod blindly to collect real state fixtures.

Goal: breadth of `state_type` coverage for F-004, not winning the run.
Each unseen (state_type, sub-discriminator) is saved once to
`tests/fixtures/real/state_<key>.json`. Picks deterministic dumb actions
to push past every state we know about; aborts cleanly on `overlay`,
`unknown`, or unhandled `menu_screen`.

Usage:
    python scripts/collect_fixtures.py            # walk one run
    python scripts/collect_fixtures.py --dry-run  # only get state, no POST
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

from slay2agent.config import Config
from slay2agent.game import GameClient
from slay2agent.game.client import ActionError

logger = logging.getLogger("collect_fixtures")

FIXTURE_DIR = Path("tests/fixtures/real")


def _key(state: dict[str, Any]) -> str:
    """Stable filename key. Different sub-screens deserve their own fixture."""
    st = state.get("state_type", "unknown")
    if st == "menu":
        return f"menu_{state.get('menu_screen', 'unknown')}"
    if st == "card_select":
        mode = (state.get("card_select") or {}).get("mode", "default")
        return f"card_select_{mode}"
    if st == "hand_select":
        mode = (state.get("hand_select") or {}).get("mode", "default")
        return f"hand_select_{mode}"
    return st


def _save(state: dict[str, Any]) -> bool:
    path = FIXTURE_DIR / f"state_{_key(state)}.json"
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")
    logger.info("saved %s", path.name)
    return True


def _menu_options(state: dict[str, Any]) -> list[str]:
    """Normalise main-menu (list[str]) and char-select (list[dict]) shapes."""
    raw = state.get("options") or []
    out: list[str] = []
    for o in raw:
        if isinstance(o, str):
            out.append(o)
        elif isinstance(o, dict) and o.get("enabled", True):
            name = o.get("name")
            if name:
                out.append(name)
    return out


def _pick_menu(state: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    screen = state.get("menu_screen")
    opts = _menu_options(state)

    if screen == "main":
        return ("menu_select", {"option": "singleplayer"}) if "singleplayer" in opts else None
    if screen == "singleplayer":
        return ("menu_select", {"option": "standard"}) if "standard" in opts else None
    if screen == "character_select":
        # Confirm/embark first: a character is preselected on entry.
        # Re-clicking the character only toggles UI state and won't advance.
        for confirm in ("embark", "confirm"):
            if confirm in opts:
                return ("menu_select", {"option": confirm})
        # If embark isn't surfaced, fall back to picking a character.
        if "IRONCLAD" in opts:
            return ("menu_select", {"option": "IRONCLAD"})
        for o in opts:
            if o not in {"confirm", "embark", "back", "unready", "RANDOM"}:
                return ("menu_select", {"option": o})
        return None
    if screen == "tutorial_prompt":
        return ("menu_select", {"option": "no"})
    if screen == "popup":
        for opt in ("ignore", "ok", "confirm", "back"):
            if opt in opts:
                return ("menu_select", {"option": opt})
        return None
    return None


def _pick_action(
    state: dict[str, Any],
    seen_keys: set[str],
) -> tuple[str, dict[str, Any]] | None:
    """Pick the dumbest 'push forward' action. None means we don't know how."""
    st = state.get("state_type")

    if st == "menu":
        return _pick_menu(state)
    if st in ("monster", "elite", "boss"):
        battle = state.get("battle") or {}
        if not (battle.get("turn") == "player" and battle.get("is_play_phase", True)):
            return ("__wait__", {})
        # Try to actually play a card — blindly ending turn loses every fight
        # and we never see the rewards / map / event branches.
        player = state.get("player") or {}
        enemies = battle.get("enemies") or []
        first_enemy_id = next(
            (e.get("entity_id") for e in enemies if (e.get("hp") or 0) > 0),
            None,
        )
        for card in player.get("hand") or []:
            if not card.get("can_play", False):
                continue
            params: dict[str, Any] = {"card_index": card.get("index", 0)}
            if card.get("target_type") in {"AnyEnemy", "Enemy"} and first_enemy_id:
                params["target"] = first_enemy_id
            return ("play_card", params)
        return ("end_turn", {})
    if st == "hand_select":
        hs = state.get("hand_select") or {}
        # Confirm as soon as the prompt allows it; otherwise toggle one card.
        if hs.get("can_confirm"):
            return ("combat_confirm_selection", {})
        cards = hs.get("cards") or []
        if cards:
            return ("combat_select_card", {"card_index": cards[0].get("index", 0)})
        return ("combat_confirm_selection", {})
    if st == "rewards":
        items = (state.get("rewards") or {}).get("items") or []
        # If only card rewards remain and we already have a card_reward fixture,
        # proceed to avoid the rewards <-> card_reward ping-pong loop.
        non_card = [r for r in items if r.get("type") != "card"]
        if non_card:
            return ("claim_reward", {"index": non_card[0].get("index", 0)})
        if items and "card_reward" not in seen_keys:
            return ("claim_reward", {"index": items[0].get("index", 0)})
        return ("proceed", {})
    if st == "card_reward":
        if (state.get("card_reward") or {}).get("can_skip", True):
            return ("skip_card_reward", {})
        return ("select_card_reward", {"card_index": 0})
    if st == "map":
        next_opts = (state.get("map") or {}).get("next_options") or []
        if next_opts:
            idx = next_opts[0].get("index", 0)
            return ("choose_map_node", {"index": idx})
        return None
    if st == "event":
        if (state.get("event") or {}).get("in_dialogue"):
            return ("advance_dialogue", {})
        for opt in (state.get("event") or {}).get("options") or []:
            if not opt.get("is_locked", False):
                return ("choose_event_option", {"index": opt.get("index", 0)})
        return None
    if st == "rest_site":
        for opt in (state.get("rest_site") or {}).get("options") or []:
            if opt.get("is_enabled", True):
                return ("choose_rest_option", {"index": opt.get("index", 0)})
        return ("proceed", {})
    if st in ("shop", "fake_merchant"):
        return ("proceed", {})  # don't buy anything, just leave
    if st == "treasure":
        relics = (state.get("treasure") or {}).get("relics") or []
        if relics:
            return ("claim_treasure_relic", {"index": 0})
        return ("proceed", {})
    if st == "card_select":
        cs = state.get("card_select") or {}
        if cs.get("can_confirm"):
            return ("confirm_selection", {})
        if cs.get("can_cancel"):
            return ("cancel_selection", {})
        cards = cs.get("cards") or []
        if cards:
            return ("select_card", {"index": cards[0].get("index", 0)})
        return None
    if st == "bundle_select":
        bs = state.get("bundle_select") or {}
        if bs.get("can_confirm"):
            return ("confirm_bundle_selection", {})
        if bs.get("can_cancel"):
            return ("cancel_bundle_selection", {})
        bundles = bs.get("bundles") or []
        if bundles:
            return ("select_bundle", {"index": bundles[0].get("index", 0)})
        return None
    if st == "relic_select":
        return ("skip_relic_selection", {})
    if st == "crystal_sphere":
        return ("crystal_sphere_proceed", {})
    if st == "game_over":
        return ("menu_select", {"option": "main_menu"})
    if st == "unknown":
        return ("__wait__", {})  # transient room loading; just re-poll
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-steps", type=int, default=300)
    parser.add_argument("--settle", type=float, default=0.4)
    parser.add_argument(
        "--continue-after-gameover",
        action="store_true",
        help="Don't stop on game_over; keep walking to capture menu_main again.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = Config.load()
    saved_total = 0
    seen_keys: set[str] = set()
    posts_in_state = 0  # only counts real POSTs that didn't change state_key
    waits_in_a_row = 0
    last_key: str | None = None
    POST_LIMIT = 40  # combats can take many turns when we never play a card
    WAIT_LIMIT = 30  # ~30s of patience for animations / transitions

    with GameClient(cfg.game.base_url, timeout=cfg.game.timeout) as client:
        for step in range(args.max_steps):
            state = client.get_state()
            key = _key(state)
            if key != last_key:
                posts_in_state = 0
                waits_in_a_row = 0
                last_key = key
            if key not in seen_keys:
                seen_keys.add(key)
                if _save(state):
                    saved_total += 1
            logger.info("step=%-3d state=%-30s saved_total=%d", step, key, saved_total)

            if args.dry_run:
                break

            if state.get("state_type") == "game_over" and not args.continue_after_gameover:
                logger.info("game_over reached; stopping (use --continue-after-gameover to walk on)")
                break

            picked = _pick_action(state, seen_keys)
            if picked is None or picked[0] == "__wait__":
                waits_in_a_row += 1
                if waits_in_a_row > WAIT_LIMIT:
                    logger.error(
                        "waited %d times on state=%s without progress — aborting",
                        waits_in_a_row,
                        key,
                    )
                    break
                logger.info("  -> wait %d/%d", waits_in_a_row, WAIT_LIMIT)
                time.sleep(max(args.settle, 0.8))
                continue
            waits_in_a_row = 0

            if posts_in_state >= POST_LIMIT:
                logger.error(
                    "%d POSTs in state=%s without state change — aborting",
                    posts_in_state,
                    key,
                )
                break

            action, params = picked
            logger.info("  -> POST %s %s", action, params or "")
            try:
                client.post_action(action, **params)
            except ActionError as exc:
                # Some errors are inherent races (we read state before the
                # engine flipped to enemy turn). Don't abort — just sleep.
                msg = str(exc).lower()
                transient_markers = (
                    "play phase",
                    "enemy turn",
                    "not your turn",
                    "actions are currently disabled",
                    "turn may already be ending",
                )
                if any(s in msg for s in transient_markers):
                    logger.info("  -> transient action race (%s) — wait", exc)
                    time.sleep(max(args.settle, 0.8))
                    continue
                logger.error("action rejected: %s — aborting", exc)
                break
            posts_in_state += 1
            time.sleep(args.settle)

    logger.info("done. unique state keys saved: %d", saved_total)
    logger.info("fixtures dir: %s", FIXTURE_DIR.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
