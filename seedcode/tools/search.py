"""Search tools: filename glob and text search across the workspace.

Pure-Python (pathlib + a linear scan) so search works identically on Windows,
Linux, and macOS with no external binaries.

v9.3.2 hardening: ``search_text`` gained an optional ``ignore_case`` flag and
an ``exclude`` path glob; the name glob may carry a ``/`` to match relative
paths; and the walker honours a workspace ``.gitignore`` (plain and wildcard
patterns for files and directories; negations are skipped) so generated and
ignored trees are not scanned or listed.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .base import ToolResult, register
from .filesystem import _INDEX_SKIP

if TYPE_CHECKING:
    from .permissions import PermissionManager

_MAX_MATCHES = 100
_MAX_FILE_BYTES = 1024 * 1024  # skip anything bigger — likely binary/asset


def _truthy(value: Any) -> bool:
    """Normalize a possibly-string boolean argument (models send "true")."""
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _load_ignore(root: Path) -> list[tuple[str, bool]]:
    """ ``(pattern, matches_dirs)`` rules from a workspace ``.gitignore``.

    Conservative subset of real gitignore semantics, meant to *reduce noise*,
    never to hide source the agent needs: comments and negations (``!``) are
    skipped, and a rule matches either a file/dir name (``build``) anywhere in
    the workspace or a relative path (``docs/generated``). A matching directory
    is not entered, so its contents are ignored transitively.
    """
    ignore_file = root / ".gitignore"
    try:
        text = ignore_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    rules: list[tuple[str, bool]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        dir_only = line.endswith("/")
        pattern = line.rstrip("/")
        if pattern:
            rules.append((pattern, dir_only))
    return rules


def _ignored(rel_posix: str, is_dir: bool, rules: list[tuple[str, bool]]) -> bool:
    """Whether ``rel_posix`` is ignored by the (conservative) rule subset."""
    parts = rel_posix.split("/")
    for pattern, dirs_only in rules:
        if dirs_only and not is_dir:
            # A directory-only rule still prunes everything beneath it.
            if any(fnmatch.fnmatch(part, pattern) for part in parts[:-1]):
                return True
            continue
        if any(fnmatch.fnmatch(part, pattern) for part in parts[:-1]):
            return True  # an ignoring ancestor prunes the whole subtree
        if is_dir:
            if fnmatch.fnmatch(parts[-1], pattern):
                return True
            continue
        if fnmatch.fnmatch(rel_posix, pattern) or fnmatch.fnmatch(
            parts[-1], pattern
        ):
            return True
    return False


def _iter_files(root: Path):
    """Workspace files, skipping caches/VCS dirs, .gitignore rules and hidden entries."""
    rules = _load_ignore(root)
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            continue
        for entry in entries:
            if entry.name in _INDEX_SKIP or entry.name.startswith("."):
                continue
            entry_is_dir = entry.is_dir()
            try:
                rel = entry.relative_to(root).as_posix()
            except ValueError:  # pragma: no cover - root always contains entry
                continue
            if rules and _ignored(rel, entry_is_dir, rules):
                continue
            if entry_is_dir:
                stack.append(entry)
            elif entry.is_file():
                yield entry


def _glob_to_regex(pattern: str) -> "re.Pattern[str]":
    """Compile a path glob ('src/**/*.py') to a regex over posix paths.

    ``**/`` matches any directory depth (including none), ``**`` any run of
    characters, ``*`` within one segment, ``?`` one character.
    (``PurePath.full_match`` needs Python 3.13; this supports 3.10+.)
    """
    out: list[str] = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if pattern.startswith("**/", i):
            out.append(r"(?:[^/]+/)*")
            i += 3
        elif pattern.startswith("**", i):
            out.append(r".*")
            i += 2
        elif ch == "*":
            out.append(r"[^/]*")
            i += 1
        elif ch == "?":
            out.append(r"[^/]")
            i += 1
        else:
            out.append(re.escape(ch))
            i += 1
    return re.compile("".join(out) + r"\Z")


@register(
    "find_files",
    "Find files by glob: a name pattern ('*.py') or a path pattern "
    "('src/**/*.py', relative to the workspace).",
    {"pattern": "glob matched against file names, or paths when it contains '/'"},
    mutates=False,
)
def _find_files(perm: "PermissionManager", args: dict[str, Any]) -> ToolResult:
    pattern = str(args["pattern"]).replace("\\", "/")
    path_mode = "/" in pattern
    path_regex = _glob_to_regex(pattern) if path_mode else None

    matches = []
    for path in _iter_files(perm.workspace):
        if path_mode:
            rel = path.relative_to(perm.workspace).as_posix()
            matched = path_regex.match(rel) is not None
        else:
            matched = fnmatch.fnmatch(path.name, pattern)
        if matched:
            matches.append(str(path.relative_to(perm.workspace)))
            if len(matches) >= _MAX_MATCHES:
                matches.append(f"... (capped at {_MAX_MATCHES})")
                break
    if not matches:
        return ToolResult(True, f"No files matching '{pattern}'.")
    return ToolResult(True, "\n".join(matches))


@register(
    "search_text",
    "Search file contents for a regex; returns file:line matches. The glob "
    "filter matches file names, or relative paths when it contains '/'.",
    {
        "pattern": "regular expression",
        "glob": "(optional) only search files whose name (or relative path, "
        "when the glob contains '/') matches this glob",
        "ignore_case": "(optional) true to match case-insensitively",
        "exclude": "(optional) second glob of paths to skip (matches names, "
        "or relative paths when it contains '/')",
    },
    mutates=False,
    types={"ignore_case": "boolean"},
)
def _search_text(perm: "PermissionManager", args: dict[str, Any]) -> ToolResult:
    try:
        flags = 0
        if _truthy(args.get("ignore_case")):
            flags = re.IGNORECASE
        regex = re.compile(str(args["pattern"]), flags)
    except re.error as exc:
        return ToolResult(False, f"Invalid regex: {exc}")
    name_glob = str(args.get("glob") or "*")
    exclude_glob = str(args.get("exclude") or "")

    def _matches(candidate: str, pattern: str) -> bool:
        if "/" in pattern:
            # Path globs must be path-aware: "*" never crosses a separator,
            # so 'src/*.py' covers one level only — 'src/gen/x.py' needs
            # 'src/**/*.py'. fnmatch would let "*" eat the separators.
            return (
                _glob_to_regex(pattern).match(candidate.replace("\\", "/"))
                is not None
            )
        return fnmatch.fnmatch(candidate.split("/")[-1], pattern)

    hits: list[str] = []
    for path in _iter_files(perm.workspace):
        rel = path.relative_to(perm.workspace).as_posix()
        if not _matches(rel, name_glob):
            continue
        if exclude_glob and _matches(rel, exclude_glob):
            continue
        try:
            if path.stat().st_size > _MAX_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "\x00" in text[:1024]:  # binary sniff
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                hits.append(f"{rel}:{lineno}: {line.strip()[:200]}")
                if len(hits) >= _MAX_MATCHES:
                    hits.append(f"... (capped at {_MAX_MATCHES})")
                    return ToolResult(True, "\n".join(hits))
    if not hits:
        return ToolResult(True, f"No matches for '{args['pattern']}'.")
    return ToolResult(True, "\n".join(hits))
