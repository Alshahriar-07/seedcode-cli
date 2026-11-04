"""Patch tools: precise in-place edits by exact text matching.

``edit_file`` is the agent's scalpel — replace one exact occurrence of a
string — and ``insert_in_file`` adds a block before/after an exact anchor;
``write_file`` (filesystem) is the sledgehammer. Multi-file editing is
several edit/write calls in one turn; each passes the same permission gate
independently.

Matching happens on ``\\n``-normalized text (see :mod:`seedcode.tools.textio`)
and the original encoding/line endings are preserved on write, so editing a
CRLF or latin-1 file never silently converts it.

There is deliberately NO unified-diff applier here: model-generated diffs
malform often (line-number drift), which converts into retry loops that burn
the agent's step budget, while exact-match editing gives precise failure
messages the model can act on. edit_file + insert_in_file + write_file cover
every edit shape the loop needs.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from .base import ToolResult, register
from .diffs import make_diff
from .textio import read_text_file, write_text_file

if TYPE_CHECKING:
    from .permissions import PermissionManager


# --- model-text tolerance (OpenCode edit-tool parity) -----------------------
# Models often retype text with typographic characters that LOOK identical
# but differ byte-wise (curly quotes from a model's own quoting, en/em dashes
# in comments, NBSP in pasted strings), or with stray trailing whitespace on
# lines. An exact byte match then fails even though the edit is obviously
# correct. When the exact tier finds nothing, the fuzzy tier matches with the
# same equivalences OpenCode normalizes for: those quote/dash characters,
# unicode spaces, whitespace runs, and per-line trailing whitespace. The
# replacement is still exactly what the model gave; it is only the MATCH that
# becomes forgiving. Uniqueness enforcement is identical in both tiers.
# Equivalence groups: any character inside a group matches any other
# member of that group. Whitespace runs collapse to ``\\s+``.
_QUOTE_GROUPS = (
    "'‘’‚‛",
    "\"“”„‟",
    "‐‑‒–—―−-",
)


def _char_pattern(char: str) -> str:
    """The regex fragment that matches one source character forgivingly."""
    for group in _QUOTE_GROUPS:
        if char in group:
            return "[" + group + "]"
    if char.isspace():
        return "\\s+"
    return re.escape(char)


def fuzzy_pattern(old: str) -> re.Pattern[str] | None:
    """Compile ``old`` into a tolerance regex for the fuzzy edit tier.

    Each line becomes: quote/dash equivalence classes for lookalikes,
    ``\\s+`` for whitespace runs, and literals otherwise; lines join on
    ``[ \\t]*\\n`` for per-line trailing whitespace. ``None`` for text
    with nothing left to match.
    """
    lines = old.replace("\\r\\n", "\n").split("\n")
    line_patterns: list[str] = []
    for line in lines:
        pattern_line = ""
        for char in line:
            fragment = _char_pattern(char)
            # Coalesce: consecutive whitespace chars share ONE \s+ quantifier
            # ("a  =" must not become \s+\s+, which demands two spaces).
            if fragment == "\\s+" and pattern_line.endswith("\\s+"):
                continue
            pattern_line += fragment
        line_patterns.append(pattern_line)
    # Trailing-whitespace tolerance BETWEEN and at the end of lines: the
    # match stays anchored to the snippet the model supplied.
    joined = "[ \t]*\n".join(line_patterns)
    # A whitespace-only snippet matches by coincidence anywhere; refuse it.
    if not joined or all(not p for p in line_patterns):
        return None
    try:
        return re.compile(joined)
    except re.error:  # pragma: no cover - pathological input only
        return None


@register(
    "edit_file",
    "Replace an exact text snippet in a file (must match exactly once; pass "
    "replace_all=true to replace every occurrence).",
    {
        "path": "file path",
        "old_text": "exact text currently in the file",
        "new_text": "replacement text",
        "replace_all": "(optional) true to replace every occurrence instead of exactly one",
    },
    mutates=True,
    types={"replace_all": "boolean"},
)
def _edit_file(perm: "PermissionManager", args: dict[str, Any]) -> ToolResult:
    path = perm.resolve(args["path"])
    perm.check_write(path)
    if not path.is_file():
        return ToolResult(False, f"File not found: {path}")

    old = str(args["old_text"]).replace("\r\n", "\n")
    new = str(args["new_text"]).replace("\r\n", "\n")
    if not old:
        return ToolResult(False, "old_text is empty — use write_file to create content.")
    if old == new:
        return ToolResult(False, "old_text and new_text are identical; nothing to do.")

    try:
        tf = read_text_file(path)
    except OSError as exc:
        return ToolResult(False, f"Could not read {path}: {exc}")

    count = tf.text.count(old)
    fuzzy = False
    if count == 0:
        # Fuzzy tier (OpenCode-style tolerance): match typographic lookalikes
        # and trailing whitespace, then behave exactly like the exact tier.
        pattern = fuzzy_pattern(old)
        if pattern is not None:
            try:
                matches = list(pattern.finditer(tf.text))
            except re.error:  # pragma: no cover
                matches = []
            if matches:
                count = len(matches)
                fuzzy = True
    if count == 0:
        hint = (
            " — a fuzzy match for lookalike characters also failed"
            if fuzzy_pattern(old) is not None
            else ""
        )
        return ToolResult(
            False,
            f"old_text was not found in {path}{hint}. Read the file again and "
            "copy the text exactly (whitespace matters).",
        )

    raw_all = args.get("replace_all")
    replace_all = raw_all is True or (
        isinstance(raw_all, str)
        and raw_all.strip().lower() in ("1", "true", "yes", "on")
    )
    if count > 1 and not replace_all:
        return ToolResult(
            False,
            f"old_text appears {count} times in {path}. Include more surrounding "
            "lines so it matches exactly once, or pass replace_all=true to "
            "replace every occurrence deliberately.",
        )

    before = tf.text
    if fuzzy:
        pattern = fuzzy_pattern(old)
        assert pattern is not None  # the fuzzy tier matched above
        # A lambda replacement is inserted literally (no backslash/\g
        # interpretation), so the model's new_text lands byte-exactly.
        tf.text = pattern.sub(lambda _m: new, tf.text, count=(0 if replace_all else 1))
    else:
        tf.text = tf.text.replace(old, new) if replace_all else tf.text.replace(old, new, 1)
    replacements = count if replace_all else 1
    expected = tf.text
    try:
        write_text_file(path, tf)
    except OSError as exc:
        return ToolResult(False, f"Could not write {path}: {exc}")

    # VERIFICATION: the file must read back as exactly what we wrote
    # (comparing full text — substring checks false-positive when new_text
    # contains old_text, e.g. "b = 2" -> "b = 20").
    try:
        updated = read_text_file(path).text
        if updated != expected:
            return ToolResult(
                False,
                f"Edit operation completed but verification failed: {path} does "
                "not match the edited content. The file may have been modified "
                "by another process."
            )
    except OSError as exc:
        return ToolResult(
            False,
            f"Edit completed but verification failed: could not read back {path}: {exc}"
        )

    return ToolResult(
        True,
        f"Edited {path} ({replacements} replacement"
        f"{'s' if replacements != 1 else ''}, verified).",
        diff=make_diff(path, before, expected),
    )


@register(
    "insert_in_file",
    "Insert a block of text before or after an exact anchor snippet "
    "(anchor must match exactly once).",
    {
        "path": "file path",
        "anchor": "exact existing text to anchor on",
        "position": "'before' or 'after'",
        "text": "block to insert",
    },
    mutates=True,
)
def _insert_in_file(perm: "PermissionManager", args: dict[str, Any]) -> ToolResult:
    path = perm.resolve(args["path"])
    perm.check_write(path)
    if not path.is_file():
        return ToolResult(False, f"File not found: {path}")

    anchor = str(args["anchor"]).replace("\r\n", "\n")
    block = str(args["text"]).replace("\r\n", "\n")
    position = str(args["position"]).strip().lower()
    if position not in ("before", "after"):
        return ToolResult(False, "position must be 'before' or 'after'.")
    if not anchor:
        return ToolResult(False, "anchor is empty — copy an exact snippet from the file.")
    if not block:
        return ToolResult(False, "text is empty; nothing to insert.")

    try:
        tf = read_text_file(path)
    except OSError as exc:
        return ToolResult(False, f"Could not read {path}: {exc}")

    count = tf.text.count(anchor)
    if count == 0:
        return ToolResult(
            False,
            f"anchor was not found in {path}. Read the file again and copy the "
            "text exactly (whitespace matters).",
        )
    if count > 1:
        return ToolResult(
            False,
            f"anchor appears {count} times in {path}. Include more surrounding "
            "lines so it matches exactly once.",
        )

    before = tf.text
    replacement = block + anchor if position == "before" else anchor + block
    tf.text = tf.text.replace(anchor, replacement, 1)
    try:
        write_text_file(path, tf)
    except OSError as exc:
        return ToolResult(False, f"Could not write {path}: {exc}")

    # VERIFICATION: Read back to confirm the block landed where intended
    try:
        updated = read_text_file(path).text
        if replacement not in updated:
            return ToolResult(
                False,
                f"Insert completed but verification failed: the inserted block was "
                f"not found next to the anchor in {path}.",
            )
    except OSError as exc:
        return ToolResult(
            False,
            f"Insert completed but verification failed: could not read back {path}: {exc}"
        )

    return ToolResult(
        True,
        f"Inserted {len(block)} chars {position} the anchor in {path} (verified).",
        diff=make_diff(path, before, tf.text),
    )
