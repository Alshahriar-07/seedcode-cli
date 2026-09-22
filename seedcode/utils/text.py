"""Unicode safety: lone surrogates must never break a session (v7.1.0).

Regression being fixed::

    ✖ Unexpected error: 'utf-8' codec can't encode characters in
      position 6159-6160: surrogates not allowed

A Python ``str`` may contain *surrogate code units* (U+D800-U+DFFF) that are
not valid Unicode scalar values. They arrive in practice from:

* model output — a streaming chunk that splits an emoji, or JSON holding a
  half ``\\ud83d`` escape without its low half;
* tool output — a command whose bytes were decoded with ``surrogateescape``;
* file content — a file written from a truncated surrogate pair;
* serialized structures — ``json.dumps`` happily emits a lone surrogate, and
  the next ``.encode("utf-8")`` (console, socket, file) raises.

The fix is a single, well-tested normalization used at every boundary:
:func:`strip_surrogates` repairs a genuine high+low pair back into its astral
character (so a split emoji is restored, not destroyed) and replaces any
remaining lone surrogate with U+FFFD. **All other Unicode is untouched** —
Bangla, Arabic, Chinese, Japanese, Cyrillic, emoji and combining marks pass
through byte-for-byte.

The helpers never raise: a session cannot be terminated by bad text.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = [
    "SURROGATE_RE",
    "contains_surrogates",
    "safe_encode",
    "safe_text",
    "strip_surrogates",
]

# Every surrogate code unit (high and low). Python's UTF-8 encoder rejects all
# of them, which is exactly why they must be handled explicitly.
SURROGATE_RE = re.compile("[\ud800-\udfff]")

_HIGH_LO = 0xD800
_HIGH_HI = 0xDBFF
_LOW_LO = 0xDC00
_LOW_HI = 0xDFFF


def contains_surrogates(text: Any) -> bool:
    """Whether ``text`` holds any surrogate code unit."""
    if not isinstance(text, str) or not text:
        return False
    return SURROGATE_RE.search(text) is not None


def strip_surrogates(text: str) -> str:
    """Return ``text`` with surrogates repaired or replaced; never raises.

    * a valid high+low pair becomes the astral character it encodes
      (``"\\ud83d\\ude00"`` -> ``"😀"``);
    * a lone or malformed surrogate becomes U+FFFD (``"�"``);
    * every other code point is returned unchanged.
    """
    if not isinstance(text, str) or not text:
        return "" if text is None else text  # type: ignore[return-value]
    if not SURROGATE_RE.search(text):
        return text  # fast path: the overwhelming majority of strings

    out: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        code = ord(text[index])
        if _HIGH_LO <= code <= _HIGH_HI and index + 1 < length:
            low = ord(text[index + 1])
            if _LOW_LO <= low <= _LOW_HI:
                combined = 0x10000 + ((code - _HIGH_LO) << 10) + (low - _LOW_LO)
                out.append(chr(combined))
                index += 2
                continue
        if _HIGH_LO <= code <= _LOW_HI:
            out.append("\ufffd")
            index += 1
            continue
        out.append(text[index])
        index += 1
    return "".join(out)


def safe_text(value: Any) -> str:
    """``str(value)`` with surrogates neutralised (for any input type)."""
    try:
        text = value if isinstance(value, str) else str(value)
    except Exception:
        return ""
    return strip_surrogates(text)


def safe_encode(text: Any, encoding: str = "utf-8") -> bytes:
    """Encode ``text`` safely: surrogates are neutralised, never raised.

    ``errors="replace"`` is kept as a final belt-and-braces guard for text that
    a custom codec still refuses.
    """
    return safe_text(text).encode(encoding, "replace")
