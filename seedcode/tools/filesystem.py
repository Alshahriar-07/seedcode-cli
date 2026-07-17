"""Filesystem tools: read, write, list, delete — plus the project indexer.

Multi-file editing is simply several ``write_file``/``edit_file`` calls in one
agent turn; each one passes the same permission gate. The indexer produces a
compact tree of the workspace so the model can orient itself without reading
every file.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .base import ToolResult, register

if TYPE_CHECKING:
    from .permissions import PermissionManager

# Read caps: a tool call must never dump a huge binary/log into the context.
_MAX_READ_BYTES = 256 * 1024
_MAX_INDEX_ENTRIES = 400

# Directories that never belong in a project index.
_INDEX_SKIP = {
    ".git", "__pycache__", ".pytest_cache", "node_modules", ".venv", "venv",
    "dist", "build", ".mypy_cache", ".ruff_cache", ".idea", ".vscode",
}


@register(
    "read_file",
    "Read a text file (optionally a line range).",
    {
        "path": "file path",
        "start_line": "(optional) first line, 1-based",
        "end_line": "(optional) last line, inclusive",
    },
    mutates=False,
)
def _read_file(perm: "PermissionManager", args: dict[str, Any]) -> ToolResult:
    path = perm.resolve(args["path"])
    perm.check_read(path)
    if not path.is_file():
        return ToolResult(False, f"File not found: {path}")
    if path.stat().st_size > _MAX_READ_BYTES and "start_line" not in args:
        return ToolResult(
            False,
            f"File is large ({path.stat().st_size} bytes). "
            "Read it in ranges with start_line/end_line.",
        )
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return ToolResult(False, f"Could not read {path}: {exc}")

    lines = text.splitlines()
    start = max(1, int(args.get("start_line", 1)))
    end = min(len(lines), int(args.get("end_line", len(lines))))
    numbered = [f"{i}\t{lines[i - 1]}" for i in range(start, end + 1)]
    header = f"{path} ({len(lines)} lines, showing {start}-{end})"
    return ToolResult(True, header + "\n" + "\n".join(numbered))


@register(
    "write_file",
    "Create or overwrite a file with the given content.",
    {"path": "file path", "content": "full new file content"},
    mutates=True,
)
def _write_file(perm: "PermissionManager", args: dict[str, Any]) -> ToolResult:
    path = perm.resolve(args["path"])
    perm.check_write(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(args["content"]), encoding="utf-8")
    except OSError as exc:
        return ToolResult(False, f"Could not write {path}: {exc}")
    return ToolResult(True, f"Wrote {len(str(args['content']))} chars to {path}")


@register(
    "delete_file",
    "Delete a single file (never a directory).",
    {"path": "file path"},
    mutates=True,
)
def _delete_file(perm: "PermissionManager", args: dict[str, Any]) -> ToolResult:
    path = perm.resolve(args["path"])
    perm.check_write(path)
    if path.is_dir():
        return ToolResult(False, f"Refusing to delete a directory: {path}")
    if not path.exists():
        return ToolResult(False, f"File not found: {path}")
    try:
        path.unlink()
    except OSError as exc:
        return ToolResult(False, f"Could not delete {path}: {exc}")
    return ToolResult(True, f"Deleted {path}")


@register(
    "list_dir",
    "List the entries of a directory.",
    {"path": "(optional) directory path, defaults to the workspace root"},
    mutates=False,
)
def _list_dir(perm: "PermissionManager", args: dict[str, Any]) -> ToolResult:
    path = perm.resolve(args.get("path") or ".")
    perm.check_read(path)
    if not path.is_dir():
        return ToolResult(False, f"Not a directory: {path}")
    try:
        entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except OSError as exc:
        return ToolResult(False, f"Could not list {path}: {exc}")
    lines = [f"{'d' if e.is_dir() else 'f'}  {e.name}" for e in entries]
    return ToolResult(True, f"{path}\n" + ("\n".join(lines) or "(empty)"))


def _walk_index(root: Path, prefix: str, lines: list[str]) -> None:
    """Depth-first tree walk, capped at _MAX_INDEX_ENTRIES lines."""
    if len(lines) >= _MAX_INDEX_ENTRIES:
        return
    try:
        entries = sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except OSError:
        return
    for entry in entries:
        if len(lines) >= _MAX_INDEX_ENTRIES:
            lines.append(f"{prefix}... (index capped at {_MAX_INDEX_ENTRIES} entries)")
            return
        if entry.name in _INDEX_SKIP or entry.name.startswith("."):
            continue
        if entry.is_dir():
            lines.append(f"{prefix}{entry.name}/")
            _walk_index(entry, prefix + "  ", lines)
        else:
            try:
                size = entry.stat().st_size
            except OSError:
                size = 0
            lines.append(f"{prefix}{entry.name}  ({size} B)")


def build_index(perm: "PermissionManager") -> str:
    """Compact workspace tree (shared by the tool and the /index command)."""
    lines: list[str] = [f"{perm.workspace}/"]
    _walk_index(perm.workspace, "  ", lines)
    return "\n".join(lines)


@register(
    "project_index",
    "Get a compact tree of the whole workspace (files, sizes).",
    {},
    mutates=False,
)
def _project_index(perm: "PermissionManager", args: dict[str, Any]) -> ToolResult:
    return ToolResult(True, build_index(perm))
