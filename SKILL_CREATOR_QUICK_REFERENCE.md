# Skill Creator Trigger Logic — Quick Reference

## TL;DR

**Is skill_creator always-on?** ❌ **NO**

**When is it called?** On **state transitions with activity**

### The Condition

```
(state_type changed) AND (L0 non-empty) → TRIGGER skill_creator
```

---

## One-Liner Formula

```python
if prev_state_type and state_type != prev_state_type and l0:
    run_skill_creator(prev_l0=l0, ...)
```

---

## Code Location

- **File**: `src/slay2agent/agent/loop.py`
- **Lines**: 276–299
- **Triggered on every loop iteration** (checks happen at line 276)
- **Nested conditions** mean it's highly selective

---

## Trigger Checklist

| Condition | Status | Example |
|-----------|--------|---------|
| State type changed? | ✅ Required | `map` → `combat` |
| L0 has messages? | ✅ Required | At least 1 step |
| First iteration? | ❌ Excluded | `prev_state_type is None` |
| Same screen twice? | ❌ Excluded | `map` → `map` |
| Empty segment? | ❌ Excluded | Transition with 0 steps |

---

## Examples

### ✅ YES, trigger

```
combat (5 steps) → map (state change + L0 filled) → TRIGGER
card_reward (1 step) → map (state change + L0 filled) → TRIGGER
upgrade (2 steps) → map (state change + L0 filled) → TRIGGER
```

### ❌ NO, don't trigger

```
None → menu (prev_state_type is None) → NO TRIGGER
map → map (no state change) → NO TRIGGER
combat (idle) → map (state change but L0 empty) → NO TRIGGER
```

---

## Why This Design?

1. **Save resources**: Don't LLM-process empty transitions
2. **Quality signals**: Only learn from segments with actual decisions
3. **KV cache**: Skills aren't reloaded mid-run (efficiency)
4. **Per-segment learning**: Each gameplay phase gets its own analysis

---

## Related Concepts

| Concept | Purpose | Reset When? |
|---------|---------|------------|
| **L0** | Conversation history per segment | State transition (always) |
| **skill_registry** | Skill library | Never mid-run; once at end |
| **loop_detector** | Detect infinite loops | Every state transition |
| **oracle.md** | Global strategy (read-only) | Read every LLM call |

---

## Implementation Flow

```
Each loop iteration:
  1. Fetch current state → parse state_type
  2. Did state_type change? AND is L0 non-empty?
     → YES: run_skill_creator(prev_l0=...)  [fire-and-forget]
            then clear L0 = []
     → NO: skip
  3. Continue with main agent LLM call
  4. Append result to L0
  5. Loop
```

---

## Is it Blocking?

**NO.** skill_creator is **fire-and-forget**:
- Exceptions caught + logged (never re-raised)
- Main loop continues immediately
- Takes 1–2 seconds (multi-turn LLM conversation)
- Token usage tracked separately under `"skill_creator"` role

---

## Token Cost

Rough per-trigger cost:
- **Input tokens**: ~500–1000 (oracle + skill list + L0 summary)
- **Output tokens**: ~200–500 (depending on changes made)
- **Frequency**: Maybe 5–20 times per run (depends on segment count)

---

## Files to Check

If you want to verify or modify:
- **Main trigger**: `src/slay2agent/agent/loop.py:276–299`
- **Implementation**: `src/slay2agent/agent/skill_creator.py`
- **L0 management**: `src/slay2agent/agent/loop.py:231–284`
- **Registry**: `src/slay2agent/memory/skill_registry.py`

---

## Final Answer

> "Is it called on every state transition, or only sometimes?"

**Only sometimes — when BOTH these are true:**
1. State changed
2. Previous segment had at least one action (L0 non-empty)

If either is false → no skill_creator call for that transition.
