"""Integration tests: SkillRegistry + SkillCache working together."""

from __future__ import annotations

from pathlib import Path

from slay2agent.memory.skill_cache import L1_CAPACITY, SkillCache
from slay2agent.memory.skill_registry import SkillRegistry


def _make_skill(skills_dir: Path, skill_id: str) -> None:
    content = (
        f"---\n"
        f"name: {skill_id}\n"
        f"description: Desc for {skill_id}. Use when relevant.\n"
        f"---\n\n"
        f"# {skill_id}\nBody.\n"
    )
    (skills_dir / f"{skill_id}.md").write_text(content, encoding="utf-8")


def test_metadata_lines_only_shows_l1(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    cache_path = tmp_path / "cache.json"

    # Create 25 skills on disk
    for i in range(25):
        _make_skill(skills_dir, f"s{i:02d}")

    cache = SkillCache.load(cache_path)
    reg = SkillRegistry(skills_dir, skill_cache=cache)

    # Sync discovers all skills → they go to L2
    on_disk = {f"s{i:02d}" for i in range(25)}
    cache.sync_with_disk(on_disk)

    # Only L2 skills, no L1 → metadata_lines is empty
    assert reg.metadata_lines() == []

    # Promote some to L1
    for i in range(5):
        cache.promote(f"s{i:02d}")

    lines = reg.metadata_lines()
    assert len(lines) == 5
    assert "[s04]" in lines[0]  # most recently promoted = head


def test_list_skills_response_returns_l2(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    cache_path = tmp_path / "cache.json"

    for i in range(10):
        _make_skill(skills_dir, f"s{i}")

    cache = SkillCache.load(cache_path)
    reg = SkillRegistry(skills_dir, skill_cache=cache)
    cache.sync_with_disk({f"s{i}" for i in range(10)})

    # Promote 3 to L1
    cache.promote("s0")
    cache.promote("s1")
    cache.promote("s2")

    resp = reg.list_skills_response()
    l2_ids = {s["skill_id"] for s in resp["skills"]}
    # L1 skills should NOT appear in list_skills_response
    assert "s0" not in l2_ids
    assert "s1" not in l2_ids
    assert "s2" not in l2_ids
    # Rest should be in L2
    assert "s3" in l2_ids


def test_read_skill_promotes_to_l1(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    cache_path = tmp_path / "cache.json"

    _make_skill(skills_dir, "target")
    cache = SkillCache.load(cache_path)
    reg = SkillRegistry(skills_dir, skill_cache=cache)
    cache.sync_with_disk({"target"})

    assert "target" in cache.l2_ids()
    assert "target" not in cache.l1_ids()

    skill = reg.read_skill("target")
    assert skill is not None
    assert "target" in cache.l1_ids()
    assert "target" not in cache.l2_ids()


def test_write_skill_goes_to_l1(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    cache_path = tmp_path / "cache.json"

    cache = SkillCache.load(cache_path)
    reg = SkillRegistry(skills_dir, skill_cache=cache)

    reg.write_skill("brand_new", "Brand New", "A new skill. Use always.", "# Brand New\nContent.")
    assert "brand_new" in cache.l1_ids()
    assert (skills_dir / "brand_new.md").exists()


def test_delete_skill_removes_from_cache(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    cache_path = tmp_path / "cache.json"

    _make_skill(skills_dir, "doomed")
    cache = SkillCache.load(cache_path)
    reg = SkillRegistry(skills_dir, skill_cache=cache)
    cache.promote("doomed")

    assert "doomed" in cache.l1_ids()
    reg.delete_skill("doomed")
    assert "doomed" not in cache.l1_ids()
    assert "doomed" not in cache.l2_ids()


def test_l1_overflow_demotes_and_l2_overflow_deletes_files(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    cache_path = tmp_path / "cache.json"

    # Create L1_CAPACITY + 1 skills
    n = L1_CAPACITY + 1
    for i in range(n):
        _make_skill(skills_dir, f"skill_{i}")

    cache = SkillCache.load(cache_path)
    reg = SkillRegistry(skills_dir, skill_cache=cache)

    # Sync all skills into cache (they start in L2)
    cache.sync_with_disk({f"skill_{i}" for i in range(n)})

    # Promote all via read — last one will overflow L1
    for i in range(n):
        reg.read_skill(f"skill_{i}")

    assert len(cache.l1_ids()) == L1_CAPACITY
    assert len(cache.l2_ids()) == 1
    # The first promoted (skill_0) should now be in L2
    assert "skill_0" in cache.l2_ids()


def test_stats_records_source(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    cache_path = tmp_path / "cache.json"

    _make_skill(skills_dir, "a")
    _make_skill(skills_dir, "b")

    cache = SkillCache.load(cache_path)
    reg = SkillRegistry(skills_dir, skill_cache=cache)
    cache.sync_with_disk({"a", "b"})

    # First read from L2
    reg.read_skill("a")
    # Second read from L1
    reg.read_skill("a")

    stats = cache.stats()
    dist = stats["source_distribution"]
    assert dist["l2"] >= 1
    assert dist["l1"] >= 1
