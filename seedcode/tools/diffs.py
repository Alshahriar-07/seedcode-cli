"""Unified diffs for real file changes made by tools (v9.2.0).

The activity backend must be able to report what actually changed on disk.
Tools capture the content before and after a mutation and call
:func:`make_diff`; the resulting unified diff (with standard ``---``/``+++``
headers and line numbers) is attached to the tool result and streamed to the
UI. Nothing here invents a line number: the diff is produced by
:mod:`difflib` from the two real texts.

New files use ``/dev/null`` as the "before" side and deleted files as the
"after" side, exactly like ``git diff`` for additions/deletions. Binary or
unreadable content is never diffed: callers pass decoded text and the helper
caps very large diffs.
"""

from __future__ import annotations

import difflib
import re

#: Diffs beyond this size are truncated before they reach the UI/event stream.
MAX_DIFF_CHARS = 20_000


def _lines(text: str) -> list[str]:
    """Split text into diffable lines, normalizing line endings."""
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    return normalized.splitlines(keepends=True)


def make_diff(
    path: object,
    before: str,
    after: str,
    *,
    created: bool = False,
    deleted: bool = False,
    context: int = 3,
) -> str:
    """A unified diff between ``before`` and ``after`` (empty when unchanged).

    ``created``/``deleted`` select the ``/dev/null`` side, so an added file
    shows every line as an addition and a removed file every line as a
    deletion. The output is bounded by :data:`MAX_DIFF_CHARS`.
    """
    before_lines = _lines(before)
    after_lines = _lines(after)
    if before_lines == after_lines and not created and not deleted:
        return ""
    label = str(path)
    diff = "".join(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile="/dev/null" if created else label,
            tofile="/dev/null" if deleted else label,
            n=max(0, int(context)),
        )
    )
    if len(diff) > MAX_DIFF_CHARS:
        omitted = len(diff) - MAX_DIFF_CHARS
        diff = diff[:MAX_DIFF_CHARS] + f"\n... [diff truncated, {omitted} chars omitted]\n"
    return diff


def diff_stats(diff: str) -> tuple[int, int]:
    """``(lines_added, lines_removed)`` counted from a unified diff's hunks."""
    added = removed = 0
    for line in (diff or "").splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return added, removed


_HUNK_RE = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.MULTILINE
)


def diff_details(diff: str) -> dict:
    """Structured facts about a unified diff, with real line numbers.

    Returns ``added``/``removed`` counts, whether the file was created or
    deleted, the first and last line of the new file touched by the change,
    and every hunk's old/new start and length. The numbers come from the diff
    headers themselves, so the UI can never invent a line range.
    """
    text = diff or ""
    added, removed = diff_stats(text)
    hunks: list[dict] = []
    starts: list[int] = []
    ends: list[int] = []
    for match in _HUNK_RE.finditer(text):
        old_start = int(match.group(1))
        old_count = int(match.group(2) or 1)
        new_start = int(match.group(3))
        new_count = int(match.group(4) or 1)
        hunks.append(
            {
                "old_start": old_start,
                "old_count": old_count,
                "new_start": new_start,
                "new_count": new_count,
            }
        )
        starts.append(new_start)
        ends.append(max(new_start, new_start + max(new_count, 1) - 1))
    created = any(
        line.strip() == "--- /dev/null" for line in text.splitlines()[:3]
    )
    deleted = any(
        line.strip() == "+++ /dev/null" for line in text.splitlines()[:3]
    )
    return {
        "added": added,
        "removed": removed,
        "created": created,
        "deleted": deleted,
        "line": min(starts) if starts else 0,
        "line_end": max(ends) if ends else 0,
        "hunks": hunks,
    }
