# Skill Creator Analysis — Complete Index

## Overview

This directory contains a comprehensive analysis of the **skill_creator** subagent trigger logic in the Slay2Agent project. The trigger mechanism has been reverse-engineered and documented across multiple levels of detail.

---

## 📄 Documents (Read in This Order)

### 1. **SKILL_CREATOR_CHEAT_SHEET.md** ⭐ START HERE
   - **Best for**: Quick answers and reference
   - **Length**: ~2 pages
   - **Contains**: 
     - One-question TL;DR
     - Decision logic (pseudocode)
     - When it runs (table)
     - FAQ

### 2. **SKILL_CREATOR_QUICK_REFERENCE.md**
   - **Best for**: Fast lookup and key facts
   - **Length**: ~2 pages
   - **Contains**:
     - One-liner formula
     - Trigger checklist
     - Examples (yes/no)
     - Design rationale
     - Related concepts table

### 3. **SKILL_CREATOR_TRIGGER_LOGIC.md**
   - **Best for**: Deep understanding with visuals
   - **Length**: ~8 pages
   - **Contains**:
     - Comprehensive analysis
     - State machine diagram
     - Data flow explanation
     - L0 lifecycle
     - Timeline example
     - Design rationale

### 4. **SKILL_CREATOR_VISUAL_FLOW.md**
   - **Best for**: Visual learners
   - **Length**: ~10 pages
   - **Contains**:
     - ASCII flowcharts
     - Decision trees
     - State diagrams
     - Execution timelines
     - Decision matrices (truth tables)
     - Performance impact analysis

### 5. **SKILL_CREATOR_ANALYSIS.md**
   - **Best for**: Architecture deep-dive (existing file)
   - **Length**: ~20 pages
   - **Contains**:
     - Component descriptions
     - Implementation details
     - Skill file format
     - LRU cache explanation

---

## 🎯 Quick Answer to Your Question

> "Where and how is skill_creator triggered? Is it called on every state transition, or only sometimes?"

### Answer: **ONLY SOMETIMES**

```
skill_creator TRIGGERS when:
  1. state_type changed (prev ≠ current)      AND
  2. L0 is non-empty (segment had activity)   AND
  3. Not first iteration (prev_state is not None)

ALL THREE must be true.

Location: src/slay2agent/agent/loop.py, lines 276–299
Code: if prev_state_type is not None and state_type != prev_state_type:
          if l0:
              run_skill_creator(prev_l0=l0, ...)
```

---

## 🗂️ Document Selection Guide

**Choose by your use case:**

| You want to... | Read... |
|---|---|
| Get the answer quickly | CHEAT_SHEET |
| Understand the logic | TRIGGER_LOGIC |
| See visual diagrams | VISUAL_FLOW |
| Deep dive into architecture | ANALYSIS |
| Quick reference/lookup | QUICK_REFERENCE |

**Or just read this order:**
1. CHEAT_SHEET (2 min)
2. TRIGGER_LOGIC (10 min)
3. VISUAL_FLOW (5 min, if needed)

---

## 📍 Code Locations

### Main Trigger
- **File**: `src/slay2agent/agent/loop.py`
- **Lines**: 276–299
- **Function**: `run_demo_loop()`
- **Context**: Main agent loop, after state fetch, before LLM call

### Implementation
- **File**: `src/slay2agent/agent/skill_creator.py`
- **Function**: `run_skill_creator()`
- **Type**: Fire-and-forget subagent
- **Blocking**: NO

### Related Files
- `src/slay2agent/memory/skill_registry.py` — Skill management
- `src/slay2agent/memory/oracle.py` — Oracle reference
- `agent_state/skills/` — Where skills are stored

---

## 🔑 Key Concepts

### L0 (In-Context Message History)
- Accumulated conversation history for one game segment
- Cleared on every state_type transition
- Non-empty L0 is the gate for skill_creator trigger

### state_type
- Current game screen/phase (e.g., "map", "combat", "card_reward")
- Compared with prev_state_type to detect transitions

### Trigger Condition
```
(prev_state_type is not None) 
    AND 
(state_type != prev_state_type)
    AND
(L0 is non-empty)
    =
TRIGGER skill_creator
```

### Fire-and-Forget Design
- Exceptions caught + logged
- Main loop continues immediately
- Takes ~1–3 seconds per call
- Async/parallel from main agent perspective

