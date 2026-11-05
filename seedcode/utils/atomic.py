"""Atomic JSON persistence helpers.

Persistent project state (``.seedcode/context.json``, session ``todo`` /
``progress`` / ``execution`` files) must never be left half-written: a crash,
a power loss or a cancelled process between two ``write()`` calls would leave a
file that no longer parses. Every write here goes to a sibling temporary file
and is moved into place with :func:`os.replace`, which is atomic on Windows and
POSIX alike, so a reader sees either the previous document or the new one.

A truncated or hand-edited file is a normal condition too: :func:`read_json`
returns ``None`` instead of raising, and the caller reconstructs from the
workspace.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

__all__ = ["read_json", "write_json_atomic"]

#: Temporary suffix. Deliberately not ``.json`` so a scanner never mistakes a
#: half-written file for a real document.
_TMP_SUFFIX = ".seedcode-tmp"


def read_json(path: Path) -> Any | None:
    """The parsed document, or ``None`` when missing/corrupt/not decodable."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def write_json_atomic(path: Path, data: Any) -> bool:
    """Write ``data`` as JSON so readers only ever see complete documents.

    Returns ``False`` when the directory or file cannot be written; callers
    treat persistence as best-effort and never fail a session over it.
    """
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + _TMP_SUFFIX)
        tmp.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, target)
        return True
    except (OSError, TypeError, ValueError):
        return False
