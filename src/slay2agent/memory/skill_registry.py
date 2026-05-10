"""Skill registry — L1 memory layer (F-008a).

Skills live in ``agent_state/skills/<skill_id>.md``.

File format
-----------
Each skill file is a Markdown document with a YAML frontmatter block::

    ---
    description: One-line description shown in every system prompt.
    when_to_read: Free-text hint for when the agent should read the full body.
    ---

    Full markdown body — only loaded when the agent calls read_skill(skill_id).

The ``skill_id`` is derived from the file stem (e.g. ``combat_basics`` for
``combat_basics.md``).  File names must be valid Python identifiers or
alphanumeric-with-hyphens strings; no spaces.

All public methods return plain Python objects so they are easy to test and
to serialise into tool responses.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Matches the YAML frontmatter block at the top of a skill file.
# Group 1: frontmatter text (between the two --- lines)
# Group 2: body text (everything after the closing ---)
_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(.*?)\n---\s*\n(.*)",
    re.DOTALL,
)


@dataclass(frozen=True)
class SkillMeta:
    """Metadata header for a skill — injected into every system prompt."""

    skill_id: str
    description: str
    when_to_read: str


@dataclass(frozen=True)
class Skill:
    """Full skill including body — only loaded on read_skill() calls."""

    skill_id: str
    description: str
    when_to_read: str
    body: str


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Parse a minimal YAML frontmatter block into a dict.

    Only handles simple ``key: value`` lines (no nested keys, no lists).
    Unknown keys are ignored.  Missing keys return an empty string.
    """
    result: dict[str, str] = {}
    for line in text.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip()
    return result


def _load_skill_file(path: Path) -> Skill | None:
    """Parse a single skill ``.md`` file.  Returns None on parse errors."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.error("skill_registry: cannot read %s: %s", path, exc)
        return None

    m = _FRONTMATTER_RE.match(raw)
    if not m:
        logger.warning(
            "skill_registry: %s has no valid frontmatter — skipping", path.name
        )
        return None

    fm = _parse_frontmatter(m.group(1))
    body = m.group(2).strip()
    skill_id = path.stem

    return Skill(
        skill_id=skill_id,
        description=fm.get("description", ""),
        when_to_read=fm.get("when_to_read", ""),
        body=body,
    )


class SkillRegistry:
    """Read-only view of the skill library on disk.

    The registry is *lazy*: it scans the directory on first use and caches
    results.  Call ``reload()`` to re-scan after on-disk changes (e.g. after
    a skill_creator sub-agent run).

    Usage::

        registry = SkillRegistry(Path("agent_state/skills"))
        metas = registry.list_skills()          # cheap — metadata only
        skill = registry.read_skill("combat_basics")  # loads full body
    """

    def __init__(self, skills_dir: Path) -> None:
        self._skills_dir = skills_dir
        self._cache: dict[str, Skill] | None = None

    # ── public API ─────────────────────────────────────────────────────────

    def list_skills(self) -> list[SkillMeta]:
        """Return metadata for all skills, sorted by skill_id."""
        return [
            SkillMeta(
                skill_id=s.skill_id,
                description=s.description,
                when_to_read=s.when_to_read,
            )
            for s in sorted(self._load().values(), key=lambda s: s.skill_id)
        ]

    def read_skill(self, skill_id: str) -> Skill | None:
        """Return the full skill (including body) or None if not found."""
        return self._load().get(skill_id)

    def reload(self) -> None:
        """Invalidate the in-memory cache and re-scan the skills directory."""
        self._cache = None
        logger.debug("skill_registry: cache invalidated")

    # ── system-prompt helpers ───────────────────────────────────────────────

    def metadata_lines(self) -> list[str]:
        """Render all skill metadata as compact lines for system prompt injection.

        Each line is ``[<id>] <description>  (when_to_read: <hint>)``.
        Returns an empty list when the skill library is empty.
        """
        metas = self.list_skills()
        if not metas:
            return []
        return [
            f"[{m.skill_id}] {m.description}  (when_to_read: {m.when_to_read})"
            for m in metas
        ]

    # ── tool-response helpers ───────────────────────────────────────────────

    def list_skills_response(self) -> dict:
        """JSON-serialisable response for the list_skills tool call."""
        return {
            "skills": [
                {
                    "skill_id": m.skill_id,
                    "description": m.description,
                    "when_to_read": m.when_to_read,
                }
                for m in self.list_skills()
            ]
        }

    def read_skill_response(self, skill_id: str) -> dict:
        """JSON-serialisable response for the read_skill tool call."""
        skill = self.read_skill(skill_id)
        if skill is None:
            return {
                "skill_id": skill_id,
                "body": f"(skill '{skill_id}' not found)",
            }
        return {
            "skill_id": skill.skill_id,
            "description": skill.description,
            "when_to_read": skill.when_to_read,
            "body": skill.body,
        }

    # ── write helpers ──────────────────────────────────────────────────────

    def write_skill(
        self,
        skill_id: str,
        description: str,
        when_to_read: str,
        body: str,
    ) -> None:
        """Create or overwrite a skill file and invalidate cache.

        The file is written in the standard frontmatter format so it can be
        read back by ``_load_skill_file``.
        """
        self._skills_dir.mkdir(parents=True, exist_ok=True)
        path = self._skills_dir / f"{skill_id}.md"
        content = (
            f"---\n"
            f"description: {description}\n"
            f"when_to_read: {when_to_read}\n"
            f"---\n\n"
            f"{body}\n"
        )
        path.write_text(content, encoding="utf-8")
        self._cache = None
        logger.info("skill_registry: wrote skill %r to %s", skill_id, path)

    def delete_skill(self, skill_id: str) -> bool:
        """Delete a skill file.

        Returns ``True`` if the file existed and was removed, ``False`` if the
        skill was not found.  Invalidates the cache on success.
        """
        path = self._skills_dir / f"{skill_id}.md"
        if not path.exists():
            logger.debug("skill_registry: delete_skill %r — not found", skill_id)
            return False
        path.unlink()
        self._cache = None
        logger.info("skill_registry: deleted skill %r", skill_id)
        return True

    # ── internal ───────────────────────────────────────────────────────────

    def _load(self) -> dict[str, Skill]:
        if self._cache is not None:
            return self._cache

        skills: dict[str, Skill] = {}

        if not self._skills_dir.exists():
            logger.warning(
                "skill_registry: skills_dir %s does not exist — empty library",
                self._skills_dir,
            )
            self._cache = skills
            return self._cache

        for path in sorted(self._skills_dir.glob("*.md")):
            skill = _load_skill_file(path)
            if skill is not None:
                skills[skill.skill_id] = skill
                logger.debug("skill_registry: loaded skill %r", skill.skill_id)

        logger.info("skill_registry: loaded %d skill(s) from %s", len(skills), self._skills_dir)
        self._cache = skills
        return self._cache
