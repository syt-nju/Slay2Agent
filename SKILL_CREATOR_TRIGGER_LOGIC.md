# Skill Creator Trigger Logic Analysis — Slay2Agent

## Executive Summary

**The `skill_creator` subagent is NOT always-on.** It is **conditionally triggered** with a very specific trigger condition:

### The Trigger Condition (CONDITIONAL)

```
skill_creator is called when:
  1. A state_type transition occurs (prev_state_type != current state_type)
  2. AND the previous segment had at least one step (L0 is non-empty)
```

If either condition is false, skill_creator does NOT run.

---

## Where is skill_creator Called?

**File**: `src/slay2agent/agent/loop.py`, lines 276–299

**Location in agent loop**: After fetching the current game state but BEFORE the main LLM decision call.

```python
# ── L0 clear on state_type transition ───────────────────────
l0_cleared = False
if prev_state_type is not None and state_type != prev_state_type:
    if l0:  # ← CRITICAL: Only if L0 is non-empty
        prev_l0_segment = l0  # capture before clear
        logger.info("state_type changed %s → %s — clearing L0 (%d messages)",
                    prev_state_type, state_type, len(l0))
        l0 = []
        l0_cleared = True
        observer.on_memory_event("L0_cleared", f"{prev_state_type} → {state_type}")
        
        # F-008b: run skill creator on the completed segment
        run_skill_creator(
            prev_l0=prev_l0_segment,
            skill_registry=skill_registry,
            oracle_path=oracle_path,
            adapter=adapter,
            tracker=tracker,
            trace=trace,
            model=cfg.llm.model,
            prev_state_type=prev_state_type,
            new_state_type=state_type,
            observer=observer,
            extra_body=cfg.llm.subagent_extra_body,
        )
    # Reset loop detector so actions from one screen don't
    # pollute the window for the next.
    loop_detector.reset()

prev_state_type = state_type
```

---

## The Two-Part Trigger Condition (Boolean AND)

### Part 1: State Type Transition

```python
if prev_state_type is not None and state_type != prev_state_type:
```

- **`prev_state_type is not None`**: Skip on the very first iteration (no previous state)
- **`state_type != prev_state_type`**: The game moved to a different screen/phase
  - e.g., `combat` → `map`, `map` → `card_reward`, `card_reward` → `combat`, etc.

### Part 2: L0 Non-Empty (Segment Had Activity)

```python
if l0:  # ← Must be truthy (non-empty list)
```

- **`l0`** = In-context conversation history (accumulated messages during a state_type)
- **Empty L0**: No actions were taken during the segment (player was idle, game transitioned immediately)
  - Example: You enter a map, immediately exit to menu → L0 is empty → skill_creator does NOT run
- **Non-empty L0**: At least one step was taken (LLM decision → tool call → result appended)
  - Example: You spend 3 steps navigating combat, then victory → L0 has 3 steps → skill_creator RUNS

---

## Example Trigger Scenarios

### ✅ TRIGGERS (runs skill_creator)

| Scenario | State Transition | L0 | Why |
|----------|------------------|----|-----|
| Complete combat encounter | `combat` → `game_over` | Non-empty | Both conditions met |
| Navigate map & choose node | `map` → `combat` | Non-empty | Multiple steps taken on map |
| Card rewards selection | `card_reward` → `map` | Non-empty | User selected/skipped cards |
| Relics & upgrades | `upgrade` → `map` | Non-empty | User made choices |

### ❌ DOES NOT TRIGGER (no skill_creator)

| Scenario | State Transition | L0 | Why |
|----------|------------------|----|-----|
| First step of run | `None` → `menu` | Empty | `prev_state_type is None` |
| Idle transition | `map` → `map` | Empty | No state change (same screen) |
| Empty segment | `combat` → `map` | Empty | No L0 activity (timeout, edge case) |
| Game not started | No transition | N/A | No state_type change |

---

## Visual: State Machine with skill_creator Triggers

