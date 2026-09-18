"""Code Mode state (v6.2.0): workspace-aware coding agent session state.

Code Mode is Assist Mode sharpened for the current project:

* the CWD at ``/codemode on`` becomes the *workspace* root;
* ``.seedcode/`` project memory is ensured and the index refreshed
  incrementally;
* the agent's system prompt gains workspace/index context (it consults the
  index instead of reading the whole repository);
* ``/codemode off`` restores the previous mode and keeps the memory on disk.

State is process-local (a session toggle), persisted nowhere except the
``.seedcode`` directory itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .codemode import SeedcodeStore


@dataclass(slots=True)
class CodeModeState:
    """One Code Mode session."""

    enabled: bool = False
    workspace: Path | None = None
    store: SeedcodeStore | None = None
    # Result of the last refresh_index call ({indexed, unchanged}).
    last_index: dict[str, int] = field(default_factory=dict)

    def status_lines(self) -> list[str]:
        """The /codemode status panel rows."""
        if not self.enabled or self.workspace is None:
            return ["Code Mode: OFF"]
        store = self.store
        memories = len(store.list_memories()) if store else 0
        indexed = len(store.load_file_map()) if store else 0
        return [
            "Code Mode: ON",
            f"Workspace: {self.workspace}",
            f"Memory: {'Ready' if store and store.exists else 'Unavailable'}"
            f" ({memories} notes)",
            f"Index: Ready ({indexed} files)",
        ]


_STATE = CodeModeState()


def codemode_state() -> CodeModeState:
    """The process-wide Code Mode session state."""
    return _STATE


def enable(workspace: Path) -> CodeModeState:
    """Enable Code Mode for ``workspace``: ensure .seedcode + refresh index."""
    _STATE.workspace = workspace.resolve()
    _STATE.store = SeedcodeStore(_STATE.workspace)
    _STATE.store.ensure()
    try:
        _STATE.last_index = _STATE.store.refresh_index()
    except OSError:
        _STATE.last_index = {"indexed": 0, "unchanged": 0}
    _STATE.enabled = True
    return _STATE


def disable() -> CodeModeState:
    """Disable Code Mode (memory stays on disk for future sessions)."""
    _STATE.enabled = False
    return _STATE


def reset() -> None:
    """Test isolation hook."""
    global _STATE
    _STATE = CodeModeState()


__all__ = [
    "CodeModeState",
    "codemode_state",
    "enable",
    "disable",
    "reset",
]
