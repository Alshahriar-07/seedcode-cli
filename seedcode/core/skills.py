"""Project and global skills (v10.4.0).

A skill is a directory with a ``SKILL.md`` file:

    .seedcode/skills/python-development/SKILL.md
    ~/.seedcode/skills/debugging/SKILL.md

The file starts with optional frontmatter:

    ---
    name: python-development
    description: Conventions for typed Python packages and pytest suites.
    ---
    Full instructions...

Skills are *instructions*, never executable tools. The agent sees only the
catalog (name + one-line description) in its prompt; the full body is loaded
on demand through the ``use_skill`` tool. This is progressive disclosure: a
small local model is not flooded with every workflow it might need.

Scripts inside a skill are never run automatically. Loading a skill returns
text; any command still goes through the normal terminal tool and permission
gate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "Skill",
    "SkillError",
    "discover_skills",
    "load_skill",
    "skill_catalog",
    "skill_body",
]

_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)
_FIELD = re.compile(r"^(name|description)\s*:\s*(.+?)\s*$", re.M | re.I)
_HEADING = re.compile(r"^#\s+(.+?)\s*$", re.M)


class SkillError(ValueError):
    """A skill could not be found or read; the message goes to the model."""


@dataclass(frozen=True, slots=True)
class Skill:
    """One discovered ``SKILL.md``."""

    name: str
    description: str
    path: Path
    source: str  # "project" | "global"

    def body(self) -> str:
        text = self.path.read_text(encoding="utf-8", errors="replace")
        return skill_body(text)


def skill_body(text: str) -> str:
    """The instruction body with frontmatter removed (never the metadata)."""
    match = _FRONTMATTER.match(text or "")
    return (text[match.end() :] if match else (text or "")).strip()


def _metadata(text: str, fallback_name: str) -> tuple[str, str]:
    match = _FRONTMATTER.match(text or "")
    fields: dict[str, str] = {}
    if match:
        for line in match.group(1).splitlines():
            found = _FIELD.match(line.strip())
            if found:
                fields[found.group(1).lower()] = found.group(2).strip().strip("\"'")
    name = fields.get("name") or fallback_name
    description = fields.get("description") or ""
    if not description:
        heading = _HEADING.search(skill_body(text))
        if heading:
            description = heading.group(1).strip()
    if not description:
        description = "No description provided."
    return name.strip(), " ".join(description.split())[:240]


def _root_skills(base: Path, source: str) -> list[Skill]:
    directory = Path(base) / ".seedcode" / "skills"
    if not directory.is_dir():
        return []
    found: list[Skill] = []
    try:
        folders = sorted(p for p in directory.iterdir() if p.is_dir())
    except OSError:
        return []
    for folder in folders:
        path = folder / "SKILL.md"
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        name, description = _metadata(text, folder.name)
        found.append(
            Skill(name=name, description=description, path=path, source=source)
        )
    return found


def discover_skills(
    workspace: str | Path,
    *,
    home: str | Path | None = None,
) -> dict[str, Skill]:
    """Every available skill, keyed by lower-cased name.

    Project skills override a global skill with the same name, so a repository
    can pin its own conventions without renaming them.
    """
    root = Path(workspace)
    global_root = Path(home) if home is not None else Path.home()
    skills: dict[str, Skill] = {}
    for skill in _root_skills(global_root, "global") + _root_skills(root, "project"):
        key = skill.name.strip().lower()
        if key:
            skills[key] = skill
    return skills


def skill_catalog(
    workspace: str | Path,
    *,
    home: str | Path | None = None,
    max_chars: int = 1200,
) -> str:
    """The short catalog injected into the system prompt ('' when none)."""
    skills = discover_skills(workspace, home=home)
    if not skills:
        return ""
    lines = [
        "AVAILABLE SKILLS (instructions; not tools, never auto-executed):",
    ]
    for skill in sorted(skills.values(), key=lambda s: s.name.lower()):
        line = f"- {skill.name} [{skill.source}]: {skill.description}"
        if sum(len(part) + 1 for part in lines) + len(line) > max_chars:
            lines.append("- ... more skills available via use_skill")
            break
        lines.append(line)
    lines.append(
        "When a skill is relevant, call use_skill(name) and follow its "
        "instructions. Skills never override the workspace boundary."
    )
    return "\n".join(lines)


def load_skill(
    workspace: str | Path,
    name: str,
    *,
    home: str | Path | None = None,
) -> str:
    """Load one skill's full instructions (raises :class:`SkillError`)."""
    key = (name or "").strip().lower()
    if not key:
        raise SkillError("Skill name is required.")
    skills = discover_skills(workspace, home=home)
    skill = skills.get(key)
    if skill is None:
        if not skills:
            raise SkillError(
                "No skills are installed. Add .seedcode/skills/<name>/SKILL.md."
            )
        available = ", ".join(sorted(s.name for s in skills.values()))
        raise SkillError(f"Unknown skill '{name}'. Available skills: {available}.")
    try:
        body = skill.body()
    except OSError as exc:
        raise SkillError(f"Could not read skill '{skill.name}': {exc}") from exc
    if not body.strip():
        raise SkillError(f"Skill '{skill.name}' has no instructions.")
    return f"[SKILL {skill.name}] ({skill.source})\n{body}"
