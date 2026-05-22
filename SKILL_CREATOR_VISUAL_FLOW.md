# Skill Creator Trigger Logic — Visual Flow Diagrams

## Simplified Decision Tree

```
                         Start of Loop Iteration
                                 |
                                 v
                    ┌────────────────────────────┐
                    │ Fetch game state           │
                    │ Parse to state_type        │
                    └─────────┬──────────────────┘
                              |
                              v
                    ┌────────────────────────────┐
                    │ Is prev_state_type None?   │
                    │ (first iteration?)         │
                    └─────────┬──────────────────┘
                          YES / \ NO
                             /   \
                            /     \
                           v       \
                        ┌──────┐   \
                        │ SKIP │    \
                        └──────┘     v
                                 ┌────────────────────────────┐
                                 │ Did state_type change?     │
                                 │ (prev != current)          │
                                 └─────────┬──────────────────┘
                                       NO / \ YES
                                          /   \
                                         /     \
                                        v       \
                                    ┌──────┐    \
                                    │ SKIP │     v
                                    │ (no  │  ┌────────────────────────┐
                                    │trans)│  │ Is L0 non-empty?       │
                                    └──────┘  │ (any steps?)           │
                                             └─────────┬──────────────┘
                                                   NO / \ YES
                                                      /   \
                                                     /     \
                                                    v       v
                                                ┌────┐  ┌────────────────────┐
                                                │SKIP│  │ RUN SKILL_CREATOR  │
                                                │    │  │ (fire-and-forget)  │
                                                └────┘  └────────┬───────────┘
                                                                 |
                                                ┌────────────────┘
                                                |
                                                v
                                    ┌──────────────────────────┐
                                    │ Clear L0 = []            │
                                    │ Reset loop_detector      │
                                    │ Set prev_state = current │
                                    └──────────┬───────────────┘
                                               |
                                               v
                                    ┌──────────────────────────┐
                                    │ Main Agent LLM Call      │
                                    │ Execute tool             │
                                    └──────────┬───────────────┘
                                               |
                                               v
                                    ┌──────────────────────────┐
                                    │ Append to L0             │
                                    │ Record to trace          │
                                    └──────────┬───────────────┘
                                               |
                                               v
                                           LOOP
```

---

## State Diagram: L0 and skill_creator Lifecycle

```
┌───────────────────────────────────────────────────────────────────────┐
│ SEGMENT 1: map screen (5 agent steps)                                 │
│                                                                       │
│  Step 1: choose_map_node → L0 = [msg1, result1]                      │
│  Step 2: choose_map_node → L0 = [msg1, r1, msg2, result2]            │
│  Step 3: choose_map_node → L0 = [msg1, r1, msg2, r2, msg3, r3]       │
│  Step 4: choose_map_node → L0 = [msg1, r1, msg2, r2, ..., msg4, r4]  │
│  Step 5: choose_map_node → L0 = [msg1, r1, msg2, r2, ..., msg5, r5]  │
│                                                                       │
│  ↓ State transition detected: map → combat                            │
│  ↓ L0 is non-empty ✓                                                  │
│  ✅ SKILL_CREATOR TRIGGERED with L0 (5 steps)                        │
│                                                                       │
└───────────────────────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────────────────────────────┐
│ SEGMENT 2: combat screen (0 agent steps - idle transition)            │
│                                                                       │
│  [Enemy turn passes immediately]                                      │
│  State transition detected: combat → map                              │
│  L0 is empty ✗                                                        │
│  ❌ SKILL_CREATOR NOT TRIGGERED                                      │
│                                                                       │
│  ↓ Continue main loop with fresh L0                                   │
│                                                                       │
└───────────────────────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────────────────────────────┐
│ SEGMENT 3: map screen (2 agent steps)                                 │
│                                                                       │
│  Step 1: choose_map_node → L0 = [msg1, result1]                      │
│  Step 2: choose_map_node → L0 = [msg1, r1, msg2, result2]            │
│                                                                       │
│  ↓ State transition detected: map → card_reward                       │
│  ↓ L0 is non-empty ✓                                                  │
│  ✅ SKILL_CREATOR TRIGGERED with L0 (2 steps)                        │
│                                                                       │
└───────────────────────────────────────────────────────────────────────┘
```

