# Comprehensive Analysis: skill-creator Implementation Pipeline

## Executive Summary

The **skill-creator** system is a sophisticated multi-layer skill management system for the Slay the Spire 2 AI agent. It consists of:

1. **Skill Creator Sub-agent** (F-008b) - LLM-driven automation that inspects and updates skills
2. **Skill Registry** (F-008a) - File I/O and metadata management layer
3. **Two-level LRU Cache** - Performance optimization with L1 (hot) and L2 (discoverable) tiers
4. **Skill frontmatter format** - YAML metadata + Markdown body structure

### Key Finding: Duplicate Detection via list_skills + read_skill

**The skill-creator DOES check for existing similar skills**, but it does so **implicitly through the LLM's reasoning** rather than through automatic duplicate detection. Here's the complete pipeline:

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                  run_skill_creator (F-008b)                     │
│                     [LLM Sub-agent]                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  MANDATORY WORKFLOW (enforced via system prompt):              │
│  1. list_skills()        ← Discover existing skills            │
│  2. read_skill(x)        ← Inspect similar skills              │
│  3. [decision logic]     ← LLM decides: extend/merge/create    │
│  4. write_skill/delete   ← Execute mutations                   │
│  5. text response        ← Summary of changes                  │
│                                                                 │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ↓
        ┌────────────────────────────────┐
        │   SkillRegistry (F-008a)       │
        │  [File I/O & Metadata]         │
        ├────────────────────────────────┤
        │ write_skill()                  │
        │ delete_skill()                 │
        │ read_skill()                   │
        │ list_skills()                  │
        └────────────────────────────────┘
                         │
                         ↓
        ┌────────────────────────────────┐
        │     SkillCache (L1/L2)         │
        │   [2-level LRU Cache]          │
        ├────────────────────────────────┤
        │ L1: 20 hot skills              │
        │ L2: 200 discoverable skills    │
        └────────────────────────────────┘
                         │
                         ↓
        ┌────────────────────────────────┐
        │  agent_state/skills/*.md       │
        │    [Disk storage]              │
        └────────────────────────────────┘
```

---

## Component 1: Skill Creator Sub-agent (run_skill_creator)

**File**: `src/slay2agent/agent/skill_creator.py`

### Entry Point

```python
def run_skill_creator(
    prev_l0: list[Message],          # Gameplay segment history
    skill_registry: SkillRegistry,   # Shared registry instance
    oracle_path: Path,               # oracle.md (read-only reference)
    adapter: LLMAdapter,             # LLM connection
    tracker: UsageTracker,           # Token accounting
    trace: TraceWriter,              # Logging to subagent.jsonl
    *,
    model: str,
    prev_state_type: str,            # e.g., "combat"
    new_state_type: str,             # e.g., "map"
    max_steps: int = 12,
) -> None
```

### System Prompt (Mandatory Workflow)

The system prompt enforces a strict workflow that guarantees duplicate checking:

```markdown
MANDATORY PROCESS — follow this order exactly:
1. Call list_skills to see what skills already exist.
2. For each insight you want to record, call read_skill on any 
   skills with similar names or themes.
3. Decide for each insight: extend an existing skill / merge two 
   similar skills / create a new skill / no-op.
4. Call write_skill or delete_skill as needed (may be zero calls).
5. When finished, reply with a plain-text summary.

Rules:
- PREFER extending an existing skill over creating a new one.
- PREFER merging two similar skills over keeping duplicates.
- Only create a NEW skill for genuinely distinct strategic 
  knowledge not covered elsewhere.
```

### Tools Available to LLM

The skill_creator LLM has access to:

1. **list_skills** (read-only)
   - Returns: `{skills: [{skill_id, name, description}, ...], note: "..."}`
   - Shows L2 skills (200-skill discoverable pool)
   - Used to discover what already exists

2. **read_skill(skill_id)** (read-only)
   - Returns: `{skill_id, name, description, body}`
   - Promotes skill to L1 cache (side-effect)
   - Used to inspect similar skills BEFORE deciding to create new ones

3. **write_skill(skill_id, name, description, body)**
   - Creates or overwrites a skill file
   - Inserts directly into L1 cache
   - Invalidates disk cache and enforces capacity

4. **delete_skill(skill_id)**
   - Removes skill file and cache entries
   - Returns success/failure status

### Execution Flow

```python
for step in range(max_steps):
    resp = adapter.chat(conversation, tools)  # Multi-turn LLM
    
    if not resp.message.tool_calls:
        # Text response → done
        break
    
    # Execute first tool call
    tool_call = resp.message.tool_calls[0]
    result = _dispatch_tool(tool_call.name, tool_call.arguments)
    conversation.append(Message(role="tool", content=json.dumps(result)))
    
    # Track file changes
    if tool_call.name in ("write_skill", "delete_skill"):
        file_changes.append(f"{tool_call.name}({skill_id})")
```

### Fire-and-Forget Pattern

```python
try:
    # ... entire sub-agent execution ...
except Exception as exc:
    logger.error("skill_creator: unhandled error: %s", exc, exc_info=True)
    # Logs error but does NOT propagate
    # Main agent loop continues uninterrupted
```

---

## Component 2: Skill Registry (F-008a)

**File**: `src/slay2agent/memory/skill_registry.py`

### Core Data Structures

```python
@dataclass(frozen=True)
class SkillMeta:
    """Metadata header for a skill — injected into every system prompt."""
    skill_id: str          # snake_case identifier (e.g., "ironclad_early_combat")
    name: str              # Human-readable name
    description: str       # SOLE trigger signal + when-to-use pattern

@dataclass(frozen=True)
class Skill:
    """Full skill including body — only loaded on read_skill() calls."""
    skill_id: str
    name: str
    description: str
    body: str              # Markdown content
```

### File Format (Frontmatter + Markdown)

```markdown
---
name: Ironclad — Early Combat
description: Strategies for early-floor combat as Ironclad, focusing 
  on exhaustion mechanics and AoE damage cards. Use when playing 
  Ironclad in Act 1 normal/elite fights where the deck is still 
  mostly starter cards.
---

# Ironclad — Early Combat

Full markdown body — only loaded when agent calls read_skill().
```

### API Methods

#### 1. list_skills() → list[SkillMeta]
Returns **all skills** from disk, sorted by skill_id.

```python
def list_skills(self) -> list[SkillMeta]:
    return [
        SkillMeta(skill_id=s.skill_id, name=s.name, 
                  description=s.description)
        for s in sorted(self._load().values(), key=lambda s: s.skill_id)
    ]
```

**Used by skill_creator**: To discover existing skills before deciding whether to create new ones.

#### 2. read_skill(skill_id) → Skill | None
Loads and returns full skill (including body).
**Side-effect**: Promotes skill to L1 cache.

```python
def read_skill(self, skill_id: str) -> Skill | None:
    skill = self._load().get(skill_id)
    if skill is not None and self._skill_cache is not None:
        source = self._skill_cache.promote(skill_id)
        logger.debug("skill_registry: read_skill %r promoted from %s", 
                     skill_id, source)
        self._handle_evictions()
    return skill
```

**Used by skill_creator**: To inspect similar skills before deciding.

#### 3. write_skill(skill_id, name, description, body)
Creates or overwrites a skill file.

```python
def write_skill(self, skill_id: str, name: str, 
                description: str, body: str) -> None:
    self._skills_dir.mkdir(parents=True, exist_ok=True)
    path = self._skills_dir / f"{skill_id}.md"
    content = f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n"
    path.write_text(content, encoding="utf-8")
    self._disk_cache = None  # Invalidate cache
    
    if self._skill_cache is not None:
        self._skill_cache.add_new(skill_id)  # Insert into L1
        self._handle_evictions()  # Enforce capacity limits
```

#### 4. delete_skill(skill_id) → bool
Removes skill file and cache entries.

```python
def delete_skill(self, skill_id: str) -> bool:
    path = self._skills_dir / f"{skill_id}.md"
    if not path.exists():
        return False
    path.unlink()
    self._disk_cache = None
    
    if self._skill_cache is not None:
        self._skill_cache.remove(skill_id)
    return True
```

### Caching Layer

The registry maintains a **disk cache** (dictionary of all Skill objects):

```python
def _load(self) -> dict[str, Skill]:
    if self._disk_cache is not None:
        return self._disk_cache
    
    # Lazy load from disk
    skills = {}
    for path in sorted(self._skills_dir.glob("*.md")):
        skill = _load_skill_file(path)
        if skill is not None:
            skills[skill.skill_id] = skill
    
    self._disk_cache = skills
    return self._disk_cache
```

Invalidation happens on `write_skill()` and `delete_skill()` by setting `self._disk_cache = None`.

---

## Component 3: Two-Level LRU Cache

**File**: `src/slay2agent/memory/skill_cache.py`

### Cache Architecture

```
┌────────────────────────────────────────────────────────────┐
│  L1 (Hot skills - injected into system prompt)             │
│  Capacity: 20 skills                                       │
│  MRU ←──────────────────────────────────────────→ LRU     │
│  [most_recently_used_1, ..., least_recently_used_20]      │
└────────────────────────────────────────────────────────────┘
                          ↓ overflow
┌────────────────────────────────────────────────────────────┐
│  L2 (Discoverable skills - available via list_skills)     │
│  Capacity: 200 skills                                      │
│  MRU ←──────────────────────────────────────────→ LRU     │
│  [most_recently_used_1, ..., least_recently_used_200]     │
└────────────────────────────────────────────────────────────┘
                          ↓ overflow
                    PERMANENT DELETION
                   (evicted_skills.md deleted)
```

### Core Operations

#### 1. promote(skill_id) → str
Moves skill to L1 head. Returns source level ("l1", "l2", or "new").

```python
def promote(self, skill_id: str) -> str:
    source = self._find_level(skill_id)  # Where was it before?
    
    # Remove from current position
    if skill_id in self.l1:
        self.l1.remove(skill_id)
    if skill_id in self.l2:
        self.l2.remove(skill_id)
    
    # Insert at L1 head (most recently used)
    self.l1.insert(0, skill_id)
    
    # Update metadata
    entry = self.meta.get(skill_id) or CacheEntry(...)
    entry.last_used = time.time()
    entry.use_count += 1
    entry.source_level_on_use.append(source)
    
    # Enforce capacities
    self._enforce_l1()  # If L1 > 20, demote tail to L2
    self.last_evicted = self._enforce_l2()  # If L2 > 200, delete tail
    
    self.save()  # Persist to skill_cache.json
    return source
```

#### 2. add_new(skill_id)
Registers newly created skill directly into L1.

```python
def add_new(self, skill_id: str) -> None:
    if skill_id in self.l1 or skill_id in self.l2:
        self.promote(skill_id)  # Already exists, promote it
        return
    
    # Insert at L1 head
    self.l1.insert(0, skill_id)
    self.meta[skill_id] = CacheEntry(
        skill_id=skill_id,
        last_used=time.time(),
        use_count=0,
        source_level_on_use=["new"],  # Track that it's newly created
    )
    
    self._enforce_l1()
    self.last_evicted = self._enforce_l2()
    self.save()
```

#### 3. remove(skill_id)
Removes skill from cache entirely (called on deletion).

```python
def remove(self, skill_id: str) -> None:
    if skill_id in self.l1:
        self.l1.remove(skill_id)
    if skill_id in self.l2:
        self.l2.remove(skill_id)
    self.meta.pop(skill_id, None)
    self.save()
```

### Capacity Enforcement

#### _enforce_l1()
If L1 overflows (>20), demote tail to L2.

```python
def _enforce_l1(self) -> list[str]:
    demoted = []
    while len(self.l1) > L1_CAPACITY:  # 20
        tail = self.l1.pop()  # Remove least recently used
        self.l2.insert(0, tail)  # Move to L2 head
        demoted.append(tail)
    return demoted
```

#### _enforce_l2()
If L2 overflows (>200), evict and delete tail.

```python
def _enforce_l2(self) -> list[str]:
    evicted = []
    while len(self.l2) > L2_CAPACITY:  # 200
        tail = self.l2.pop()  # Remove least recently used
        self.meta.pop(tail, None)  # Remove metadata
        evicted.append(tail)  # Mark for file deletion
    return evicted
```

**The evicted skill's .md file is then deleted by `SkillRegistry._handle_evictions()`.**

### Persistence

Cache state is saved to `agent_state/skill_cache.json`:

```json
{
  "l1": ["skill_1", "skill_2", ...],
  "l2": ["skill_101", "skill_102", ...],
  "meta": {
    "skill_1": {
      "last_used": 1715823842.123,
      "use_count": 5,
      "source_level_on_use": ["new", "l2", "l1", "l1"]
    },
    ...
  }
}
```

---

## How Duplicate Detection Works

### The Mechanism: Implicit via LLM Reasoning

The skill-creator **does NOT have automatic duplicate detection logic**. Instead, it relies on the LLM's reasoning and the mandatory workflow:

### Step-by-Step Process

#### 1. LLM sees list_skills output
```
Tool result:
{
  "skills": [
    {"skill_id": "combat_basics", "name": "Combat Basics", 
     "description": "Basic combat strategies. Use when..."},
    {"skill_id": "early_game", "name": "Early Game Strategy", 
     "description": "Early game tactics. Use when..."},
    ...
  ],
  "note": "These are additional skills not shown in your system prompt."
}
```

#### 2. LLM decides to create a new skill for concept "X"
LLM may call `read_skill("combat_basics")` to check if it overlaps with the new concept.

#### 3. LLM compares based on:
- **Skill ID similarity** (naming convention)
- **Description semantics** (what the skill covers)
- **Body content** (when LLM reads it via read_skill)

#### 4. LLM makes decision:
- **Extend existing skill**: Call `write_skill` with same `skill_id`, updated `name`, `description`, `body`
- **Merge two skills**: Call `write_skill` to create consolidated skill, then `delete_skill` for the old one
- **Create new skill**: Call `write_skill` with new `skill_id` for genuinely distinct knowledge
- **No-op**: Don't call write_skill or delete_skill

#### 5. LLM provides summary
```
"Extended combat_basics with new tactics for poison builds.
Updated early_game with shield mechanics. No changes to defense_strategy."
```

### Why This Design?

1. **Flexibility**: LLM can decide when to extend vs. merge vs. create based on semantic understanding
2. **Context-aware**: The LLM sees the gameplay history and oracle context
3. **Avoids false positives**: Simple string matching would incorrectly flag skills as duplicates

---

## File System Layout

### Skill Files

```
agent_state/
├── skills/
│   ├── combat_basics.md
│   │   ---
│   │   name: Combat Basics
│   │   description: Fundamental combat tactics for all characters. Use when...
│   │   ---
│   │   # Combat Basics
│   │   [markdown body]
│   │
│   ├── ironclad_early_combat.md
│   ├── silent_poison_synergy.md
│   └── ... (up to 200-220 skills)
│
├── skill_cache.json  # State of L1/L2 cache
└── ...
```

### Eviction Behavior

When a skill is evicted from L2 (due to 200-skill capacity limit):

1. `SkillCache._enforce_l2()` adds skill_id to `last_evicted`
2. `SkillRegistry._handle_evictions()` reads `last_evicted`
3. **Skill file is permanently deleted from disk**

```python
def _handle_evictions(self) -> None:
    if self._skill_cache is None:
        return
    evicted = self._skill_cache.last_evicted
    if not evicted:
        return
    for sid in evicted:
        path = self._skills_dir / f"{sid}.md"
        if path.exists():
            path.unlink()  # PERMANENT DELETION
            logger.info("skill_registry: evicted skill file %s (L2 overflow)", sid)
    self._skill_cache.last_evicted = []
    self._disk_cache = None
```

---

## Test Coverage

### Files
- `tests/test_skill_creator.py` - Sub-agent execution
- `tests/test_skill_registry.py` - Registry operations
- `tests/test_skill_cache.py` - Cache logic
- `tests/test_skill_cache_integration.py` - Integration

### Key Test: Full Flow with Duplicate Check

```python
def test_full_flow_list_read_write_text(tmp_path):
    """LLM calls list_skills → read_skill → write_skill → text response."""
    responses = [
        _make_tool_response("list_skills", {}, "tc1"),
        _make_tool_response("read_skill", {"skill_id": "nonexistent"}, "tc2"),
        _make_tool_response("write_skill", {
            "skill_id": "new_skill",
            "name": "New Skill",
            "description": "A new insight. Use at the start of combat.",
            "body": "# New Skill\n\nAlways do X.",
        }, "tc3"),
        _make_text_response("Created new_skill with combat tip."),
    ]
    
    reg, tracker, run_dir = _run(tmp_path, responses)
    
    # Skill was written
    skill = reg.read_skill("new_skill")
    assert skill is not None
    assert "Always do X." in skill.body
```

---

## Key Insights & Limitations

### ✅ What Works Well

1. **Two-level caching** efficiently manages up to 220 skills (20 hot + 200 discoverable)
2. **LLM-driven merging** allows semantic duplicate detection
3. **Fire-and-forget pattern** ensures skill updates never crash the main agent
4. **Mandatory workflow** in system prompt enforces proper duplicate checking
5. **Persistent cache** tracks skill usage and promotion patterns

### ⚠️ Limitations

1. **No automatic duplicate detection** - relies entirely on LLM reasoning
   - If LLM misses a semantically similar skill, duplicates can occur
   - No string matching or embedding-based similarity check

2. **Permanent L2 eviction** - when 200-skill limit is reached, oldest LRU skills are deleted
   - Could lose valuable skills if cache is full
   - No recovery mechanism (deleted files are gone)

3. **Single LLM call model** - skill_creator doesn't call itself recursively
   - One pass through gameplay history
   - Can't refactor/consolidate existing skills proactively

4. **No skill versioning** - write_skill overwrites without backup
   - Previous versions are lost
   - No history of how skills evolved

5. **LLM prompt length** - L0 history is truncated to 8000 chars
   - Large gameplay segments are summarized
   - May miss context for good skill decisions

---

## System Prompt Rules (Critical)

From `_SYSTEM_TEMPLATE` in `skill_creator.py`:

```markdown
MANDATORY PROCESS — follow this order exactly:
1. Call list_skills to see what skills already exist.
2. For each insight you want to record, call read_skill on any 
   skills with similar names or themes.
3. Decide for each insight: extend an existing skill / merge two 
   similar skills / create a new skill / no-op.
4. Call write_skill or delete_skill as needed (may be zero calls).
5. When finished, reply with a plain-text summary of what you changed 
   (or "no changes needed" if nothing was worth recording).

Rules:
- PREFER extending an existing skill over creating a new one.
- PREFER merging two similar skills over keeping duplicates.
- Only create a NEW skill for genuinely distinct strategic 
  knowledge not covered elsewhere.
- skill_id must be snake_case, no spaces, no special characters.
- Body must start with a level-1 heading and be self-contained markdown.
- Never call write_skill or delete_skill before calling list_skills first.
```

This is the **primary mechanism** that prevents duplicates.

---

## Integration Example

### Typical Run Flow

```python
# Main agent completes a gameplay segment
prev_l0 = [Message(...), Message(...), ...]  # Segment history
state_transition = "combat" → "map"

# Trigger skill_creator
run_skill_creator(
    prev_l0=prev_l0,
    skill_registry=registry,
    oracle_path=Path("agent_state/oracle.md"),
    adapter=llm_adapter,
    tracker=usage_tracker,
    trace=trace_writer,
    model="claude-3-5-sonnet",
    prev_state_type="combat",
    new_state_type="map",
)

# Inside run_skill_creator:
# 1. Builds system prompt with oracle.md injected
# 2. Serializes L0 history into user message (max 8000 chars)
# 3. Calls LLM with tools: list_skills, read_skill, write_skill, delete_skill
# 4. LLM makes up to 12 tool calls in sequence
# 5. Results written to subagent.jsonl
# 6. Any exception is caught and logged

# If LLM decides to create "new_poison_synergy" skill:
# 1. LLM calls list_skills → gets all 127 existing skills
# 2. LLM calls read_skill("silent_poison_synergy") → sees related content
# 3. LLM decides: "This is distinct, create new_poison_synergy"
# 4. LLM calls write_skill("new_poison_synergy", ...)
#    → SkillRegistry writes file
#    → SkillCache adds to L1 head
#    → L1 enforced (if >20, demote 1 to L2)
#    → L2 enforced (if >200, delete 1 oldest)
# 5. LLM provides text summary: "Created new_poison_synergy..."
# 6. Execution ends, main agent loop continues
```

---

## Conclusion

The skill-creator system is a **multi-layer, LLM-driven skill management pipeline** with:

1. **Implicit duplicate detection** via LLM reasoning + mandatory workflow
2. **Two-level LRU cache** for performance (20 hot + 200 discoverable)
3. **File-based persistence** with YAML frontmatter format
4. **Fire-and-forget robustness** for the main agent loop

The key mechanism for avoiding duplicates is the **mandatory workflow enforced via system prompt**, which requires the LLM to:
1. List all skills
2. Read similar skills before deciding
3. Prefer extending/merging over creating

This design prioritizes **semantic correctness** (LLM decides) over **automatic detection**, accepting the tradeoff that duplicates can exist if the LLM misses them.
