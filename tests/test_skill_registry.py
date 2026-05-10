"""Tests for F-008a: SkillRegistry and oracle reader."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from slay2agent.memory.skill_registry import SkillRegistry, _load_skill_file
from slay2agent.memory.oracle import read_oracle, oracle_version


# ── helpers ────────────────────────────────────────────────────────────────


def make_skill_file(tmp_path: Path, name: str, description: str, when_to_read: str, body: str) -> Path:
    content = textwrap.dedent(f"""\
        ---
        description: {description}
        when_to_read: {when_to_read}
        ---

        {body}
    """)
    p = tmp_path / f"{name}.md"
    p.write_text(content, encoding="utf-8")
    return p


# ── _load_skill_file ───────────────────────────────────────────────────────


def test_load_skill_file_parses_correctly(tmp_path):
    p = make_skill_file(
        tmp_path, "combat_basics",
        description="Basic combat strategy",
        when_to_read="During any combat encounter",
        body="Always play Strike first.",
    )
    skill = _load_skill_file(p)
    assert skill is not None
    assert skill.skill_id == "combat_basics"
    assert skill.description == "Basic combat strategy"
    assert skill.when_to_read == "During any combat encounter"
    assert "Always play Strike first." in skill.body


def test_load_skill_file_missing_frontmatter(tmp_path):
    p = tmp_path / "bad.md"
    p.write_text("Just a plain markdown file with no frontmatter.\n", encoding="utf-8")
    skill = _load_skill_file(p)
    assert skill is None


def test_load_skill_file_nonexistent(tmp_path):
    p = tmp_path / "ghost.md"
    skill = _load_skill_file(p)
    assert skill is None


def test_load_skill_file_empty_frontmatter_fields(tmp_path):
    content = "---\ndescription:\nwhen_to_read:\n---\n\nbody here"
    p = tmp_path / "empty_meta.md"
    p.write_text(content, encoding="utf-8")
    skill = _load_skill_file(p)
    assert skill is not None
    assert skill.description == ""
    assert skill.when_to_read == ""
    assert skill.body == "body here"


# ── SkillRegistry ──────────────────────────────────────────────────────────


def test_registry_empty_dir(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    reg = SkillRegistry(skills_dir)
    assert reg.list_skills() == []
    assert reg.metadata_lines() == []


def test_registry_missing_dir(tmp_path):
    skills_dir = tmp_path / "no_skills"
    reg = SkillRegistry(skills_dir)
    assert reg.list_skills() == []


def test_registry_loads_multiple_skills(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    make_skill_file(skills_dir, "aaa", "Skill A", "When A", "Body A")
    make_skill_file(skills_dir, "bbb", "Skill B", "When B", "Body B")

    reg = SkillRegistry(skills_dir)
    metas = reg.list_skills()
    assert len(metas) == 2
    # sorted by skill_id
    assert metas[0].skill_id == "aaa"
    assert metas[1].skill_id == "bbb"


def test_registry_read_skill_found(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    make_skill_file(skills_dir, "combat", "Combat tips", "In combat", "Tip: end turn early.")

    reg = SkillRegistry(skills_dir)
    skill = reg.read_skill("combat")
    assert skill is not None
    assert skill.skill_id == "combat"
    assert "Tip: end turn early." in skill.body


def test_registry_read_skill_not_found(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    reg = SkillRegistry(skills_dir)
    assert reg.read_skill("nonexistent") is None


def test_registry_metadata_lines_format(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    make_skill_file(skills_dir, "s1", "Do X", "When X happens", "body")

    reg = SkillRegistry(skills_dir)
    lines = reg.metadata_lines()
    assert len(lines) == 1
    assert "[s1]" in lines[0]
    assert "Do X" in lines[0]
    assert "When X happens" in lines[0]


def test_registry_caches_on_repeated_calls(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    make_skill_file(skills_dir, "x", "X skill", "always", "body x")

    reg = SkillRegistry(skills_dir)
    first = reg.list_skills()
    # Add another file on disk — without reload, cache should still return old result
    make_skill_file(skills_dir, "y", "Y skill", "always", "body y")
    second = reg.list_skills()
    assert len(first) == len(second) == 1  # cache not invalidated

    reg.reload()
    third = reg.list_skills()
    assert len(third) == 2  # cache refreshed


def test_registry_list_skills_response(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    make_skill_file(skills_dir, "tip", "A tip", "Whenever", "Full body.")

    reg = SkillRegistry(skills_dir)
    resp = reg.list_skills_response()
    assert "skills" in resp
    assert len(resp["skills"]) == 1
    assert resp["skills"][0]["skill_id"] == "tip"
    assert resp["skills"][0]["description"] == "A tip"


def test_registry_read_skill_response_found(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    make_skill_file(skills_dir, "tip", "A tip", "Whenever", "Full body text.")

    reg = SkillRegistry(skills_dir)
    resp = reg.read_skill_response("tip")
    assert resp["skill_id"] == "tip"
    assert "Full body text." in resp["body"]


def test_registry_read_skill_response_not_found(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    reg = SkillRegistry(skills_dir)
    resp = reg.read_skill_response("missing")
    assert resp["skill_id"] == "missing"
    assert "not found" in resp["body"]


def test_registry_skips_files_without_frontmatter(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "bad.md").write_text("no frontmatter here", encoding="utf-8")
    make_skill_file(skills_dir, "good", "Good skill", "always", "body")

    reg = SkillRegistry(skills_dir)
    metas = reg.list_skills()
    assert len(metas) == 1
    assert metas[0].skill_id == "good"


# ── oracle reader ──────────────────────────────────────────────────────────


def test_read_oracle_missing_file(tmp_path):
    result = read_oracle(tmp_path / "oracle.md")
    assert result == ""


def test_read_oracle_empty_file(tmp_path):
    p = tmp_path / "oracle.md"
    p.write_text("", encoding="utf-8")
    assert read_oracle(p) == ""


def test_read_oracle_whitespace_only(tmp_path):
    p = tmp_path / "oracle.md"
    p.write_text("   \n\n  ", encoding="utf-8")
    assert read_oracle(p) == ""


def test_read_oracle_with_content(tmp_path):
    p = tmp_path / "oracle.md"
    p.write_text("# Global Strategy\nAlways take relics.\n", encoding="utf-8")
    result = read_oracle(p)
    assert "Always take relics." in result


def test_oracle_version_missing(tmp_path):
    assert oracle_version(tmp_path / "oracle.md") is None


def test_oracle_version_exists(tmp_path):
    p = tmp_path / "oracle.md"
    p.write_text("content", encoding="utf-8")
    ver = oracle_version(p)
    assert ver is not None
    assert len(ver) == 15  # YYYYMMDDTHHmmss