---

## Execution Timeline: What Happens When skill_creator Triggers

```
MAIN AGENT LOOP (time →)
│
├─── Step N: map screen (agent acts)
│    ├─ LLM decision
│    ├─ Tool: choose_map_node
│    └─ Append to L0
│
├─── Step N+1: still on map (agent acts again)
│    ├─ LLM decision
│    ├─ Tool: choose_map_node
│    └─ Append to L0
│
├─── Step N+2: state transition detected!
│    ├─ Fetch state → state_type = "combat"
│    ├─ Check: prev_state_type ("map") != current ("combat") ✓
│    ├─ Check: L0 has 4 messages ✓
│    │
│    ├─ TRIGGER: skill_creator (non-blocking)
│    │                    │
│    │                    ├─→ [Async / Fire-and-Forget]
│    │                    ├─→ Read oracle.md
│    │                    ├─→ List existing skills
│    │                    ├─→ Multi-turn LLM: analyze L0 segment
│    │                    ├─→ Write/delete skills if needed
│    │                    ├─→ Log to subagent.jsonl
│    │                    └─→ Return (exceptions caught)
│    │
│    ├─ Clear L0 = []
│    ├─ Reset loop_detector
│    └─ Continue immediately ← MAIN LOOP DOES NOT WAIT
│
└─── Step N+3: now in combat, fresh L0
     ├─ LLM decision
     ├─ Tool: play_card
     └─ Append to L0
```

---

## Example Run: How Many skill_creator Calls?

```
GAME FLOW                           skill_creator TRIGGER?
─────────────────────────────────────────────────────────────
START
  ↓
Step 1: menu screen
  (first iteration, no prev_state)
  → NO TRIGGER (prev_state_type is None)
  ↓
Step 2: map screen  
  (state: None → map, L0 empty)
  → NO TRIGGER (L0 empty)
  ↓
Step 3: choose node (map)
  L0 = [1 message]
  ↓
Step 4: choose node (map)
  L0 = [2 messages]
  ↓
Step 5: state → combat
  (map → combat, L0 non-empty)
  ✅ TRIGGER #1 (skill_creator runs)
  L0 cleared
  ↓
Step 6-20: combat turns
  L0 grows: [msg1, msg2, msg3, ...]
  ↓
Step 21: victory → card_reward
  (combat → card_reward, L0 non-empty)
  ✅ TRIGGER #2 (skill_creator runs)
  L0 cleared
  ↓
Step 22: claim card (card_reward)
  L0 = [1 message]
  ↓
Step 23: state → map
  (card_reward → map, L0 non-empty)
  ✅ TRIGGER #3 (skill_creator runs)
  L0 cleared
  ↓
Step 24-26: map navigation
  L0 grows
  ↓
Step 27: state → boss_reward
  (map → boss_reward, L0 non-empty)
  ✅ TRIGGER #4 (skill_creator runs)
  L0 cleared
  ↓
Step 28: claim relic (boss_reward)
  L0 = [1 message]
  ↓
Step 29: state → map
  (boss_reward → map, L0 non-empty)
  ✅ TRIGGER #5 (skill_creator runs)
  L0 cleared
  ↓
... (similar pattern repeats for each segment)
  ↓
Step N: game_over
  (any_state → game_over, L0 non-empty)
  ✅ TRIGGER #K (final skill_creator run)
  L0 cleared

TOTAL skill_creator CALLS: Typically 5-20 per run
(One per major state transition with activity)
```

---

## Conditional Logic Flowchart

```
                    ┏━━━━━━━━━━━━━━━━━━━━━━┓
                    ┃ state_type changed?  ┃
                    ┗━━━━┳━━━━━━━━━━━━━━┳━━┛
                         │              │
                        NO          YES │
                         │              │ prev != current
                         │              ├─ prev not None
                         │              │
                         v              v
                    ┌─────────┐    ┌──────────────────┐
                    │ NO CALL │    │ L0 non-empty?    │
                    └─────────┘    └──┳───────────┬───┘
                                      │           │
                                     NO         YES
                                      │           │
                                      v           v
                                  ┌────────┐ ┌─────────────────┐
                                  │NO CALL │ │ RUN SKILL_      │
                                  └────────┘ │ CREATOR (F-008b)│
                                             └─────────────────┘
                                                     │
                                                     ↓
                                          [Multi-turn LLM]
                                          [Write/delete skills]
                                          [Log changes]
                                          [Fire-and-forget]
```

