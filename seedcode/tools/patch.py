"""Patch tool: precise in-place edits by exact text replacement.

``edit_file`` is the agent's scalpel — replace one exact occurrence of a
string — while ``write_file`` (filesystem) is the sledgehammer. Multi-file
editing is several edit/write calls in one turn; each passes the same
permission gate independently.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import ToolResult, register

if TYPE_CHECKING:
    from .permissions import PermissionManager


@register(
    "edit_file",
    "Replace an exact text snippet in a file (must match exactly once).",
    {
        "path": "file path",
        "old_text": "exact text currently in the file",
        "new_text": "replacement text",
    },
    mutates=True,
)
def _edit_file(perm: "PermissionManager", args: dict[str, Any]) -> ToolResult:
    path = perm.resolve(args["path"])
    perm.check_write(path)
    if not path.is_file():
        return ToolResult(False, f"File not found: {path}")

    old = str(args["old_text"])
    new = str(args["new_text"])
    if not old:
        return ToolResult(False, "old_text is empty — use write_file to create content.")
    if old == new:
        return ToolResult(False, "old_text and new_text are identical; nothing to do.")

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return ToolResult(False, f"Could not read {path}: {exc}")

    count = text.count(old)
    if count == 0:
        return ToolResult(
            False,
            f"old_text was not found in {path}. Read the file again and copy the "
            "text exactly (whitespace matters).",
        )
    if count > 1:
        return ToolResult(
            False,
            f"old_text appears {count} times in {path}. Include more surrounding "
            "lines so it matches exactly once.",
        )

    try:
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
    except OSError as exc:
        return ToolResult(False, f"Could not write {path}: {exc}")
    return ToolResult(True, f"Edited {path} (1 replacement).")
