# Skill Creator — Cheat Sheet

## The One Question

**Q: Is skill_creator called every time the state changes?**

**A: NO.** Only when BOTH conditions are true:
1. State changed (prev_state_type ≠ current state_type)
2. Previous segment had activity (L0 non-empty)

---

## The Code

```python
# src/slay2agent/agent/loop.py, lines 276-299

if prev_state_type is not None and state_type != prev_state_type:
    if l0:  # ← This is the gate
        run_skill_creator(prev_l0=l0, ...)
```

---

## Decision Logic (Pseudocode)

```python
# Checked every loop iteration
if not state_changed():
    pass  # no trigger (still on same screen)
elif not l0:
    pass  # no trigger (empty segment)
else:
    trigger_skill_creator()  # ✅ Both conditions met
```

---

## When It Runs

| Scenario | State Changed? | L0 Empty? | Trigger? |
|----------|---|---|---|
| First iteration (menu) | ✗ | ✓ | ❌ |
| Same screen twice | ✗ | ✗ | ❌ |
| State change, but no steps | ✓ | ✓ | ❌ |
| State change + steps taken | ✓ | ✗ | ✅ YES |

---

## Real Example

```
Step 5:  Choose map node
         state_type: map
         L0: [msg1, result1]
         ↓
Step 6:  Choose map node again
         state_type: map (NO CHANGE)
         L0: [msg1, r1, msg2, result2]
         ↓
Step 7:  Combat starts
         state_type: combat (CHANGED!)
         L0: [msg1, r1, msg2, r2]
         L0 non-empty (2 decisions made) ✓
         ✅ SKILL_CREATOR RUNS with L0 segment
```

---

## Key Variables

| Variable | Meaning | When Reset | Type |
|----------|---------|-----------|------|
| `state_type` | Current screen (map, combat, etc.) | Each iteration | str |
| `prev_state_type` | Previous screen | Each iteration | str \| None |
| `l0` | Conversation history for segment | On state change | list[Message] |
| `l0_cleared` | Flag: did we just clear L0? | Each iteration | bool |

---

## The L0 Buffer

```
L0 = [] (empty at segment start)

Agent step 1: L0 = [msg, result]      (1 message pair)
Agent step 2: L0 = [msg, r, msg, r]   (2 message pairs)
Agent step 3: L0 = [msg, r, msg, r, msg, r]  (3 pairs)

When state changes:
  - skill_creator triggered IF L0 non-empty
  - Then: L0 = [] (reset for next segment)
```

---

## Cost When Triggered

| Item | Cost |
|------|------|
| Time per call | ~1–3 seconds |
| Input tokens | ~500–1000 |
| Output tokens | ~200–500 |
| Disk I/O | ~10–50ms per write |
| Frequency per run | ~5–20 times |

---

## Files Involved

```
src/slay2agent/agent/loop.py          ← Main trigger (lines 276–299)
src/slay2agent/agent/skill_creator.py ← Implementation (runs when triggered)
src/slay2agent/memory/skill_registry.py ← Skill management
agent_state/skills/                   ← Where skills are stored
```

---

## Exports / External API

```python
# Main loop calls this
from slay2agent.agent.skill_creator import run_skill_creator

run_skill_creator(
    prev_l0=prev_l0_segment,           # The conversation history
    skill_registry=skill_registry,      # Access to skills
    oracle_path=oracle_path,            # Read-only strategy reference
    adapter=adapter,                    # LLM connection
    tracker=tracker,                    # Token accounting
    trace=trace,                        # Logging
    model=cfg.llm.model,                # Which LLM
    prev_state_type=prev_state_type,    # e.g., "combat"
    new_state_type=state_type,          # e.g., "map"
    observer=observer,                  # UI notifications
    extra_body=cfg.llm.subagent_extra_body,  # Config
)
```

---

## Is It Blocking?

**NO — fire-and-forget design**
- Exceptions caught + logged
- Main loop continues immediately
- Takes 1–3 seconds (multi-turn LLM)
- Async/parallel from main agent perspective

---

## Skill Registry Reload Policy

**Mid-run**: ❌ NOT reloaded (preserves KV cache)
**Run end**: ✅ Reloaded once (line 591 in finally block)

This ensures:
- KV cache stays warm during run (efficient)
- Next run sees all newly created skills
- Stable skill list across gameplay phases

---

## Debug / Logging

Watch the logs for:
```
INFO: state_type changed map → combat — clearing L0 (5 messages)
     [means skill_creator will be called]

INFO: skill_creator: triggered by map → combat, L0 len=5
     [skill_creator is running]

INFO: skill_creator: finished after 3 step(s); changes: write_skill(...)
     [skill_creator completed, what it did]
```

---

## FAQ

**Q: Why not always run skill_creator on state change?**
A: Efficiency. Don't waste LLM tokens on empty transitions. Only learn from segments with actual decisions.

**Q: Why not reload skills mid-run?**
A: KV cache efficiency. Reloading would force re-encode of skill list. One reload at run end is cheaper.

**Q: Can skill_creator errors crash the main loop?**
A: NO. All exceptions caught + logged. Main loop continues.

**Q: How many times does it run in a typical game?**
A: ~5–20 times (roughly one per major gameplay segment: combat, map navigation, rewards, etc.)

**Q: Does skill_creator block the agent?**
A: NO. Fire-and-forget. Takes 1–3 seconds per call but async.

---

## Quick Sanity Check

If you want to verify the trigger logic exists and works:

1. **Read source**: `src/slay2agent/agent/loop.py:276–299`
2. **Search for**: `if l0:` inside the state transition block
3. **Look for**: `run_skill_creator(prev_l0=prev_l0_segment, ...)`
4. **Check logs**: Run a game and look for `state_type changed` messages

---

## Visual Summary

```
┌─ State changed? ─┐
│  prev ≠ current │
└────┬────────────┘
    NO / \ YES
       /   \
      /     \
     v       v
  [SKIP]  ┌─────────────┐
          │ L0 empty?   │
          └────┬────────┘
              YES / \ NO
              /     \
             v       v
          [SKIP]  [RUN skill_creator]
                    ↓
                 Write/delete skills
                 Log changes
                 (fire-and-forget)
```
