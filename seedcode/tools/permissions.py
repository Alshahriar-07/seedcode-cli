"""Permission system for agent mode.

Three modes decide what the agent may do, checked before every tool run:

* ``read_only``   — inspect only: no writes, no commands, no git mutations.
* ``workspace``   — read anywhere, but mutate only inside the workspace
                    directory (the directory Seed Code was started in).
* ``full_access`` — no path restriction on mutations (commands still run
                    with the workspace as their cwd).

The manager is the single gate: tools never check paths or modes themselves,
they ask :meth:`PermissionManager.check_read` / :meth:`check_write` /
:meth:`check_execute` and let the raised error flow back to the model.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path


class PermissionError_(Exception):
    """An action was blocked by the current permission mode.

    Named with a trailing underscore to avoid shadowing the builtin
    ``PermissionError`` (raised by the OS for filesystem denials).
    """


class PermissionMode(str, Enum):
    READ_ONLY = "read_only"
    WORKSPACE = "workspace"
    FULL_ACCESS = "full_access"

    @classmethod
    def parse(cls, value: str) -> "PermissionMode":
        raw = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "read": cls.READ_ONLY,
            "readonly": cls.READ_ONLY,
            "read_only": cls.READ_ONLY,
            "ws": cls.WORKSPACE,
            "workspace": cls.WORKSPACE,
            "full": cls.FULL_ACCESS,
            "full_access": cls.FULL_ACCESS,
            "fullaccess": cls.FULL_ACCESS,
        }
        mode = aliases.get(raw)
        if mode is None:
            raise ValueError(
                f"Unknown permission mode '{value}'. "
                "Choose read_only, workspace, or full_access."
            )
        return mode

    @property
    def label(self) -> str:
        return {
            PermissionMode.READ_ONLY: "Read Only",
            PermissionMode.WORKSPACE: "Workspace",
            PermissionMode.FULL_ACCESS: "Full Access",
        }[self]


class PermissionManager:
    """Gatekeeper consulted by every tool before acting."""

    def __init__(
        self, workspace: Path | None = None, mode: PermissionMode = PermissionMode.WORKSPACE
    ) -> None:
        self.workspace = (workspace or Path.cwd()).resolve()
        self.mode = mode

    # --- path helpers --------------------------------------------------------
    def resolve(self, raw: str) -> Path:
        """Resolve a tool-supplied path (relative paths anchor at the workspace)."""
        path = Path(str(raw)).expanduser()
        if not path.is_absolute():
            path = self.workspace / path
        return path.resolve()

    def in_workspace(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self.workspace)
            return True
        except ValueError:
            return False

    # --- checks (raise PermissionError_ when blocked) -------------------------
    def check_read(self, path: Path) -> None:
        """Reading is allowed in every mode."""

    def check_write(self, path: Path) -> None:
        if self.mode is PermissionMode.READ_ONLY:
            raise PermissionError_(
                "Blocked: permission mode is Read Only — no file changes allowed. "
                "The user can raise it with /permission."
            )
        if self.mode is PermissionMode.WORKSPACE and not self.in_workspace(path):
            raise PermissionError_(
                f"Blocked: '{path}' is outside the workspace ({self.workspace}). "
                "Workspace mode only allows changes inside it — "
                "the user can raise it with /permission full_access."
            )

    def check_execute(self, description: str = "") -> None:
        """Commands and git mutations need more than Read Only."""
        if self.mode is PermissionMode.READ_ONLY:
            what = f" ({description})" if description else ""
            raise PermissionError_(
                f"Blocked: permission mode is Read Only — cannot execute{what}. "
                "The user can raise it with /permission."
            )
