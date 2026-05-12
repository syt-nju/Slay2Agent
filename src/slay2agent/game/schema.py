"""State parser + compact prompt rendering (F-004).

Why this module exists:
* Strategy code (main agent / sub-agents) must not consume STS2MCP raw dicts.
* Prompts must stay token-bounded by design — drop pile contents, drop already
  chosen options, drop far map nodes, drop keyword definition strings, etc.

Parsing contract: ``parse(raw)`` always returns a ``ParsedState``. Unrecognized
``state_type`` falls through to ``UnknownView`` carrying the raw payload so the
agent can still see something instead of crashing. Field-level missing values
get safe defaults; the parser never raises on shape drift, only logs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Union

logger = logging.getLogger(__name__)


# ── Common dataclasses ──────────────────────────────────────────────────


@dataclass(frozen=True)
class RunInfo:
    act: int
    floor: int
    ascension: int


@dataclass(frozen=True)
class Status:
    name: str
    amount: int
    type: str
    description: str
    id: str | None = None


@dataclass(frozen=True)
class Intent:
    type: str
    label: str
    title: str
    description: str


@dataclass(frozen=True)
class Enemy:
    entity_id: str
    name: str
    hp: int
    max_hp: int
    block: int
    status: tuple[Status, ...] = ()
    intents: tuple[Intent, ...] = ()


@dataclass(frozen=True)
class Card:
    """Card across all card-bearing screens.

    Pile cards (draw / discard / exhaust) only fill ``name`` / ``cost`` /
    ``description``; hand & overlay cards add ``index`` / ``id`` / ``type`` /
    ``target_type`` / ``can_play`` / ``unplayable_reason``.
    """

    name: str
    cost: str
    description: str
    star_cost: str | None = None
    index: int | None = None
    id: str | None = None
    type: str | None = None
    rarity: str | None = None
    is_upgraded: bool | None = None
    target_type: str | None = None
    can_play: bool | None = None
    unplayable_reason: str | None = None


@dataclass(frozen=True)
class Relic:
    name: str
    description: str
    id: str | None = None
    counter: int | None = None


@dataclass(frozen=True)
class Potion:
    name: str
    description: str
    id: str | None = None


@dataclass(frozen=True)
class PlayerSnapshot:
    character: str
    hp: int
    max_hp: int
    block: int
    gold: int
    status: tuple[Status, ...] = ()
    relics: tuple[Relic, ...] = ()
    potions: tuple[Potion, ...] = ()
    max_potion_slots: int = 0
    energy: int | None = None
    max_energy: int | None = None
    hand: tuple[Card, ...] | None = None
    draw_pile_count: int | None = None
    discard_pile_count: int | None = None
    exhaust_pile_count: int | None = None


# ── State-specific views ───────────────────────────────────────────────


@dataclass(frozen=True)
class CharacterSlot:
    id: str
    name: str
    locked: bool
    hp: int
    starting_relic: str | None
    description: str


@dataclass(frozen=True)
class MenuOption:
    name: str
    enabled: bool = True


@dataclass(frozen=True)
class MenuView:
    menu_screen: str | None
    message: str
    options: tuple[MenuOption, ...]
    characters: tuple[CharacterSlot, ...] = ()


@dataclass(frozen=True)
class CombatView:
    round: int
    turn: str
    is_play_phase: bool
    enemies: tuple[Enemy, ...]


@dataclass(frozen=True)
class HandSelectView:
    mode: str
    prompt: str
    cards: tuple[Card, ...]
    can_confirm: bool
    enemies: tuple[Enemy, ...] = ()


@dataclass(frozen=True)
class MapNode:
    col: int
    row: int
    type: str
    index: int | None = None
    leads_to: tuple[tuple[int, int, str], ...] = ()


@dataclass(frozen=True)
class MapView:
    next_options: tuple[MapNode, ...]
    boss_pos: tuple[int, int] | None
    visited_count: int
    total_nodes: int


@dataclass(frozen=True)
class EventOption:
    index: int
    title: str
    description: str
    is_locked: bool
    is_proceed: bool
    was_chosen: bool


@dataclass(frozen=True)
class EventView:
    event_id: str
    event_name: str
    is_ancient: bool
    in_dialogue: bool
    body: str | None
    options: tuple[EventOption, ...]


@dataclass(frozen=True)
class RewardItem:
    index: int
    type: str
    description: str


@dataclass(frozen=True)
class RewardsView:
    items: tuple[RewardItem, ...]
    can_proceed: bool


@dataclass(frozen=True)
class CardRewardView:
    cards: tuple[Card, ...]
    can_skip: bool


@dataclass(frozen=True)
class CardSelectView:
    screen_type: str
    prompt: str
    cards: tuple[Card, ...]
    can_skip: bool
    can_confirm: bool
    can_cancel: bool
    preview_showing: bool


@dataclass(frozen=True)
class GameOverView:
    message: str
    options: tuple[str, ...]


@dataclass(frozen=True)
class RestSiteOption:
    index: int
    name: str
    is_enabled: bool


@dataclass(frozen=True)
class RestSiteView:
    options: tuple[RestSiteOption, ...]


@dataclass(frozen=True)
class ShopItem:
    index: int
    name: str
    price: int
    type: str
    sold_out: bool


@dataclass(frozen=True)
class ShopView:
    items: tuple[ShopItem, ...]
    remove_cost: int | None


@dataclass(frozen=True)
class TreasureRelic:
    index: int
    name: str
    description: str


@dataclass(frozen=True)
class TreasureView:
    relics: tuple[TreasureRelic, ...]


@dataclass(frozen=True)
class UnknownView:
    """Catch-all for state_types without a dedicated parser.

    Stores the raw dict (minus the well-known ``state_type`` / ``run`` /
    ``player`` keys already lifted to ``ParsedState``). Compact prompt
    surfaces only top-level key names so the LLM can ask for more.
    """

    payload: dict[str, Any] = field(default_factory=dict)


View = Union[
    MenuView,
    CombatView,
    HandSelectView,
    MapView,
    EventView,
    RewardsView,
    CardRewardView,
    CardSelectView,
    RestSiteView,
    ShopView,
    TreasureView,
    GameOverView,
    UnknownView,
]


@dataclass(frozen=True)
class ParsedState:
    state_type: str
    run: RunInfo | None
    player: PlayerSnapshot | None
    view: View
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


# ── Field-level helpers ─────────────────────────────────────────────────


_COMBAT_TYPES = frozenset({"monster", "elite", "boss"})


def _parse_run(raw: dict[str, Any] | None) -> RunInfo | None:
    if not raw:
        return None
    return RunInfo(
        act=int(raw.get("act", 0)),
        floor=int(raw.get("floor", 0)),
        ascension=int(raw.get("ascension", 0)),
    )


def _parse_status(raw: dict[str, Any]) -> Status:
    return Status(
        name=str(raw.get("name", "")),
        amount=int(raw.get("amount", 0)),
        type=str(raw.get("type", "")),
        description=str(raw.get("description", "")),
        id=raw.get("id"),
    )


def _parse_intent(raw: dict[str, Any]) -> Intent:
    return Intent(
        type=str(raw.get("type", "")),
        label=str(raw.get("label", "")),
        title=str(raw.get("title", "")),
        description=str(raw.get("description", "")),
    )


def _parse_enemy(raw: dict[str, Any]) -> Enemy:
    return Enemy(
        entity_id=str(raw.get("entity_id", "")),
        name=str(raw.get("name", "")),
        hp=int(raw.get("hp", 0)),
        max_hp=int(raw.get("max_hp", 0)),
        block=int(raw.get("block", 0)),
        status=tuple(_parse_status(s) for s in raw.get("status", []) or []),
        intents=tuple(_parse_intent(i) for i in raw.get("intents", []) or []),
    )


def _parse_card(raw: dict[str, Any]) -> Card:
    return Card(
        name=str(raw.get("name", "")),
        cost=str(raw.get("cost", "")),
        description=str(raw.get("description", "")),
        star_cost=raw.get("star_cost"),
        index=raw.get("index"),
        id=raw.get("id"),
        type=raw.get("type"),
        rarity=raw.get("rarity"),
        is_upgraded=raw.get("is_upgraded"),
        target_type=raw.get("target_type"),
        can_play=raw.get("can_play"),
        unplayable_reason=raw.get("unplayable_reason"),
    )


def _parse_relic(raw: dict[str, Any]) -> Relic:
    return Relic(
        name=str(raw.get("name", "")),
        description=str(raw.get("description", "")),
        id=raw.get("id"),
        counter=raw.get("counter"),
    )


def _parse_potion(raw: dict[str, Any]) -> Potion:
    return Potion(
        name=str(raw.get("name", "")),
        description=str(raw.get("description", "")),
        id=raw.get("id"),
    )


def _parse_player(raw: dict[str, Any] | None) -> PlayerSnapshot | None:
    if not raw:
        return None
    hand_raw = raw.get("hand")
    return PlayerSnapshot(
        character=str(raw.get("character", "")),
        hp=int(raw.get("hp", 0)),
        max_hp=int(raw.get("max_hp", 0)),
        block=int(raw.get("block", 0)),
        gold=int(raw.get("gold", 0)),
        status=tuple(_parse_status(s) for s in raw.get("status", []) or []),
        relics=tuple(_parse_relic(r) for r in raw.get("relics", []) or []),
        potions=tuple(_parse_potion(p) for p in raw.get("potions", []) or []),
        max_potion_slots=int(raw.get("max_potion_slots", 0)),
        energy=raw.get("energy"),
        max_energy=raw.get("max_energy"),
        hand=tuple(_parse_card(c) for c in hand_raw) if hand_raw is not None else None,
        draw_pile_count=raw.get("draw_pile_count"),
        discard_pile_count=raw.get("discard_pile_count"),
        exhaust_pile_count=raw.get("exhaust_pile_count"),
    )


# ── Per-state-type view parsers ─────────────────────────────────────────


def _parse_menu(raw: dict[str, Any]) -> MenuView:
    options_raw = raw.get("options", []) or []
    options: list[MenuOption] = []
    for opt in options_raw:
        if isinstance(opt, str):
            options.append(MenuOption(name=opt, enabled=True))
        elif isinstance(opt, dict):
            options.append(
                MenuOption(name=str(opt.get("name", "")), enabled=bool(opt.get("enabled", True)))
            )

    characters: list[CharacterSlot] = []
    for c in raw.get("characters", []) or []:
        starting = c.get("starting_relics") or []
        relic_name = starting[0].get("name") if starting else None
        characters.append(
            CharacterSlot(
                id=str(c.get("id", "")),
                name=str(c.get("name", "")),
                locked=bool(c.get("locked", False)),
                hp=int(c.get("hp", 0)),
                starting_relic=relic_name,
                description=str(c.get("description", "")),
            )
        )

    return MenuView(
        menu_screen=raw.get("menu_screen"),
        message=str(raw.get("message", "")),
        options=tuple(options),
        characters=tuple(characters),
    )


def _parse_combat(raw: dict[str, Any]) -> CombatView:
    battle = raw.get("battle", {}) or {}
    return CombatView(
        round=int(battle.get("round", 0)),
        turn=str(battle.get("turn", "")),
        is_play_phase=bool(battle.get("is_play_phase", False)),
        enemies=tuple(_parse_enemy(e) for e in battle.get("enemies", []) or []),
    )


def _parse_hand_select(raw: dict[str, Any]) -> HandSelectView:
    hs = raw.get("hand_select", {}) or {}
    battle = raw.get("battle") or {}
    return HandSelectView(
        mode=str(hs.get("mode", "")),
        prompt=str(hs.get("prompt", "")),
        cards=tuple(_parse_card(c) for c in hs.get("cards", []) or []),
        can_confirm=bool(hs.get("can_confirm", False)),
        enemies=tuple(_parse_enemy(e) for e in battle.get("enemies", []) or []),
    )


def _parse_map_node(raw: dict[str, Any], *, with_index: bool = False) -> MapNode:
    leads = []
    for child in raw.get("leads_to", []) or []:
        leads.append(
            (int(child.get("col", 0)), int(child.get("row", 0)), str(child.get("type", "")))
        )
    return MapNode(
        col=int(raw.get("col", 0)),
        row=int(raw.get("row", 0)),
        type=str(raw.get("type", "")),
        index=raw.get("index") if with_index else None,
        leads_to=tuple(leads),
    )


def _parse_map(raw: dict[str, Any]) -> MapView:
    m = raw.get("map", {}) or {}
    boss = m.get("boss") or {}
    boss_pos: tuple[int, int] | None = None
    if "col" in boss and "row" in boss:
        boss_pos = (int(boss["col"]), int(boss["row"]))
    return MapView(
        next_options=tuple(
            _parse_map_node(n, with_index=True) for n in m.get("next_options", []) or []
        ),
        boss_pos=boss_pos,
        visited_count=len(m.get("visited", []) or []),
        total_nodes=len(m.get("nodes", []) or []),
    )


def _parse_event(raw: dict[str, Any]) -> EventView:
    e = raw.get("event", {}) or {}
    options = tuple(
        EventOption(
            index=int(o.get("index", 0)),
            title=str(o.get("title", "")),
            description=str(o.get("description", "")),
            is_locked=bool(o.get("is_locked", False)),
            is_proceed=bool(o.get("is_proceed", False)),
            was_chosen=bool(o.get("was_chosen", False)),
        )
        for o in e.get("options", []) or []
    )
    return EventView(
        event_id=str(e.get("event_id", "")),
        event_name=str(e.get("event_name", "")),
        is_ancient=bool(e.get("is_ancient", False)),
        in_dialogue=bool(e.get("in_dialogue", False)),
        body=e.get("body"),
        options=options,
    )


def _parse_rewards(raw: dict[str, Any]) -> RewardsView:
    r = raw.get("rewards", {}) or {}
    items = tuple(
        RewardItem(
            index=int(it.get("index", 0)),
            type=str(it.get("type", "")),
            description=str(it.get("description", "")),
        )
        for it in r.get("items", []) or []
    )
    return RewardsView(items=items, can_proceed=bool(r.get("can_proceed", False)))


def _parse_card_reward(raw: dict[str, Any]) -> CardRewardView:
    r = raw.get("card_reward", {}) or {}
    return CardRewardView(
        cards=tuple(_parse_card(c) for c in r.get("cards", []) or []),
        can_skip=bool(r.get("can_skip", False)),
    )


def _parse_card_select(raw: dict[str, Any]) -> CardSelectView:
    cs = raw.get("card_select", {}) or {}
    return CardSelectView(
        screen_type=str(cs.get("screen_type", "")),
        prompt=str(cs.get("prompt", "")),
        cards=tuple(_parse_card(c) for c in cs.get("cards", []) or []),
        can_skip=bool(cs.get("can_skip", False)),
        can_confirm=bool(cs.get("can_confirm", False)),
        can_cancel=bool(cs.get("can_cancel", False)),
        preview_showing=bool(cs.get("preview_showing", False)),
    )


def _parse_rest_site(raw: dict[str, Any]) -> RestSiteView:
    rs = raw.get("rest_site", {}) or {}
    options = tuple(
        RestSiteOption(
            index=int(o.get("index", i)),
            name=str(o.get("name", o.get("label", "?"))),
            is_enabled=bool(o.get("is_enabled", True)),
        )
        for i, o in enumerate(rs.get("options", []) or [])
    )
    return RestSiteView(options=options)


def _parse_shop(raw: dict[str, Any]) -> ShopView:
    s = raw.get("shop", {}) or {}
    items_raw = s.get("items", s.get("inventory", []) or []) or []
    items = tuple(
        ShopItem(
            index=int(it.get("index", i)),
            name=str(it.get("name", "?")),
            price=int(it.get("price", it.get("cost", 0))),
            type=str(it.get("type", "")),
            sold_out=bool(it.get("sold_out", it.get("is_sold_out", False))),
        )
        for i, it in enumerate(items_raw)
    )
    remove_cost = s.get("remove_cost") or s.get("purge_cost")
    return ShopView(
        items=items,
        remove_cost=int(remove_cost) if remove_cost is not None else None,
    )


def _parse_treasure(raw: dict[str, Any]) -> TreasureView:
    t = raw.get("treasure", {}) or {}
    relics = tuple(
        TreasureRelic(
            index=int(r.get("index", i)),
            name=str(r.get("name", "?")),
            description=str(r.get("description", "")),
        )
        for i, r in enumerate(t.get("relics", []) or [])
    )
    return TreasureView(relics=relics)


def _parse_game_over(raw: dict[str, Any]) -> GameOverView:
    g = raw.get("game_over", {}) or {}
    return GameOverView(
        message=str(g.get("message", "")),
        options=tuple(str(o) for o in g.get("options", []) or []),
    )


_VIEW_PARSERS: dict[str, Any] = {
    "menu": _parse_menu,
    "monster": _parse_combat,
    "elite": _parse_combat,
    "boss": _parse_combat,
    "hand_select": _parse_hand_select,
    "map": _parse_map,
    "event": _parse_event,
    "rewards": _parse_rewards,
    "card_reward": _parse_card_reward,
    "card_select": _parse_card_select,
    "rest_site": _parse_rest_site,
    "shop": _parse_shop,
    "fake_merchant": _parse_shop,
    "treasure": _parse_treasure,
    "game_over": _parse_game_over,
}


# ── Public API ──────────────────────────────────────────────────────────


def parse(raw: dict[str, Any]) -> ParsedState:
    """Parse a raw STS2MCP state dict into a ``ParsedState``.

    Always returns a value. Unrecognized ``state_type`` falls through to
    ``UnknownView``; the agent never gets a None.
    """
    state_type = str(raw.get("state_type", "unknown"))
    run = _parse_run(raw.get("run"))
    player = _parse_player(raw.get("player"))

    parser = _VIEW_PARSERS.get(state_type)
    if parser is None:
        # Strip well-known top-level keys so the fallback prompt isn't dominated by
        # them; whatever remains is what the LLM gets surfaced.
        residual = {k: v for k, v in raw.items() if k not in {"state_type", "run", "player"}}
        view: View = UnknownView(payload=residual)
    else:
        view = parser(raw)

    return ParsedState(state_type=state_type, run=run, player=player, view=view, raw=raw)


# ── Compact prompt rendering ────────────────────────────────────────────


def _fmt_run(run: RunInfo | None) -> str:
    if run is None:
        return ""
    return f"Floor {run.floor} / Act {run.act} / Asc {run.ascension}"


def _fmt_player_header(player: PlayerSnapshot | None, *, in_combat: bool) -> str:
    if player is None:
        return ""
    parts = [
        f"{player.character}",
        f"HP {player.hp}/{player.max_hp}",
        f"Block {player.block}",
        f"Gold {player.gold}",
    ]
    if in_combat and player.energy is not None and player.max_energy is not None:
        parts.append(f"Energy {player.energy}/{player.max_energy}")
    return " | ".join(parts)


def _fmt_status_list(status: tuple[Status, ...]) -> str:
    if not status:
        return ""
    return ", ".join(f"{s.name} {s.amount}" for s in status)


def _fmt_relics(relics: tuple[Relic, ...]) -> str:
    if not relics:
        return "(none)"
    return ", ".join(r.name for r in relics)


def _fmt_potions(potions: tuple[Potion, ...], slots: int) -> str:
    if not potions:
        return f"(empty, {slots} slot{'s' if slots != 1 else ''})"
    entries = [f"{p.name} — {p.description}" if p.description else p.name for p in potions]
    return f"{'; '.join(entries)} ({len(potions)}/{slots})"


def _fmt_card_line(c: Card) -> str:
    """One-line card entry for hand / overlay lists."""
    bits: list[str] = []
    if c.index is not None:
        bits.append(f"[{c.index}]")
    bits.append(f"({c.cost})")
    bits.append(c.name)
    if c.is_upgraded:
        bits.append("+")
    bits.append(f"— {c.description}")
    if c.target_type and c.target_type != "Self" and c.target_type != "None":
        bits.append(f"[target:{c.target_type}]")
    if c.can_play is False:
        bits.append(f"[unplayable:{c.unplayable_reason or '?'}]")
    return " ".join(bits)


def _fmt_enemy(e: Enemy) -> str:
    head = f"{e.name} ({e.entity_id}) HP {e.hp}/{e.max_hp} Block {e.block}"
    extras: list[str] = []
    if e.status:
        extras.append(f"status: {_fmt_status_list(e.status)}")
    for it in e.intents:
        # Intent labels often carry the damage number; title is a category like "Aggressive".
        extras.append(f"intent: {it.type} {it.label} ({it.title})")
    if extras:
        return head + "\n  " + "; ".join(extras)
    return head


def _render_menu(state: ParsedState, view: MenuView) -> str:
    lines: list[str] = []
    screen = view.menu_screen or "?"
    lines.append(f"## Menu — screen: {screen}")
    if view.message:
        lines.append(view.message)

    if view.menu_screen == "character_select" and view.characters:
        lines.append("Characters:")
        for c in view.characters:
            tag = " [LOCKED]" if c.locked else ""
            relic = f" — start relic: {c.starting_relic}" if c.starting_relic else ""
            lines.append(f"  - {c.id} (HP {c.hp}){tag}{relic}")

    enabled_opts = [o.name for o in view.options if o.enabled]
    disabled_opts = [o.name for o in view.options if not o.enabled]
    if enabled_opts:
        lines.append(f"Options (enabled): {', '.join(enabled_opts)}")
    if disabled_opts:
        lines.append(f"Options (disabled): {', '.join(disabled_opts)}")
    return "\n".join(lines)


def _render_combat(state: ParsedState, view: CombatView) -> str:
    p = state.player
    lines: list[str] = []
    lines.append(f"## Combat ({state.state_type}) — {_fmt_run(state.run)}")
    lines.append(_fmt_player_header(p, in_combat=True))
    if p and p.status:
        lines.append(f"Player status: {_fmt_status_list(p.status)}")
    phase_label = "YOUR TURN — you may play cards or end_turn" if view.is_play_phase else "ENEMY TURN — wait, do NOT play cards or end_turn"
    lines.append(f"Round {view.round} | {phase_label}")
    lines.append(f"Relics: {_fmt_relics(p.relics) if p else '(none)'}")
    if p:
        lines.append(f"Potions: {_fmt_potions(p.potions, p.max_potion_slots)}")

    lines.append("Enemies:")
    for e in view.enemies:
        lines.append("  - " + _fmt_enemy(e))

    if p and p.hand is not None:
        lines.append("Hand:")
        for c in p.hand:
            lines.append("  - " + _fmt_card_line(c))
        lines.append(
            f"Piles: draw {p.draw_pile_count} / discard {p.discard_pile_count} / "
            f"exhaust {p.exhaust_pile_count}"
        )
    return "\n".join(lines)


def _render_hand_select(state: ParsedState, view: HandSelectView) -> str:
    lines = [f"## HandSelect — mode: {view.mode}"]
    if view.prompt:
        lines.append(view.prompt)
    lines.append(f"can_confirm: {view.can_confirm}")
    if view.enemies:
        lines.append("Enemies:")
        for e in view.enemies:
            lines.append("  - " + _fmt_enemy(e))
    lines.append("Cards (select by index):")
    for c in view.cards:
        lines.append("  - " + _fmt_card_line(c))
    return "\n".join(lines)


def _render_map(state: ParsedState, view: MapView) -> str:
    lines = [f"## Map — {_fmt_run(state.run)}"]
    p = state.player
    if p:
        lines.append(_fmt_player_header(p, in_combat=False))
        lines.append(f"Relics: {_fmt_relics(p.relics)}")
        lines.append(f"Potions: {_fmt_potions(p.potions, p.max_potion_slots)}")
    lines.append(
        f"Map size: {view.total_nodes} nodes total, {view.visited_count} visited"
    )
    if view.boss_pos:
        lines.append(f"Boss at col={view.boss_pos[0]} row={view.boss_pos[1]}")
    lines.append("Next options (use index for choose_map_node):")
    for n in view.next_options:
        leads = ", ".join(f"({c},{r}) {t}" for c, r, t in n.leads_to) or "(none)"
        lines.append(
            f"  - [{n.index}] col={n.col} row={n.row} type={n.type} → {leads}"
        )
    return "\n".join(lines)


def _render_event(state: ParsedState, view: EventView) -> str:
    lines = [f"## Event — {view.event_name} ({view.event_id})"]
    if view.is_ancient:
        lines.append("(Ancient encounter)")
    lines.append(f"In dialogue: {view.in_dialogue}")
    if view.body:
        lines.append(f"Body: {view.body}")
    p = state.player
    if p:
        lines.append(_fmt_player_header(p, in_combat=False))
    lines.append("Options:")
    for o in view.options:
        if o.was_chosen:
            continue
        tag = " [LOCKED]" if o.is_locked else ""
        proceed = " [PROCEED]" if o.is_proceed else ""
        lines.append(f"  - [{o.index}]{tag}{proceed} {o.title}: {o.description}")
    return "\n".join(lines)


def _render_rewards(state: ParsedState, view: RewardsView) -> str:
    lines = [f"## Rewards — can_proceed={view.can_proceed} — {_fmt_run(state.run)}"]
    p = state.player
    if p:
        lines.append(_fmt_player_header(p, in_combat=False))
    for it in view.items:
        lines.append(f"  - [{it.index}] {it.type}: {it.description}")
    return "\n".join(lines)


def _render_card_reward(state: ParsedState, view: CardRewardView) -> str:
    lines = [f"## CardReward — can_skip={view.can_skip}"]
    for c in view.cards:
        rarity = f" [{c.rarity}]" if c.rarity else ""
        lines.append(f"  - [{c.index}] ({c.cost}) {c.name}{rarity} — {c.description}")
    return "\n".join(lines)


def _render_card_select(state: ParsedState, view: CardSelectView) -> str:
    lines = [
        f"## CardSelect — screen: {view.screen_type} | "
        f"can_skip={view.can_skip} can_confirm={view.can_confirm} "
        f"can_cancel={view.can_cancel} preview={view.preview_showing}"
    ]
    if view.prompt:
        lines.append(view.prompt)
    for c in view.cards:
        rarity = f" [{c.rarity}]" if c.rarity else ""
        lines.append(f"  - [{c.index}] ({c.cost}) {c.name}{rarity} — {c.description}")
    return "\n".join(lines)


def _render_rest_site(state: ParsedState, view: RestSiteView) -> str:
    lines = [f"## Rest Site — {_fmt_run(state.run)}"]
    p = state.player
    if p:
        lines.append(_fmt_player_header(p, in_combat=False))
        lines.append(f"Relics: {_fmt_relics(p.relics)}")
        lines.append(f"Potions: {_fmt_potions(p.potions, p.max_potion_slots)}")
    enabled = [o for o in view.options if o.is_enabled]
    if enabled:
        lines.append("Options (use choose_rest_option):")
        for o in enabled:
            lines.append(f"  - [{o.index}] {o.name}")
    else:
        lines.append("No options remaining — use proceed to leave.")
    return "\n".join(lines)


def _render_shop(state: ParsedState, view: ShopView) -> str:
    label = "Fake Merchant" if state.state_type == "fake_merchant" else "Shop"
    lines = [f"## {label} — {_fmt_run(state.run)}"]
    p = state.player
    if p:
        lines.append(_fmt_player_header(p, in_combat=False))
        lines.append(f"Relics: {_fmt_relics(p.relics)}")
        lines.append(f"Potions: {_fmt_potions(p.potions, p.max_potion_slots)}")
    available = [it for it in view.items if not it.sold_out]
    sold_out = [it for it in view.items if it.sold_out]
    if available:
        lines.append("Items for sale (use shop_purchase):")
        for it in available:
            type_tag = f" [{it.type}]" if it.type else ""
            lines.append(f"  - [{it.index}] {it.name}{type_tag} — {it.price}g")
    else:
        lines.append("No items available.")
    if sold_out:
        lines.append(f"Sold out: {', '.join(it.name for it in sold_out)}")
    if view.remove_cost is not None:
        lines.append(f"Card removal: {view.remove_cost}g")
    lines.append("Use proceed to leave the shop.")
    return "\n".join(lines)


def _render_treasure(state: ParsedState, view: TreasureView) -> str:
    lines = [f"## Treasure — {_fmt_run(state.run)}"]
    p = state.player
    if p:
        lines.append(_fmt_player_header(p, in_combat=False))
    if view.relics:
        lines.append("Relics (use claim_treasure_relic):")
        for r in view.relics:
            desc = f" — {r.description}" if r.description else ""
            lines.append(f"  - [{r.index}] {r.name}{desc}")
    else:
        lines.append("Chest is empty — use proceed to leave.")
    return "\n".join(lines)


def _render_game_over(state: ParsedState, view: GameOverView) -> str:
    p = state.player
    lines = [f"## GameOver — {_fmt_run(state.run)}"]
    if view.message:
        lines.append(view.message)
    if p:
        lines.append(_fmt_player_header(p, in_combat=False))
    if view.options:
        lines.append(f"Options: {', '.join(view.options)}")
    return "\n".join(lines)


def _render_unknown(state: ParsedState, view: UnknownView) -> str:
    lines = [f"## Unknown state — state_type={state.state_type}"]
    if state.run:
        lines.append(_fmt_run(state.run))
    if state.player:
        lines.append(_fmt_player_header(state.player, in_combat=False))
    if view.payload:
        keys = ", ".join(sorted(view.payload.keys()))
        lines.append(f"Top-level fields: {keys}")
    return "\n".join(lines)


_RENDERERS: dict[type, Any] = {
    MenuView: _render_menu,
    CombatView: _render_combat,
    HandSelectView: _render_hand_select,
    MapView: _render_map,
    EventView: _render_event,
    RewardsView: _render_rewards,
    CardRewardView: _render_card_reward,
    CardSelectView: _render_card_select,
    RestSiteView: _render_rest_site,
    ShopView: _render_shop,
    TreasureView: _render_treasure,
    GameOverView: _render_game_over,
    UnknownView: _render_unknown,
}


def to_compact_prompt(state: ParsedState) -> str:
    """Render a compact, token-bounded text view of ``state`` for the main agent.

    Each view has its own renderer; pile contents / map nodes far from the
    current cursor / already-chosen event options / status keyword definitions
    are intentionally dropped to keep the message bounded by design rather than
    by runtime truncation.
    """
    renderer = _RENDERERS.get(type(state.view), _render_unknown)
    return renderer(state, state.view)


__all__ = [
    "Card",
    "CardRewardView",
    "CardSelectView",
    "CharacterSlot",
    "CombatView",
    "Enemy",
    "EventOption",
    "EventView",
    "GameOverView",
    "HandSelectView",
    "Intent",
    "MapNode",
    "MapView",
    "MenuOption",
    "MenuView",
    "ParsedState",
    "PlayerSnapshot",
    "Potion",
    "Relic",
    "RewardItem",
    "RestSiteOption",
    "RestSiteView",
    "RewardsView",
    "RunInfo",
    "ShopItem",
    "ShopView",
    "Status",
    "TreasureRelic",
    "TreasureView",
    "UnknownView",
    "View",
    "parse",
    "to_compact_prompt",
]
