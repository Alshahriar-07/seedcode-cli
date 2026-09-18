""".seedcode — workspace-aware project memory for Code Mode (v6.2.0).

When Code Mode is enabled, the project root gains a ``.seedcode/`` directory::

    .seedcode/
    ├── memory/     durable project knowledge (architecture, decisions…)
    ├── index/      per-file summaries + a file map (incremental, hashed)
    ├── context/    reusable project context (conventions, snippets)
    ├── sessions/   compact per-session summaries (never raw transcripts)
    └── config.json safe project configuration

Design rules:

* **Incremental indexing.** Every indexed file is hashed; on the next enable
  only files whose hash changed are re-summarised (``refresh_index``), so a
  large project costs one ``stat`` per file instead of a full re-read.
* **Secrets never land here.** Everything written passes the same
  secret-key filter as :mod:`seedcode.memory.store` — API keys, tokens, and
  passwords are rejected at write time.
* **Not source code.** ``.seedcode`` is excluded from workspace search/index
  (``tools.search._INDEX_SKIP`` / ``filesystem._INDEX_SKIP``) and from the
  agent's project tree, and the repo-level ``.gitignore`` already lists it.
* **Session notes are compact.** ``save_session_summary`` stores one small
  JSON per session (goal, changes, verification) — never raw transcripts.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

# Keys that must never be persisted into .seedcode (mirrors memory.store).
_SECRET_KEY_RE = re.compile(
    r"(password|passwd|pwd|secret|token|api[_-]?key|apikey|credential|"
    r"auth|cookie|session[_-]?id|private[_-]?key|access[_-]?key)",
    re.IGNORECASE,
)

SEEDCODE_DIRNAME = ".seedcode"

# Files whose content is worth summarising into the index.
_CODE_SUFFIXES = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs", ".go", ".rs", ".java",
    ".kt", ".c", ".h", ".cpp", ".hpp", ".cs", ".rb", ".php", ".swift", ".m",
    ".md", ".toml", ".yaml", ".yml", ".json", ".sql", ".sh", ".ps1", ".bat",
}
_MAX_INDEX_FILE_BYTES = 256 * 1024
_MAX_SUMMARY_LINES = 40
_MAX_INDEX_FILES = 2_000

DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "codemode": True,
    "notes": "Safe project configuration only — never store secrets here.",
}


# --- secret guard -------------------------------------------------------------
def assert_no_secrets(data: Any, path: str = "") -> None:
    """Reject nested secret-looking keys before anything touches disk."""
    if isinstance(data, dict):
        for key, value in data.items():
            here = f"{path}.{key}" if path else str(key)
            if _SECRET_KEY_RE.search(str(key)):
                raise ValueError(
                    f"Refusing to store a secret-looking field ('{here}') in "
                    ".seedcode. Credentials belong in a credential manager."
                )
            assert_no_secrets(value, here)
    elif isinstance(data, list):
        for i, item in enumerate(data):
            assert_no_secrets(item, f"{path}[{i}]")


def _write_json(path: Path, payload: dict[str, Any]) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        tmp.replace(path)
        return True
    except (OSError, ValueError):
        return False


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_text(path: Path, text: str) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return True
    except OSError:
        return False


# --- the workspace store ---------------------------------------------------------
class SeedcodeStore:
    """Read/write access to one project's ``.seedcode/`` directory."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.root = workspace / SEEDCODE_DIRNAME

    # --- structure -----------------------------------------------------------
    def ensure(self) -> bool:
        """Create the full directory skeleton when missing; True when ready."""
        try:
            for sub in ("memory", "index", "context", "sessions"):
                (self.root / sub).mkdir(parents=True, exist_ok=True)
            config_path = self.root / "config.json"
            if not config_path.exists():
                _write_json(config_path, DEFAULT_CONFIG)
            return self.root.is_dir()
        except OSError:
            return False

    @property
    def exists(self) -> bool:
        return self.root.is_dir()

    # --- paths -----------------------------------------------------------------
    def memory_path(self, name: str) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9._-]", "_", name)[:60] or "note"
        return self.root / "memory" / f"{safe}.md"

    # --- memory ------------------------------------------------------------------
    def save_memory(self, name: str, content: str) -> bool:
        """Persist one markdown memory note (architecture, decisions, ...)."""
        self.ensure()
        return _write_text(self.memory_path(name), content)

    def load_memory(self, name: str) -> str:
        try:
            return self.memory_path(name).read_text(encoding="utf-8")
        except OSError:
            return ""

    def list_memories(self) -> list[str]:
        try:
            return sorted(p.stem for p in (self.root / "memory").glob("*.md"))
        except OSError:
            return []

    # --- context ---------------------------------------------------------------
    def save_context(self, name: str, content: str) -> bool:
        self.ensure()
        return _write_text(self.root / "context" / f"{name}.md", content)

    def load_context(self, name: str) -> str:
        try:
            return (self.root / "context" / f"{name}.md").read_text(encoding="utf-8")
        except OSError:
            return ""

    # --- sessions ---------------------------------------------------------------
    def save_session_summary(self, summary: dict[str, Any]) -> bool:
        """One compact JSON per session (never a raw transcript)."""
        self.ensure()
        assert_no_secrets(summary)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        return _write_json(self.root / "sessions" / f"session-{stamp}.json", summary)

    def latest_session_summaries(self, limit: int = 5) -> list[dict[str, Any]]:
        try:
            paths = sorted((self.root / "sessions").glob("session-*.json"), reverse=True)
        except OSError:
            return []
        out: list[dict[str, Any]] = []
        for path in paths[: max(1, limit)]:
            data = _read_json(path)
            if data:
                out.append(data)
        return out

    # --- config -----------------------------------------------------------------
    def load_config(self) -> dict[str, Any]:
        return _read_json(self.root / "config.json") or dict(DEFAULT_CONFIG)

    def save_config(self, config: dict[str, Any]) -> bool:
        assert_no_secrets(config)
        return _write_json(self.root / "config.json", config)

    # --- index --------------------------------------------------------------------
    def _map_path(self) -> Path:
        return self.root / "index" / "files.json"

    def load_file_map(self) -> dict[str, dict[str, Any]]:
        data = _read_json(self._map_path())
        files = data.get("files") if data else None
        return files if isinstance(files, dict) else {}

    def _save_file_map(self, files: dict[str, dict[str, Any]]) -> bool:
        return _write_json(self._map_path(), {"files": files})

    @staticmethod
    def _hash_text(text: str) -> str:
        return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()

    def index_file(self, rel: str, text: str) -> dict[str, Any]:
        """Summarise one file's text (used by tests; refresh_index calls _summarize)."""
        return self._summarize(text)

    def _summarize(self, text: str) -> dict[str, Any]:
        lines = text.splitlines()
        header = lines[0].strip() if lines else ""
        # Collect low-cost orientation signals.
        symbols: list[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(("def ", "class ", "function ", "fn ", "pub fn ")):
                # Store the bare identifier ('login', not 'def login').
                head = stripped.split("(")[0].split(":")[0].strip()
                for keyword in ("def ", "class ", "function ", "fn ", "pub fn "):
                    if head.startswith(keyword):
                        head = head[len(keyword):].strip()
                        break
                if head:
                    symbols.append(head[:60])
            if len(symbols) >= 12:
                break
        return {
            "lines": len(lines),
            "header": header[:120],
            "symbols": symbols,
        }

    def refresh_index(self, changed_limit: int = 200) -> dict[str, int]:
        """Incremental index refresh over workspace text files.

        Returns ``{"indexed": n, "unchanged": m}``. Only files whose content
        hash differs from the stored map are re-read and re-summarised; a
        fresh index walks the tree once, afterwards each refresh costs one
        ``stat`` per file until something changes.
        """
        self.ensure()
        old_map = self.load_file_map()
        new_map: dict[str, dict[str, Any]] = {}
        indexed = unchanged = 0
        try:
            entries = sorted(self.workspace.rglob("*"))
        except OSError:
            entries = []
        for path in entries:
            if len(new_map) >= _MAX_INDEX_FILES:
                break
            name = path.name
            if not path.is_file():
                continue
            if (
                SEEDCODE_DIRNAME in path.parts
                or name.startswith(".")
                or name in _INDEX_SKIP_NAMES
                or _INDEX_SKIP_NAMES.intersection(path.parts)
            ):
                continue
            if path.suffix.lower() not in _CODE_SUFFIXES:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size > _MAX_INDEX_FILE_BYTES:
                continue
            rel = path.relative_to(self.workspace).as_posix()
            new_map[rel] = {"size": size}
            previous = old_map.get(rel)
            if (
                previous
                and previous.get("size") == size
                and (self.root / "index" / f"{_entry_hash(rel)}.json").exists()
            ):
                new_map[rel].update(previous)
                unchanged += 1
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            summary = self._summarize(text)
            digest = self._hash_text(text)
            entry = {**summary, "hash": digest, "size": size}
            if indexed < changed_limit:
                _write_json(self.root / "index" / f"{_entry_hash(rel)}.json", entry)
            indexed += 1
            new_map[rel].update(entry)
        self._save_file_map(new_map)
        return {"indexed": indexed, "unchanged": unchanged}

    def query_index(self, needle: str, limit: int = 12) -> list[dict[str, Any]]:
        """Search the index map (paths, headers, symbols) without reading files."""
        low = needle.strip().lower()
        if not low:
            return []
        hits: list[dict[str, Any]] = []
        for rel, entry in self.load_file_map().items():
            hay = " ".join([
                rel,
                str(entry.get("header", "")),
                " ".join(str(s) for s in entry.get("symbols", [])),
            ]).lower()
            if low in hay:
                hits.append({"path": rel, **{k: entry.get(k) for k in ("header", "symbols", "lines")}})
                if len(hits) >= limit:
                    break
        return hits


def _entry_hash(rel: str) -> str:
    return hashlib.sha1(rel.encode("utf-8", "replace")).hexdigest()[:16]


# Directory names that never belong in a .seedcode index.
_INDEX_SKIP_NAMES = {
    "__pycache__", "node_modules", ".venv", "venv", "dist", "build",
    ".git", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".idea", ".vscode",
    "target", "vendor", ".next", ".cache",
}


def workspace_iter(root: Path):
    """Workspace files for indexing/search, skipping .seedcode and caches."""
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            continue
        for entry in entries:
            if entry.name in _INDEX_SKIP_NAMES or entry.name.startswith("."):
                continue
            if entry.is_dir():
                stack.append(entry)
            elif entry.is_file():
                yield entry


# --- module-level helpers ---------------------------------------------------------
def store_for(workspace: Path) -> SeedcodeStore:
    return SeedcodeStore(workspace)


__all__ = [
    "SEEDCODE_DIRNAME",
    "DEFAULT_CONFIG",
    "SeedcodeStore",
    "assert_no_secrets",
    "store_for",
    "workspace_iter",
]
