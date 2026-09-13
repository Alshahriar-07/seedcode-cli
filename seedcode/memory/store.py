"""Persistent local memory: indexed JSON records under ``~/.seedcode/memory``.

Design rules the tests pin:

* **Structured records, not transcripts.** Each record is a small JSON file
  (``<namespace>/<name>.json``) plus a per-namespace ``index.json`` holding
  id/summary/tags/timestamps. Retrieval reads the *index*, then loads only
  the referenced records — never the whole directory.
* **Namespaces.** ``sessions`` (task records), ``desktop`` (apps, monitors,
  environment), ``user`` (operational preferences), ``web`` (extracted
  pages), ``files`` (downloaded/extracted artifacts).
* **Secret filtering.** Keys matching secret-ish names (password, token,
  api_key, cookie, secret...) are rejected at write time with
  :class:`SecurityError`; redaction masks values in anything that slips
  through nested structures. Secrets belong in a credential manager, never
  in memory JSON.
* **Local-first.** Everything lives under the per-user app dir; nothing
  uploads. Corruption is contained: a bad record/index degrades to empty
  for that namespace without touching the others.

Storage is atomic (write-to-temp + replace) so a crash never leaves a
half-written record behind.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.errors import SecurityError
from ..utils.helpers import app_dir

NAMESPACES = ("sessions", "desktop", "user", "web", "files")

# Keys that must never be persisted into normal memory.
_SECRET_KEY_RE = re.compile(
    r"(password|passwd|pwd|secret|token|api[_-]?key|apikey|credential|"
    r"auth|cookie|session[_-]?id|private[_-]?key|access[_-]?key)",
    re.IGNORECASE,
)


def mask_secret(value: Any) -> str:
    """Mask a secret-ish value for logs/echoes: keep 2 leading chars."""
    text = str(value)
    if len(text) <= 4:
        return "*" * len(text)
    return text[:2] + "*" * (len(text) - 2)


@dataclass(slots=True)
class MemoryRecord:
    """One retrievable memory entry (metadata lives in the index)."""

    id: str
    namespace: str
    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)


def _assert_no_secrets(data: Any, path: str = "") -> None:
    """Reject nested secret-looking keys before anything touches disk."""
    if isinstance(data, dict):
        for key, value in data.items():
            here = f"{path}.{key}" if path else str(key)
            if _SECRET_KEY_RE.search(str(key)):
                raise SecurityError(
                    f"Refusing to store a secret-looking field ('{here}') in "
                    "memory. Credentials belong in a credential manager."
                )
            _assert_no_secrets(value, here)
    elif isinstance(data, list):
        for i, item in enumerate(data):
            _assert_no_secrets(item, f"{path}[{i}]")


class MemoryStore:
    """Namespaced, indexed, atomic JSON record store."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root or (app_dir() / "memory")
        try:
            self._root.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass  # writes will fail later and are handled per-call

    # --- paths ---------------------------------------------------------------
    def _ns_dir(self, namespace: str) -> Path:
        ns = namespace.strip().lower()
        if ns not in NAMESPACES:
            raise ValueError(
                f"Unknown memory namespace '{namespace}'. Choose one of: "
                f"{', '.join(NAMESPACES)}."
            )
        return self._root / ns

    def _index_path(self, namespace: str) -> Path:
        return self._ns_dir(namespace) / "index.json"

    def _record_path(self, namespace: str, record_id: str) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9._-]", "_", record_id)[:80] or "record"
        return self._ns_dir(namespace) / f"{safe}.json"

    # --- atomic IO -------------------------------------------------------------
    @staticmethod
    def _write_atomic(path: Path, payload: dict[str, Any]) -> bool:
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

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any] | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None  # corrupt or missing -> treated as empty

    # --- index -----------------------------------------------------------------
    def _load_index(self, namespace: str) -> dict[str, Any]:
        data = self._read_json(self._index_path(namespace))
        if data is None:
            return {"records": {}}
        records = data.get("records")
        return {"records": records} if isinstance(records, dict) else {"records": {}}

    def _save_index(self, namespace: str, index: dict[str, Any]) -> bool:
        return self._write_atomic(self._index_path(namespace), index)

    # --- public API ----------------------------------------------------------------
    def put(
        self,
        namespace: str,
        record_id: str,
        summary: str,
        data: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> bool:
        """Store a record + index entry (secret-checked). False on IO failure."""
        _assert_no_secrets(data or {})
        _assert_no_secrets({"summary": summary})
        record = {
            "id": record_id,
            "namespace": namespace,
            "summary": summary,
            "tags": list(tags or []),
            "created_at": time.time(),
            "data": data or {},
        }
        if not self._write_atomic(self._record_path(namespace, record_id), record):
            return False
        index = self._load_index(namespace)
        index["records"][record_id] = {
            "summary": summary,
            "tags": list(tags or []),
            "created_at": record["created_at"],
        }
        return self._save_index(namespace, index)

    def get(self, namespace: str, record_id: str) -> MemoryRecord | None:
        """Load one record by id (None when missing/corrupt)."""
        raw = self._read_json(self._record_path(namespace, record_id))
        if raw is None or not isinstance(raw.get("id"), str):
            return None
        return MemoryRecord(
            id=raw["id"],
            namespace=raw.get("namespace", namespace),
            summary=raw.get("summary", ""),
            data=raw.get("data", {}) if isinstance(raw.get("data"), dict) else {},
            tags=raw.get("tags", []) if isinstance(raw.get("tags"), list) else [],
            created_at=float(raw.get("created_at", 0.0)),
        )

    def delete(self, namespace: str, record_id: str) -> bool:
        """Remove a record and its index entry."""
        try:
            self._record_path(namespace, record_id).unlink(missing_ok=True)
        except OSError:
            return False
        index = self._load_index(namespace)
        index["records"].pop(record_id, None)
        return self._save_index(namespace, index)

    def query(
        self, namespace: str, *, text: str = "", tags: list[str] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Index-level search (no record loads): newest first, filtered.

        Matches on summary + tags + id. Returns index entries
        ``{id, summary, tags, created_at}`` — callers load full records
        selectively via :meth:`get`.
        """
        index = self._load_index(namespace)
        wanted_tags = {t.lower() for t in (tags or [])}
        needle = (text or "").strip().lower()
        hits: list[dict[str, Any]] = []
        for record_id, entry in index["records"].items():
            if not isinstance(entry, dict):
                continue
            entry_tags = [str(t).lower() for t in entry.get("tags", [])]
            if wanted_tags and not wanted_tags.issubset(set(entry_tags)):
                continue
            haystack = " ".join([
                record_id, str(entry.get("summary", "")), " ".join(entry_tags),
            ]).lower()
            if needle and needle not in haystack:
                continue
            hits.append({
                "id": record_id,
                "summary": entry.get("summary", ""),
                "tags": entry.get("tags", []),
                "created_at": entry.get("created_at", 0.0),
            })
        hits.sort(key=lambda h: h["created_at"], reverse=True)
        return hits[: max(1, limit)]

    def namespaces(self) -> list[str]:
        """Namespaces that exist on disk (of the known set)."""
        return [ns for ns in NAMESPACES if self._ns_dir(ns).is_dir()]


# --- process-wide store ---------------------------------------------------------------
_STORE: MemoryStore | None = None


def memory_store(root: Path | None = None) -> MemoryStore:
    """The process-wide :class:`MemoryStore` (root injectable for tests)."""
    global _STORE
    if _STORE is None or root is not None:
        if _STORE is None:
            _STORE = MemoryStore(root)
    return _STORE


def reset_memory_store() -> None:
    global _STORE
    _STORE = None


__all__ = [
    "NAMESPACES", "MemoryRecord", "MemoryStore", "mask_secret",
    "memory_store", "reset_memory_store",
]
