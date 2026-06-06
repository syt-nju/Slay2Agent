"""Skill registry — L1 memory layer (F-008a).

Skills live in ``agent_state/skills/<skill_id>.md`` as flat single-file
markdown documents. The format aligns with the mainstream agent skill
convention (Claude Code / Cursor ``.cursor/skills/*/SKILL.md``), extended with
a ``failure_reason`` frontmatter field used only by the offline skill
maintenance pipeline (F-013):

    ---
    name: Ironclad — Early Combat
    failure_reason: Wastes energy attacking before setting up block, dies to
      multi-hit enemies.
    description: Strategies for early-floor combat as Ironclad. Use when playing
      Ironclad in Act 1 normal/elite fights where the deck is still mostly
      starter cards.
    ---

    # Ironclad — Early Combat

    Full markdown body — only loaded when the agent calls read_skill(skill_id).

Frontmatter fields:
    - ``name``: human-readable display name (free-form).
    - ``failure_reason``: the failure this skill addresses. Read by the F-013
      distill pass for dedup; NOT injected into the play-time system prompt.
    - ``description``: SOLE play-time trigger signal. Must describe both *what*
      the skill covers AND *when* to load it.

``skill_id`` is derived from the filename stem (e.g. ``ironclad_early_combat``
for ``ironclad_early_combat.md``). Filenames are snake_case identifiers.

At play time the skill library is **read-only**: all skill ``description``\\ s
are injected into the system prompt, and bodies are fetched on demand via
``read_skill``. Skill creation / merge / delete happens only in the offline
F-013 pipeline.
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
    failure_reason: str = ""


@dataclass(frozen=True)
class Skill:
    """Full skill including body — only loaded on read_skill() calls."""

    skill_id: str
    name: str
    description: str
    body: str
    failure_reason: str = ""


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
        failure_reason=fm.get("failure_reason", ""),
    )


class SkillRegistry:
    """Skill library backed by flat markdown files.

    The registry is *lazy*: it scans the directory on first use and caches
    the parsed result in memory.  Call ``reload()`` to re-scan after on-disk
    changes (e.g. after an offline F-013 distill run).

    Usage::

        registry = SkillRegistry(Path("agent_state/skills"))
        metas = registry.list_skills()                 # all skills
        skill = registry.read_skill("combat_basics")   # full body
    """

    def __init__(self, skills_dir: Path) -> None:
        self._skills_dir = skills_dir
        self._disk_cache: dict[str, Skill] | None = None

    # ── public API ─────────────────────────────────────────────────────────

    def list_skills(self) -> list[SkillMeta]:
        """Return metadata for ALL skills, sorted by skill_id."""
        return [
            SkillMeta(
                skill_id=s.skill_id,
                name=s.name,
                description=s.description,
                failure_reason=s.failure_reason,
            )
            for s in sorted(self._load().values(), key=lambda s: s.skill_id)
        ]

    def read_skill(self, skill_id: str) -> Skill | None:
        """Return the full skill (including body) or None if not found."""
        return self._load().get(skill_id)

    def reload(self) -> None:
        """Invalidate the in-memory cache and re-scan the skills directory."""
        self._disk_cache = None
        logger.debug("skill_registry: cache invalidated")

    # ── system-prompt helpers ───────────────────────────────────────────────

    def metadata_lines(self) -> list[str]:
        """Render skill metadata for system-prompt injection.

        Injects every skill's ``description`` (the sole play-time trigger
        signal). ``failure_reason`` is intentionally excluded — it is only used
        offline by the F-013 distill pass.
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
        metas = self.list_skills()
        return {
            "skills": [
                {
                    "skill_id": m.skill_id,
                    "name": m.name,
                    "description": m.description,
                }
                for m in metas
            ],
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
        failure_reason: str = "",
    ) -> None:
        """Create or overwrite a skill file.

        The file is written in the standard frontmatter format so it can be
        read back by ``_load_skill_file``.
        """
        self._skills_dir.mkdir(parents=True, exist_ok=True)
        path = self._skills_dir / f"{skill_id}.md"
        content = (
            f"---\n"
            f"name: {name}\n"
            f"failure_reason: {failure_reason}\n"
            f"description: {description}\n"
            f"---\n\n"
            f"{body.strip()}\n"
        )
        path.write_text(content, encoding="utf-8")
        self._disk_cache = None
        logger.info("skill_registry: wrote skill %r to %s", skill_id, path)

    def delete_skill(self, skill_id: str) -> bool:
        """Delete a skill file.

        Returns ``True`` if the file existed and was removed, ``False`` if the
        skill was not found.
        """
        path = self._skills_dir / f"{skill_id}.md"
        if not path.exists():
            logger.debug("skill_registry: delete_skill %r — not found", skill_id)
            return False
        path.unlink()
        self._disk_cache = None
        logger.info("skill_registry: deleted skill %r", skill_id)
        return True

    # ── internal ───────────────────────────────────────────────────────────

    def _load(self) -> dict[str, Skill]:
        if self._disk_cache is not None:
            return self._disk_cache

        skills: dict[str, Skill] = {}

        if not self._skills_dir.exists():
            logger.warning(
                "skill_registry: skills_dir %s does not exist — empty library",
                self._skills_dir,
            )
            self._disk_cache = skills
            return self._disk_cache

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
        self._disk_cache = skills
        return self._disk_cache
