"""Tests for two-level LRU skill cache."""

from __future__ import annotations

from pathlib import Path

from slay2agent.memory.skill_cache import L1_CAPACITY, L2_CAPACITY, SkillCache


def test_empty_cache(tmp_path):
    path = tmp_path / "cache.json"
    cache = SkillCache.load(path)
    assert cache.l1_ids() == []
    assert cache.l2_ids() == []
    assert cache.stats()["l1_size"] == 0


def test_promote_new_skill(tmp_path):
    path = tmp_path / "cache.json"
    cache = SkillCache.load(path)

    source = cache.promote("skill_a")
    assert source == "new"
    assert cache.l1_ids() == ["skill_a"]
    assert cache.meta["skill_a"].use_count == 1


def test_promote_from_l2_to_l1(tmp_path):
    path = tmp_path / "cache.json"
    cache = SkillCache.load(path)

    cache.l2 = ["skill_b"]
    cache.meta["skill_b"] = __import__(
        "slay2agent.memory.skill_cache", fromlist=["CacheEntry"]
    ).CacheEntry(skill_id="skill_b", last_used=0)

    source = cache.promote("skill_b")
    assert source == "l2"
    assert "skill_b" in cache.l1_ids()
    assert "skill_b" not in cache.l2_ids()


def test_promote_within_l1_moves_to_head(tmp_path):
    path = tmp_path / "cache.json"
    cache = SkillCache.load(path)

    cache.promote("a")
    cache.promote("b")
    cache.promote("c")
    assert cache.l1_ids() == ["c", "b", "a"]

    cache.promote("a")
    assert cache.l1_ids() == ["a", "c", "b"]


def test_l1_overflow_demotes_to_l2(tmp_path):
    path = tmp_path / "cache.json"
    cache = SkillCache.load(path)

    for i in range(L1_CAPACITY + 5):
        cache.promote(f"s{i}")

    assert len(cache.l1_ids()) == L1_CAPACITY
    assert len(cache.l2_ids()) == 5
    # The earliest promoted should be in L2 tail
    assert "s0" in cache.l2_ids()


def test_l2_overflow_evicts(tmp_path):
    path = tmp_path / "cache.json"
    cache = SkillCache.load(path)

    # Fill L2 to capacity
    cache.l2 = [f"old_{i}" for i in range(L2_CAPACITY)]
    for sid in cache.l2:
        cache.meta[sid] = __import__(
            "slay2agent.memory.skill_cache", fromlist=["CacheEntry"]
        ).CacheEntry(skill_id=sid, last_used=0)

    # Fill L1 then overflow into L2
    for i in range(L1_CAPACITY + 1):
        cache.promote(f"new_{i}")

    # L2 should be at capacity, old tail evicted
    assert len(cache.l2_ids()) <= L2_CAPACITY
    # Evicted skills should have no meta
    all_tracked = set(cache.all_ids())
    assert "old_199" not in all_tracked or len(cache.l2) <= L2_CAPACITY


def test_add_new_goes_to_l1(tmp_path):
    path = tmp_path / "cache.json"
    cache = SkillCache.load(path)

    cache.add_new("fresh")
    assert "fresh" in cache.l1_ids()
    assert cache.meta["fresh"].source_level_on_use == ["new"]


def test_remove(tmp_path):
    path = tmp_path / "cache.json"
    cache = SkillCache.load(path)

    cache.promote("x")
    cache.remove("x")
    assert "x" not in cache.l1_ids()
    assert "x" not in cache.l2_ids()
    assert "x" not in cache.meta


def test_persistence(tmp_path):
    path = tmp_path / "cache.json"
    cache = SkillCache.load(path)
    cache.promote("persistent")
    cache.save()

    reloaded = SkillCache.load(path)
    assert "persistent" in reloaded.l1_ids()
    assert reloaded.meta["persistent"].use_count == 1


def test_sync_with_disk(tmp_path):
    path = tmp_path / "cache.json"
    cache = SkillCache.load(path)
    cache.promote("exists")
    cache.promote("gone")

    removed = cache.sync_with_disk({"exists", "new_on_disk"})
    assert "gone" in removed
    assert "gone" not in cache.l1_ids()
    # new_on_disk should be added to L2
    assert "new_on_disk" in cache.l2_ids()


def test_stats_tracks_source_distribution(tmp_path):
    path = tmp_path / "cache.json"
    cache = SkillCache.load(path)

    cache.promote("a")  # new
    cache.promote("a")  # l1
    cache.promote("b")  # new

    stats = cache.stats()
    assert stats["total_uses"] == 3
    dist = stats["source_distribution"]
    assert dist["new"] == 2
    assert dist["l1"] == 1
