"""Persistent project context: ``.seedcode/context.json`` (v10.2.0).

A session that starts from zero every time re-discovers the same stack, the
same entry points and the same conventions. This module is the small, durable
memory that prevents that: a summarized description of the project — never
source code — that is loaded before planning, refreshed after meaningful
changes, and rebuilt from the workspace if the file is missing or corrupt.

What it stores (and what it deliberately does not):

* identity, stack, description, top-level structure and important files;
* dependencies (names only, not lockfile contents), architecture notes,
  conventions, user requirements, decisions, known issues and completed work;
* timestamps and a schema version for forward compatibility.

It never stores file contents, transcripts, credentials or environment values.
Everything written here is summarized text or a path reference.

Untrusted input: project files are read to *describe* the project. Their text is
never treated as instructions — the system prompt separately tells the model
that file contents are data. A prompt injected into a README is context, not a
command.

Writes are atomic, and a corrupt document is reconstructed rather than fatal.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ..utils.atomic import read_json, write_json_atomic

__all__ = [
    "CONTEXT_FILENAME",
    "SCHEMA_VERSION",
    "ProjectContext",
    "context_path",
    "load_context",
]

CONTEXT_FILENAME = "context.json"
SCHEMA_VERSION = 1

#: Every list field is capped so the document (and its prompt block) stays small.
_MAX_LIST = 40
_MAX_DEPENDENCIES = 40
_MAX_TEXT = 2000

#: Directories a stack scan never descends into.
_SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".seedcode",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        "node_modules",
        "dist",
        "build",
        "target",
        ".next",
        ".turbo",
        ".idea",
        ".vscode",
        "coverage",
        ".pytest_cache",
    }
)

_LANGUAGE_BY_EXT: dict[str, str] = {
    ".py": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".kt": "Kotlin",
    ".cs": "C#",
    ".rb": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".c": "C",
    ".h": "C",
    ".cc": "C++",
    ".cpp": "C++",
    ".hpp": "C++",
    ".html": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".sql": "SQL",
    ".sh": "Shell",
}

#: Marker file -> framework/runtime description.
_MARKERS: tuple[tuple[str, str], ...] = (
    ("package.json", "Node.js"),
    ("tsconfig.json", "TypeScript"),
    ("pyproject.toml", "Python (pyproject)"),
    ("requirements.txt", "Python (pip)"),
    ("setup.py", "Python (setuptools)"),
    ("go.mod", "Go modules"),
    ("Cargo.toml", "Rust (Cargo)"),
    ("pom.xml", "Java (Maven)"),
    ("build.gradle", "Java/Kotlin (Gradle)"),
    ("Gemfile", "Ruby (Bundler)"),
    ("composer.json", "PHP (Composer)"),
    ("Dockerfile", "Docker"),
    ("docker-compose.yml", "Docker Compose"),
    ("next.config.js", "Next.js"),
    ("next.config.ts", "Next.js"),
    ("vite.config.ts", "Vite"),
    ("vite.config.js", "Vite"),
    ("manage.py", "Django"),
    ("pytest.ini", "pytest"),
    ("Cargo.lock", "Rust (Cargo)"),
)

#: Files worth naming in the prompt when they exist.
_IMPORTANT_FILES = (
    "README.md",
    "pyproject.toml",
    "package.json",
    "requirements.txt",
    "tsconfig.json",
    "Cargo.toml",
    "go.mod",
    "Dockerfile",
    "Makefile",
    "AGENTS.md",
    "CLAUDE.md",
)


def context_path(workspace: Path) -> Path:
    """``<workspace>/.seedcode/context.json``."""
    return Path(workspace) / ".seedcode" / CONTEXT_FILENAME


def _clip(text: str, limit: int = _MAX_TEXT) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: max(0, limit - 1)] + "…"


def _unique(values: Iterable[Any], limit: int = _MAX_LIST) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = _clip(value, 300)
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= limit:
            break
    return out


@dataclass
class ProjectContext:
    """The summarized, persistent description of one workspace."""

    version: int = SCHEMA_VERSION
    workspace: str = ""
    name: str = ""
    description: str = ""
    languages: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    structure: list[str] = field(default_factory=list)
    architecture: str = ""
    important_files: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    conventions: list[str] = field(default_factory=list)
    requirements: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    known_issues: list[str] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)
    updated_at: float = 0.0

    # --- serialization --------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "version": int(self.version),
            "workspace": self.workspace,
            "name": self.name,
            "description": self.description,
            "languages": list(self.languages),
            "frameworks": list(self.frameworks),
            "structure": list(self.structure),
            "architecture": self.architecture,
            "important_files": list(self.important_files),
            "dependencies": list(self.dependencies),
            "conventions": list(self.conventions),
            "requirements": list(self.requirements),
            "decisions": list(self.decisions),
            "known_issues": list(self.known_issues),
            "completed": list(self.completed),
            "updated_at": float(self.updated_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ProjectContext":
        """Tolerant load: unknown keys ignored, wrong types coerced or dropped."""
        data = data if isinstance(data, dict) else {}

        def text(key: str) -> str:
            value = data.get(key)
            return value.strip() if isinstance(value, str) else ""

        def items(key: str, limit: int = _MAX_LIST) -> list[str]:
            value = data.get(key)
            if not isinstance(value, list):
                return []
            return _unique([v for v in value if isinstance(v, (str, int, float))], limit)

        try:
            version = int(data.get("version") or 0)
        except (TypeError, ValueError):
            version = 0
        try:
            updated = float(data.get("updated_at") or 0.0)
        except (TypeError, ValueError):
            updated = 0.0

        return cls(
            version=version,
            workspace=text("workspace"),
            name=text("name"),
            description=text("description"),
            languages=items("languages", 12),
            frameworks=items("frameworks", 16),
            structure=items("structure", 20),
            architecture=text("architecture"),
            important_files=items("important_files"),
            dependencies=items("dependencies", _MAX_DEPENDENCIES),
            conventions=items("conventions"),
            requirements=items("requirements"),
            decisions=items("decisions"),
            known_issues=items("known_issues"),
            completed=items("completed"),
            updated_at=updated,
        )

    # --- validation -----------------------------------------------------------
    def problems(self) -> list[str]:
        """Schema problems that make this document untrustworthy.

        A version mismatch or a workspace identity mismatch is *not* corruption:
        those are refreshed from the workspace. Only structurally broken values
        are reported.
        """
        found: list[str] = []
        if not isinstance(self.version, int) or self.version < 0:
            found.append("invalid version")
        for name in (
            "languages",
            "frameworks",
            "structure",
            "important_files",
            "dependencies",
            "conventions",
            "requirements",
            "decisions",
            "known_issues",
            "completed",
        ):
            if not isinstance(getattr(self, name), list):
                found.append(f"{name} is not a list")
        if self.version > SCHEMA_VERSION:
            found.append(
                f"version {self.version} is newer than supported {SCHEMA_VERSION}"
            )
        return found

    def valid(self) -> bool:
        return not self.problems()

    # --- lifecycle ------------------------------------------------------------
    @classmethod
    def reconstruct(cls, workspace: Path) -> "ProjectContext":
        """Build the document from the real workspace (no model involvement)."""
        from .project import detect_project

        root = Path(workspace)
        context = cls(
            version=SCHEMA_VERSION,
            workspace=str(root),
            name=root.name or "workspace",
            updated_at=time.time(),
        )
        try:
            info = detect_project(root)
            context.structure = _unique(
                [line.strip() for line in info.summary.splitlines()[1:] if line.strip()],
                20,
            )
            context.frameworks = _unique(list(info.kinds), 16)
        except Exception:
            context.structure = []
        context.languages = _detect_languages(root)
        context.frameworks = _unique(context.frameworks + _detect_frameworks(root), 16)
        context.important_files = [
            name for name in _IMPORTANT_FILES if (root / name).is_file()
        ]
        context.dependencies = _read_dependencies(root)
        return context

    @classmethod
    def load(
        cls,
        workspace: Path,
        *,
        refresh: bool = True,
        create: bool = True,
    ) -> "ProjectContext":
        """Load, validate and (optionally) refresh the document.

        Missing, corrupt or invalid documents are reconstructed from the
        workspace; a schema change refreshes the summary while keeping the
        durable user knowledge (requirements, decisions, completed work).
        """
        root = Path(workspace)
        raw = read_json(context_path(root))
        context = cls.from_dict(raw) if isinstance(raw, dict) else cls.reconstruct(root)

        if raw is None or context.problems():
            rebuilt = cls.reconstruct(root)
            if isinstance(raw, dict):
                # Keep the durable knowledge; replace the observed facts.
                rebuilt.requirements = context.requirements
                rebuilt.decisions = context.decisions
                rebuilt.completed = context.completed
                rebuilt.conventions = context.conventions
            context = rebuilt
        else:
            if context.version != SCHEMA_VERSION:
                rebuilt = cls.reconstruct(root)
                rebuilt.requirements = context.requirements
                rebuilt.decisions = context.decisions
                rebuilt.completed = context.completed
                rebuilt.conventions = context.conventions
                rebuilt.architecture = context.architecture
                context = rebuilt

        if refresh:
            context.refresh(root, save=False)
        if create:
            context.save(root)
        return context

    def refresh(self, workspace: Path, *, save: bool = True) -> None:
        """Re-derive the observed facts, preserving the recorded knowledge."""
        fresh = self.reconstruct(workspace)
        self.workspace = fresh.workspace
        self.name = self.name or fresh.name
        self.languages = fresh.languages
        self.frameworks = fresh.frameworks
        self.structure = fresh.structure
        self.important_files = _unique(self.important_files + fresh.important_files, 24)
        self.dependencies = fresh.dependencies
        self.updated_at = time.time()
        if save:
            self.save(workspace)

    def save(self, workspace: Path | None = None) -> bool:
        """Persist atomically under ``workspace`` (or the stored workspace)."""
        root = Path(workspace) if workspace is not None else Path(self.workspace or ".")
        self.workspace = str(root)
        self.version = SCHEMA_VERSION
        self.updated_at = time.time()
        return write_json_atomic(context_path(root), self.to_dict())

    # --- knowledge updates ----------------------------------------------------
    def note_requirement(self, text: str) -> None:
        value = _clip(text, 300)
        if value and value not in self.requirements:
            self.requirements.append(value)
            del self.requirements[:-_MAX_LIST]

    def note_decision(self, text: str) -> None:
        value = _clip(text, 400)
        if value and value not in self.decisions:
            self.decisions.append(value)
            del self.decisions[:-_MAX_LIST]

    def note_convention(self, text: str) -> None:
        value = _clip(text, 240)
        if value and value not in self.conventions:
            self.conventions.append(value)
            del self.conventions[:-_MAX_LIST]

    def note_issue(self, text: str) -> None:
        value = _clip(text, 400)
        if value and value not in self.known_issues:
            self.known_issues.append(value)
            del self.known_issues[:-_MAX_LIST]

    def note_completed(self, text: str) -> None:
        value = _clip(text, 240)
        if value and value not in self.completed:
            self.completed.append(value)
            del self.completed[:-_MAX_LIST]

    def merge_evidence(self, evidence: Any) -> None:
        """Fold one turn's observed facts into the document (never a claim)."""
        for path in getattr(evidence, "changed_files", []) or []:
            value = _clip(path, 300)
            if value and value not in self.important_files:
                self.important_files.append(value)
        # Keep the last 24 entries; a short list must never be emptied
        # (``[-24:]`` deletes everything, ``[:-24]`` deletes the surplus).
        del self.important_files[:-24]
        for message in getattr(evidence, "errors", []) or []:
            self.note_issue(message)
        self.updated_at = time.time()

    def note_task_completed(self, task: Any) -> None:
        """Record a *verified* task, with the evidence that verified it."""
        task_id = getattr(task, "id", "")
        title = getattr(task, "title", "")
        verification = getattr(task, "verification", "")
        label = f"{task_id}. {title}".strip()
        if verification:
            label = f"{label} — {verification}"
        self.note_completed(label)

    # --- prompt rendering -----------------------------------------------------
    def to_prompt(self, *, max_chars: int = 1600) -> str:
        """A short, bounded summary block for the model.

        Ordered from most to least actionable. Nothing here is presented as
        current truth without the model verifying it against the workspace.
        """
        lines: list[str] = []
        if self.name or self.description:
            lines.append(f"Project: {self.name or 'workspace'}" + (
                f" — {self.description}" if self.description else ""
            ))
        if self.languages:
            lines.append("Languages: " + ", ".join(self.languages[:8]))
        if self.frameworks:
            lines.append("Stack: " + ", ".join(self.frameworks[:10]))
        if self.dependencies:
            lines.append("Dependencies: " + ", ".join(self.dependencies[:20]))
        if self.important_files:
            lines.append("Important files: " + ", ".join(self.important_files[:12]))
        if self.structure:
            lines.append("Top level: " + ", ".join(self.structure[:12]))
        if self.architecture:
            lines.append("Architecture: " + _clip(self.architecture, 400))
        if self.conventions:
            lines.append("Conventions: " + "; ".join(self.conventions[-5:]))
        if self.requirements:
            lines.append("User requirements: " + "; ".join(self.requirements[-6:]))
        if self.decisions:
            lines.append("Decisions: " + "; ".join(self.decisions[-6:]))
        if self.known_issues:
            lines.append("Known issues: " + "; ".join(self.known_issues[-4:]))
        if self.completed:
            lines.append("Completed work: " + "; ".join(self.completed[-8:]))
        text = "\n".join(f"- {line}" for line in lines)
        if not text:
            return ""
        if len(text) > max_chars:
            text = text[: max(0, max_chars - 1)] + "…"
        return text