---

## 📊 Summary Statistics

| Metric | Value |
|---|---|
| Files in this analysis | 5 |
| Total documentation | ~40 pages |
| Code location | `src/slay2agent/agent/loop.py:276–299` |
| Implementation | `src/slay2agent/agent/skill_creator.py` |
| Trigger frequency | ~5–20 per run |
| Tokens per trigger | ~700–1500 |
| Run impact | ~10–30% of total tokens |

---

## ✅ Verification Checklist

To verify the trigger logic exists and works:

- [ ] Read `src/slay2agent/agent/loop.py:276–299`
- [ ] Find: `if prev_state_type is not None and state_type != prev_state_type:`
- [ ] Find: `if l0:` (nested inside)
- [ ] Find: `run_skill_creator(prev_l0=prev_l0_segment, ...)`
- [ ] Check logs: Look for `"state_type changed X → Y — clearing L0"`
- [ ] Check logs: Look for `"skill_creator: triggered by X → Y"`

---

## 🎓 Learning Path

### Beginner (5 min)
1. Read CHEAT_SHEET — The One Question section
2. Understand: state change + L0 non-empty = trigger

### Intermediate (15 min)
1. Read CHEAT_SHEET (entire)
2. Read TRIGGER_LOGIC — The Two-Part Trigger Condition section
3. Look at VISUAL_FLOW — Simplified Decision Tree

### Advanced (30 min)
1. Read all documents in order
2. Read source code: `src/slay2agent/agent/loop.py:276–299`
3. Read implementation: `src/slay2agent/agent/skill_creator.py:239–378`
4. Trace through a game log to see trigger patterns

---

## 🔗 Cross References

### Within Slay2Agent
- Framework design: `docs/framework-design.md` (mentions F-008b)
- Main loop: `src/slay2agent/agent/loop.py` (lines 177–634)
- Oracle strategy: `src/slay2agent/memory/oracle.py`
- Skill cache: `src/slay2agent/memory/skill_cache.py`

### Comments in Code
- Line 9: `(skill_creator stub — runs in F-008b)`
- Line 286: `F-008b: run skill creator on the completed segment`
- Line 308: Explanation of mid-run reload policy

---

## 💡 Design Insights

### Why This Approach?

1. **Efficiency**
   - Avoid LLM processing of empty transitions
   - Only learn from meaningful gameplay

2. **Signal Quality**
   - Focus on segments with actual decisions
   - Reduce noise from idle/automatic transitions

3. **Resource Management**
   - Save tokens for high-value learning moments
   - Distribute computation across run

4. **Per-Segment Learning**
   - Each game phase analyzed independently
   - Clean separation of concerns

---

## 📋 FAQ

**Q: Does skill_creator run every loop iteration?**
A: No. The check happens every iteration, but runs only when conditions are met.

**Q: Can it crash the main agent?**
A: No. Fire-and-forget design — exceptions caught + logged.

**Q: Why not reload skills mid-run?**
A: KV cache efficiency. One reload at run end is cheaper than 100+ mid-run.

**Q: How many times per run?**
A: Typically 5–20 (one per major gameplay segment with activity).

**Q: Is it always-on?**
A: No. Conditional trigger based on state change + segment activity.

---

## 🚀 Quick Start

**If you have 2 minutes:**
- Read: CHEAT_SHEET — "The One Question" + "The Code"

**If you have 10 minutes:**
- Read: TRIGGER_LOGIC — Entire document

**If you have 30 minutes:**
- Read: All documents in recommended order

**If you have 1 hour:**
- Read: All documents + Source code (`src/slay2agent/agent/loop.py:276–299`)

---

## 📝 Notes

- Analysis created: 2026-05-22
- Source code version: Latest in `/Users/syt/Desktop/code/Slay2Agent/`
- All line numbers reference: `src/slay2agent/agent/loop.py`
- Test files: `tests/test_skill_creator.py` (if you want to see test cases)

---

## ✨ Summary

**skill_creator is NOT always-on.**

It's a **selective, conditional trigger** that runs only when both:
1. The game state changed
2. The previous segment had meaningful activity (L0 non-empty)

This design ensures learning happens at **strategic boundaries** with **high-quality signals**, while maintaining efficiency through **fire-and-forget execution** and **mid-run KV cache stability**.

---

*For questions or clarifications, refer to the specific document sections listed above.*
