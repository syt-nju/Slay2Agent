"""Skill registry — L1 memory layer (F-008a).

Skills live in ``agent_state/skills/<skill_id>.md`` as flat single-file
markdown documents. The format is aligned with the mainstream agent skill
convention (Claude Code / Cursor ``.cursor/skills/*/SKILL.md``):

    ---
    name: Ironclad — Early Combat
    description: Strategies for early-floor combat as Ironclad, focusing on
      exhaustion mechanics and AoE damage cards. Use when playing Ironclad in
      Act 1 normal/elite fights where the deck is still mostly starter cards.
    ---

    # Ironclad — Early Combat

    Full markdown body — only loaded when the agent calls read_skill(skill_id).

Frontmatter fields:
    - ``name``: human-readable display name (free-form).
    - ``description``: SOLE trigger signal. Must describe both *what* the skill
      covers AND *when* to load it (mainstream pattern — see e.g.
      ``.cursor/skills/karparthy-guideline/SKILL.md``).

``skill_id`` is derived from the filename stem (e.g. ``ironclad_early_combat``
for ``ironclad_early_combat.md``). Filenames are snake_case identifiers.

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
    name: str
    description: str


@dataclass(frozen=True)
class Skill:
    """Full skill including body — only loaded on read_skill() calls."""

    skill_id: str
    name: str
    description: str
    body: str


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Parse a minimal YAML frontmatter block into a dict.

    Supports ``key: value`` lines plus simple multi-line continuation for
    long ``description`` blocks (any line that does not contain a colon and
    is not blank is appended to the previous key's value, joined by a
    single space).  Unknown keys are ignored.
    """
    result: dict[str, str] = {}
    last_key: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            last_key = None
            continue
        if ":" in line and not line.startswith((" ", "\t")):
            key, _, value = line.partition(":")
            key = key.strip()
            result[key] = value.strip()
            last_key = key
        elif last_key is not None:
            # Continuation line (indented or follows a known key).
            result[last_key] = (result[last_key] + " " + line.strip()).strip()
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
        # Fall back to skill_id when name is missing OR blank — we always want
        # something printable in metadata_lines.
        name=fm.get("name") or skill_id,
        description=fm.get("description", ""),
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
                name=s.name,
                description=s.description,
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
        """Render skill metadata as compact lines for system-prompt injection.

        Each line is ``- [<id>] <name> — <description>``.  The description is
        the SOLE signal the main agent uses to decide whether to call
        ``read_skill``, so it must already encode both "what" and "when to
        use" (mainstream pattern).

        Returns an empty list when the skill library is empty.
        """
        metas = self.list_skills()
        if not metas:
            return []
        return [
            f"- [{m.skill_id}] {m.name} — {m.description}"
            for m in metas
        ]

    # ── tool-response helpers ───────────────────────────────────────────────

    def list_skills_response(self) -> dict:
        """JSON-serialisable response for the list_skills tool call."""
        return {
            "skills": [
                {
                    "skill_id": m.skill_id,
                    "name": m.name,
                    "description": m.description,
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
            "name": skill.name,
            "description": skill.description,
            "body": skill.body,
        }

    # ── write helpers ──────────────────────────────────────────────────────

    def write_skill(
        self,
        skill_id: str,
        name: str,
        description: str,
        body: str,
    ) -> None:
        """Create or overwrite a skill file and invalidate cache.

        The file is written in the standard frontmatter format so it can be
        read back by ``_load_skill_file``.  The body is written verbatim and
        should be a self-contained markdown document (start with an ``H1``
        title so it looks like a proper SKILL.md).
        """
        self._skills_dir.mkdir(parents=True, exist_ok=True)
        path = self._skills_dir / f"{skill_id}.md"
        content = (
            f"---\n"
            f"name: {name}\n"
            f"description: {description}\n"
            f"---\n\n"
            f"{body.strip()}\n"
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

        logger.info(
            "skill_registry: loaded %d skill(s) from %s",
            len(skills),
            self._skills_dir,
        )
        self._cache = skills
        return self._cache