```
                              START (prev_state_type = None)
                                    |
                                    v
                    ┌──────────────────────────────────┐
                    │  Step 1: Fetch game state        │
                    │  Parse to state_type             │
                    └──────────────────────────────────┘
                                    |
                                    v
                    ┌──────────────────────────────────┐
                    │ Check: state_type changed?       │
                    │ (prev != current)                │
                    └──────────────────────────────────┘
                        /              \
                       /NO              \ YES (+ prev_state_type != None)
                      /                  \
                     /                    v
                    |        ┌──────────────────────────┐
                    |        │ Check: L0 non-empty?     │
                    |        │ (any steps in segment?)  │
                    |        └──────────────────────────┘
                    |             /            \
                    |        YES /              \ NO
                    |           /                \
                    |          v                  v
                    |    ┌────────────────┐  ┌──────────┐
                    |    │ RUN SKILL_     │  │ SKIP     │
                    |    │ CREATOR        │  │ skill_   │
                    |    │ (F-008b)       │  │ creator  │
                    |    │ Fire-and-      │  │          │
                    |    │ forget         │  └──────────┘
                    |    └────────────────┘       |
                    |             |               |
                    |             +-------────────+
                    |                     |
                    v                     v
        ┌──────────────────────────────────────────────┐
        │ Reset L0 = []                                │
        │ Reset loop_detector                          │
        │ Set prev_state_type = current state_type    │
        └──────────────────────────────────────────────┘
                            |
                            v
        ┌──────────────────────────────────────────────┐
        │ Main Agent LLM Call + Tool Dispatch          │
        │ (Regular gameplay loop continues)           │
        └──────────────────────────────────────────────┘
                            |
                            v
                      Append to L0
                            |
                            v
                    Loop back to Step 1
```

---

## Data Flow: What Does skill_creator Receive?

When triggered, skill_creator receives:

```python
run_skill_creator(
    prev_l0=prev_l0_segment,              # List[Message] - completed conversation
    skill_registry=skill_registry,         # SkillRegistry - shared memory
    oracle_path=oracle_path,               # Path to oracle.md (read-only)
    adapter=adapter,                       # LLMAdapter - Claude API connection
    tracker=tracker,                       # UsageTracker - token accounting
    trace=trace,                           # TraceWriter - logging to disk
    model=cfg.llm.model,                   # "claude-3-5-sonnet-20241022"
    prev_state_type=prev_state_type,       # e.g. "combat"
    new_state_type=state_type,             # e.g. "map"
    observer=observer,                     # RunObserver - UI notifications
    extra_body=cfg.llm.subagent_extra_body # Extra LLM config
)
```

**Key insight**: skill_creator ONLY sees the **completed segment** (`prev_l0`), not the current state.
- It does NOT interrupt the main agent loop
- It is **fire-and-forget** (exceptions are caught and logged, never re-raised)
- It runs **in parallel** from the main agent's perspective (non-blocking)

---

## What Does skill_creator Do?

Once called with the completed segment:

1. **Reads oracle.md** (global strategy — read-only)
2. **Inspects skill library** via `list_skills()`
3. **Compares** learned insights against existing skills
4. **Decides** for each insight:
   - Extend an existing skill
   - Merge two similar skills
   - Create a new skill
   - No-op (ignore)
5. **Writes or deletes skill files** via `write_skill()` / `delete_skill()`
6. **Logs all changes** to `subagent.jsonl`

---

## Key Implementation Details

### L0 (In-Context Message History)

- **Cleared on every state_type transition** (lines 276–284)
- Contains: user prompts, LLM responses, tool calls, tool results
- **Limited size**: Compacted if exceeds threshold (lines 353–374)
- Starts fresh on each "screen" (combat phase, map, etc.)

### Skill Registry Reload

```python
# NOTE: skill_registry.reload() is intentionally NOT called on
# state_type transitions to preserve KV cache hit rate (the
# system prompt skill list stays stable within a run). Reload
# happens once at run end (see finally block).
```

- Skills are **NOT reloaded mid-run** to maintain LLM KV cache efficiency
- Final reload happens in the `finally` block (line 591) **after the run ends**
- This ensures the next run sees all skills created during this run

### Loop Detector Reset

```python
loop_detector.reset()
```

On every state_type transition, the loop detector is reset. This prevents actions from one screen from triggering a false-positive loop detection on the next screen.

---