# --- workspace facts ---------------------------------------------------------
def _detect_languages(root: Path) -> list[str]:
    counts: dict[str, int] = {}
    visited = 0
    for path in root.rglob("*"):
        if visited >= 800:
            break
        try:
            if path.is_dir():
                continue
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            visited += 1
            language = _LANGUAGE_BY_EXT.get(path.suffix.lower())
        except OSError:
            continue
        if language:
            counts[language] = counts.get(language, 0) + 1
    return [name for name, _count in sorted(counts.items(), key=lambda kv: -kv[1])[:8]]


def _detect_frameworks(root: Path) -> list[str]:
    found: list[str] = []
    for marker, label in _MARKERS:
        try:
            if (root / marker).is_file():
                found.append(label)
        except OSError:
            continue
    return found


def _read_dependencies(root: Path) -> list[str]:
    """Dependency *names* from the common manifests — never their contents."""
    names: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if not text or text.startswith("#"):
            return
        name = text.split("==")[0].split(">=")[0].split("<")[0].split("~=")[0]
        name = name.split("[")[0].strip()
        if name and name not in names:
            names.append(name[:60])

    package = read_json(root / "package.json")
    if isinstance(package, dict):
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            section = package.get(key)
            if isinstance(section, dict):
                for name in section:
                    add(name)

    requirements = _read_text(root / "requirements.txt")
    for line in requirements.splitlines():
        if line.strip() and not line.strip().startswith(("-", "git+", "http")):
            add(line)

    pyproject = _read_text(root / "pyproject.toml")
    in_deps = False
    for line in pyproject.splitlines():
        stripped = line.strip()
        if stripped.startswith("dependencies") and stripped.endswith("["):
            in_deps = True
            continue
        if in_deps and stripped.startswith("]"):
            in_deps = False
            continue
        if in_deps:
            add(stripped.strip('",\' '))

    cargo = _read_text(root / "Cargo.toml")
    for line in cargo.splitlines():
        stripped = line.strip()
        if "=" in stripped and not stripped.startswith(("[", "#")):
            name = stripped.split("=")[0].strip()
            if name and name != "version":
                add(name)

    go_mod = _read_text(root / "go.mod")
    for line in go_mod.splitlines():
        stripped = line.strip()
        if stripped.startswith("require "):
            add(stripped[len("require "):].split()[0])

    return names[:_MAX_DEPENDENCIES]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def load_context(workspace: Path, *, refresh: bool = True) -> ProjectContext:
    """Load (or reconstruct) the workspace's persistent project context."""
    return ProjectContext.load(workspace, refresh=refresh)