---

## State Machine: L0 Lifecycle

```
START
  |
  └─→ [L0 empty] ──────────────────────┐
      |                                 │
      ├─ Main agent acts                │
      │  └─→ Append to L0               │
      │                                 │
      ├─ Main agent acts                │
      │  └─→ Append to L0               │
      │      (L0 grows)                 │
      │                                 │
      ├─ Main agent acts                │
      │  └─→ Append to L0               │
      │                                 │
      └─→ [L0 non-empty] ───────────────┤
         |                              │
         ├─ STATE TRANSITION DETECTED   │
         │                              │
         ├─ Trigger skill_creator       │
         │  with L0 segment             │
         │                              │
         └─→ CLEAR L0 = []  ────────────┘
            |
            └─ (cycle repeats)
```

---

## Decision Matrix

```
┌────────────────────┬────────────────┬─────────────┬──────────────┐
│ prev_state_type    │ state_type     │ L0          │ skill_creator│
├────────────────────┼────────────────┼─────────────┼──────────────┤
│ None               │ menu           │ any         │ ❌ NO        │
│ None               │ map            │ any         │ ❌ NO        │
│ menu               │ menu           │ any         │ ❌ NO        │
│ menu               │ map            │ empty       │ ❌ NO        │
│ menu               │ map            │ non-empty   │ ✅ YES       │
│ map                │ map            │ non-empty   │ ❌ NO        │
│ map                │ combat         │ empty       │ ❌ NO        │
│ map                │ combat         │ non-empty   │ ✅ YES       │
│ combat             │ card_reward    │ empty       │ ❌ NO        │
│ combat             │ card_reward    │ non-empty   │ ✅ YES       │
│ card_reward        │ map            │ non-empty   │ ✅ YES       │
│ any_state          │ game_over      │ non-empty   │ ✅ YES       │
└────────────────────┴────────────────┴─────────────┴──────────────┘
```

---

## Performance Impact: When Does skill_creator Cost?

```
COST WHEN TRIGGERED:
  ├─ Read oracle.md (~100 bytes)
  ├─ Summarize L0 (~500–2000 bytes, depending on segment size)
  ├─ LLM call(s) for analysis (~2–5 turns)
  │  ├─ list_skills (~200–500 output tokens)
  │  ├─ read_skill (maybe 0–2 calls)
  │  └─ write_skill / delete_skill (maybe 0–3 calls)
  ├─ Disk I/O (~10–50ms per write)
  ├─ Log to subagent.jsonl
  └─ Total time: ~1–3 seconds per call

COST WHEN NOT TRIGGERED:
  └─ Zero (skipped completely)

TYPICAL RUN:
  ├─ ~5–20 state transitions with activity
  ├─ ~5–20 skill_creator calls triggered
  ├─ ~5,000–20,000 tokens for skill_creator
  └─ Main agent: ~10,000–50,000 tokens
     (skill_creator is ~10–30% of run tokens, but async)
```

---

## Summary: When Does skill_creator Run?

### ✅ WILL RUN

- After completing a combat phase (many steps) → transition to rewards
- After navigating a map (multiple choices) → transition to combat  
- After selecting cards → transition back to map
- After upgrading relics → transition to map
- Any time you finish meaningful gameplay before state changes

### ❌ WON'T RUN

- First step of run (no prev_state)
- Staying on same screen (no state change)
- Immediate transitions with 0 steps (L0 empty)
- Idle timeouts where game auto-transitioned

---

## Conclusion

**skill_creator is Selective, Not Always-On**

It's designed to trigger at **meaningful boundaries** where you can actually extract strategy from what just happened. If nothing happened (empty segment) or if you're still on the same screen, it doesn't run.

This is **efficient by design**: resources are spent on learning moments, not on processing every single transition.