## Summary Table: Is skill_creator Triggered?

| Condition | Triggered? |
|-----------|-----------|
| Every step | ❌ No |
| Every state_type transition | ❌ No (only if L0 non-empty) |
| State_type change + L0 non-empty | ✅ Yes |
| State_type change + L0 empty | ❌ No |
| Within same state_type (loop turns) | ❌ No |

---

## Trigger Logic Pseudocode

```python
while game_running:
    current_state = get_current_game_state()
    current_state_type = parse_state_type(current_state)
    
    # Check trigger conditions
    state_changed = (prev_state_type is not None 
                     and current_state_type != prev_state_type)
    has_activity = bool(l0)  # Non-empty segment
    
    if state_changed:
        if has_activity:
            # ✅ TRIGGER: Both conditions met
            run_skill_creator(prev_l0=l0, prev_state_type=prev_state_type, ...)
        else:
            # ❌ No trigger: Empty segment
            pass
        
        l0 = []  # Reset for next segment
        loop_detector.reset()
    
    prev_state_type = current_state_type
    
    # ... rest of main agent loop ...
```

---

## Timeline Example: skill_creator Triggers During a Run

```
Step 0:  START
         prev_state_type = None
         L0 = []
         ↓
Step 1:  state_type = "menu"
         No state change (prev == None)
         → NO skill_creator trigger
         ↓
Step 2:  state_type = "map"
         State changed (None → map)
         L0 = []  (empty)
         → NO skill_creator trigger ❌
         ↓
Step 3:  Took action: choose_map_node
         state_type = "map"
         No state change
         L0 = [user msg, assistant msg, tool result]
         ↓
Step 4:  Took action: choose_map_node again
         state_type = "combat"
         State changed (map → combat)
         L0 = non-empty (2 steps from map)
         → ✅ SKILL_CREATOR TRIGGERS ✅
         L0 reset to []
         ↓
Step 5:  state_type = "combat"
         No state change
         L0 = [combat step 1]
         ↓
Step 6:  Took action: play_card
         state_type = "combat"
         No state change
         L0 = [combat step 1, combat step 2, ...]
         ↓
Step 7:  Took action: end_turn
         state_type = "combat"
         No state change
         L0 grows more
         ↓
Step 8:  Combat ends
         state_type = "card_reward"
         State changed (combat → card_reward)
         L0 = non-empty (many combat steps)
         → ✅ SKILL_CREATOR TRIGGERS ✅
         L0 reset to []
         ↓
Step 9:  Took action: claim reward
         state_type = "map"
         State changed (card_reward → map)
         L0 = [1 step from card_reward]
         → ✅ SKILL_CREATOR TRIGGERS ✅
         L0 reset to []
         ↓
... continues ...
```

---

## References

- **Main loop**: `src/slay2agent/agent/loop.py`, lines 176–634
- **Trigger code**: Lines 276–299
- **skill_creator implementation**: `src/slay2agent/agent/skill_creator.py`
- **L0 compaction**: Lines 353–374
- **Run finalization**: Lines 588–632 (includes skill_registry.reload())

---

## Answer to Your Question

> "Is it called on every state transition, or only sometimes?"

**ANSWER**: **Only sometimes.** 

skill_creator is called on state transitions **only if the previous segment had at least one step** (L0 is non-empty). If the player immediately transitions between screens without taking any actions, skill_creator does NOT run for that transition.

This design ensures that skill_creator only learns from **meaningful gameplay segments**, not from empty or trivial transitions.

---

## Design Rationale

**Why this trigger design?**

1. **Efficiency**: Avoid running an expensive LLM call for empty segments
2. **Signal Quality**: Only train on segments that contain actual gameplay decisions
3. **Resource Management**: Reduce token usage for trivial transitions
4. **Learning Focus**: skill_creator focuses on "interesting" moments (combat, decisions, rewards)

**Why not reload skills mid-run?**

1. **KV Cache Hit Rate**: Reloading would invalidate the cache, forcing re-encode of skill list
2. **Consistency**: Skills stay stable during a run so agent behavior doesn't suddenly change
3. **Efficiency**: One reload at run end (line 591) is cheaper than reloading 100+ times mid-run

