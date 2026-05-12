"""Two-level LRU skill cache (L1/L2).

L1 (capacity=20): hot skills injected directly into system prompt.
L2 (capacity=200): full skill library, discoverable via list_skills tool.

Promotion: when a skill is used (read_skill), it moves to L1 head.
Demotion: L1 overflow → tail drops to L2; L2 overflow → permanent deletion.
New skills created by skill_creator go directly into L1.

State is persisted to ``skill_cache.json`` in the agent_state directory.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

L1_CAPACITY = 20
L2_CAPACITY = 200


@dataclass
class CacheEntry:
    skill_id: str
    last_used: float  # time.time()
    use_count: int = 0
    source_level_on_use: list[str] = field(default_factory=list)


@dataclass
class SkillCache:
    """Two-level LRU cache for skills.

    L1 and L2 are ordered lists of skill_ids (most recently used first).
    Metadata (timestamps, counts) is stored separately.
    """

    l1: list[str] = field(default_factory=list)
    l2: list[str] = field(default_factory=list)
    meta: dict[str, CacheEntry] = field(default_factory=dict)
    last_evicted: list[str] = field(default_factory=list)

    _path: Path | None = field(default=None, repr=False)

    # ── Persistence ──────────────────────────────────────────────────────

    @classmethod
    def load(cls, path: Path) -> "SkillCache":
        """Load cache state from disk, or create empty if missing."""
        cache = cls(_path=path)
        if not path.exists():
            return cache
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            cache.l1 = data.get("l1", [])
            cache.l2 = data.get("l2", [])
            for sid, m in data.get("meta", {}).items():
                cache.meta[sid] = CacheEntry(
                    skill_id=sid,
                    last_used=m.get("last_used", 0),
                    use_count=m.get("use_count", 0),
                    source_level_on_use=m.get("source_level_on_use", []),
                )
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.error("skill_cache: failed to load %s: %s", path, exc)
        return cache

    def save(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "l1": self.l1,
            "l2": self.l2,
            "meta": {
                sid: {
                    "last_used": e.last_used,
                    "use_count": e.use_count,
                    "source_level_on_use": e.source_level_on_use,
                }
                for sid, e in self.meta.items()
            },
        }
        self._path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ── Core operations ──────────────────────────────────────────────────

    def promote(self, skill_id: str) -> str:
        """Promote a skill to L1 head. Returns the source level ("l1"/"l2"/"new").

        If L1 overflows, the tail is demoted to L2.
        If L2 overflows, the tail is permanently evicted (see ``last_evicted``).
        """
        source = self._find_level(skill_id)

        # Remove from current position
        if skill_id in self.l1:
            self.l1.remove(skill_id)
        if skill_id in self.l2:
            self.l2.remove(skill_id)

        # Insert at L1 head
        self.l1.insert(0, skill_id)

        # Update metadata
        entry = self.meta.get(skill_id)
        if entry is None:
            entry = CacheEntry(skill_id=skill_id, last_used=time.time())
            self.meta[skill_id] = entry
        entry.last_used = time.time()
        entry.use_count += 1
        entry.source_level_on_use.append(source)

        # Enforce capacities
        self._enforce_l1()
        self.last_evicted = self._enforce_l2()

        self.save()
        return source

    def add_new(self, skill_id: str) -> None:
        """Register a newly created skill directly into L1."""
        if skill_id in self.l1 or skill_id in self.l2:
            self.promote(skill_id)
            return

        self.l1.insert(0, skill_id)
        self.meta[skill_id] = CacheEntry(
            skill_id=skill_id,
            last_used=time.time(),
            use_count=0,
            source_level_on_use=["new"],
        )
        self._enforce_l1()
        self.last_evicted = self._enforce_l2()
        self.save()

    def remove(self, skill_id: str) -> None:
        """Remove a skill from cache entirely (called when skill is deleted)."""
        if skill_id in self.l1:
            self.l1.remove(skill_id)
        if skill_id in self.l2:
            self.l2.remove(skill_id)
        self.meta.pop(skill_id, None)
        self.save()

    # ── Queries ──────────────────────────────────────────────────────────

    def l1_ids(self) -> list[str]:
        """Skill IDs in L1 (most recently used first)."""
        return list(self.l1)

    def l2_ids(self) -> list[str]:
        """Skill IDs in L2 (most recently used first)."""
        return list(self.l2)

    def all_ids(self) -> list[str]:
        """All tracked skill IDs (L1 + L2)."""
        return self.l1 + self.l2

    def stats(self) -> dict:
        """Usage statistics summary."""
        level_counts: dict[str, int] = {"l1": 0, "l2": 0, "new": 0}
        for entry in self.meta.values():
            for src in entry.source_level_on_use:
                level_counts[src] = level_counts.get(src, 0) + 1
        return {
            "l1_size": len(self.l1),
            "l2_size": len(self.l2),
            "l1_capacity": L1_CAPACITY,
            "l2_capacity": L2_CAPACITY,
            "total_uses": sum(e.use_count for e in self.meta.values()),
            "source_distribution": level_counts,
        }

    # ── Sync with disk ───────────────────────────────────────────────────

    def sync_with_disk(self, on_disk_ids: set[str]) -> list[str]:
        """Remove cache entries for skills no longer on disk.

        Returns list of removed skill_ids.
        """
        removed = []
        for sid in list(self.l1):
            if sid not in on_disk_ids:
                self.l1.remove(sid)
                removed.append(sid)
        for sid in list(self.l2):
            if sid not in on_disk_ids:
                self.l2.remove(sid)
                removed.append(sid)
        # Also add on-disk skills not yet tracked to L2
        tracked = set(self.l1 + self.l2)
        for sid in on_disk_ids:
            if sid not in tracked:
                self.l2.append(sid)
                if sid not in self.meta:
                    self.meta[sid] = CacheEntry(skill_id=sid, last_used=0)

        # Clean up meta for removed
        for sid in removed:
            self.meta.pop(sid, None)

        if removed:
            self.save()
            logger.info("skill_cache: synced, removed %s", removed)
        return removed

    # ── Internal ─────────────────────────────────────────────────────────

    def _find_level(self, skill_id: str) -> str:
        if skill_id in self.l1:
            return "l1"
        if skill_id in self.l2:
            return "l2"
        return "new"

    def _enforce_l1(self) -> list[str]:
        """Demote L1 tail to L2 head if over capacity. Returns demoted IDs."""
        demoted = []
        while len(self.l1) > L1_CAPACITY:
            tail = self.l1.pop()
            self.l2.insert(0, tail)
            demoted.append(tail)
        return demoted

    def _enforce_l2(self) -> list[str]:
        """Evict L2 tail if over capacity. Returns evicted IDs."""
        evicted = []
        while len(self.l2) > L2_CAPACITY:
            tail = self.l2.pop()
            self.meta.pop(tail, None)
            evicted.append(tail)
        return evicted
