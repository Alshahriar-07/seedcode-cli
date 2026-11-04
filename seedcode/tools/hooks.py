"""Tool-lifecycle hooks and user plugin loading.

OpenCode reference: ``packages/plugin`` exposes lifecycle effects (``tool``,
``permission``, ``command``, …) so third-party code can observe and veto
agent actions. Reimplemented for SeedCode as a small, dependency-free hook
manager wired at the agent loop's single tool-execution choke point
(``AgentEngine._execute_one``) plus a plugin loader that imports user Python
modules, which register tools with the existing :func:`seedcode.tools.base.register`
decorator and hooks with :class:`HookManager` here.

Plugins live in ``<workspace>/.seedcode/plugins/*.py`` (project level) and
``<app_dir>/plugins/*.py`` (user level). Loading is contained: one broken
plugin is logged and skipped, never fatal, and a broken plugin can never
silently disable the agent's tools — a hook that raises is treated the same
as one that vetoes (the tool is blocked with a clear reason fed back to the
model), so failures stay honest instead of being swallowed.

Security note (documented, deliberate): plugins are code the USER chose to
put in their project or profile, executed with the session's own permissions.
The permission system — not this loader — is the gate on what the resulting
tools may do.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..utils.logger import get_logger
from ..utils.text import safe_text

_log = get_logger("hooks")

#:(before_tool listener) → ``True`` to allow, ``False`` (or an exception) to
#: veto the call. A veto error string is produced by the manager.
BeforeToolHook = Callable[[str, dict[str, Any]], bool]
#: (after_tool listener) → observation only; return value ignored.
AfterToolHook = Callable[[str, dict[str, Any], "ToolResultLike"], None]

#: Structural stand-in for a finished tool result, forwarded to listeners.
@dataclass(frozen=True, slots=True)
class ToolHookResult:
    ok: bool
    output: str
    diff: str = ""
    exit_code: int | None = None


_HookResult = ToolHookResult


@dataclass(slots=True)
class HookManager:
    """Session-wide before/after interception for tool execution."""

    before: list[BeforeToolHook] = field(default_factory=list)
    after: list[AfterToolHook] = field(default_factory=list)

    def add_before(self, listener: BeforeToolHook) -> Callable[[], None]:
        """Register a pre-execution listener; returns its unsubscribe."""
        self.before.append(listener)

        def remove() -> None:
            if listener in self.before:
                self.before.remove(listener)

        return remove

    def add_after(self, listener: AfterToolHook) -> Callable[[], None]:
        """Register a post-execution listener; returns its unsubscribe."""
        self.after.append(listener)

        def remove() -> None:
            if listener in self.after:
                self.after.remove(listener)

        return remove

    def run_before(self, tool: str, args: dict[str, Any]) -> str:
        """Run every before-hook. Returns a veto reason, or "" when allowed.

        A listener vetoes ONLY by returning ``False`` (a raised exception is
        treated the same — a broken hook must never become a silently
        allowed call). Any other return value, including ``None``, allows:
        an observer that forgets to ``return True`` must not block the whole
        agent. The veto reason is fed back to the model instead of being
        swallowed.
        """
        for listener in list(self.before):
            try:
                allowed = listener(tool, args)
            except Exception as exc:
                _log.warning("before_tool hook %r raised: %s", listener, exc)
                allowed = False
            if allowed is False:
                return safe_text(f"blocked by a before_tool hook ({listener!r})")
        return ""

    def run_after(self, tool: str, args: dict[str, Any], result: _HookResult) -> None:
        """Run every after-hook; listener failures are contained and logged."""
        for listener in list(self.after):
            try:
                listener(tool, args, result)
            except Exception as exc:
                _log.warning("after_tool hook %r raised: %s", listener, exc)
        return None

    def reset(self) -> None:
        """Drop every listener (used by tests and engine rebuilds)."""
        self.before.clear()
        self.after.clear()


#: The process-wide hook manager. The agent engine consults it for every
#: tool call; commands, tests and plugins attach listeners to it.
MANAGER = HookManager()


# ---------------------------------------------------------------------------
# plugin loading
# ---------------------------------------------------------------------------
def _plugin_files(workspace: Path | None) -> list[tuple[Path, str]]:
    """User plugin files, lowest precedence first."""
    files: list[tuple[Path, str]] = []
    try:
        from ..utils.helpers import app_dir

        user_dir = Path(app_dir()) / "plugins"
        if user_dir.is_dir():
            files.extend((p, "user") for p in sorted(user_dir.glob("*.py")))
    except OSError:
        pass  # an unwritable home must not break plugin loading
    if workspace is None:
        try:
            from ..codemode_state import codemode_state

            workspace = getattr(codemode_state(), "workspace", None)
        except Exception:
            workspace = None
    if workspace is not None:
        project_dir = Path(workspace) / ".seedcode" / "plugins"
        if project_dir.is_dir():
            files.extend((p, "project") for p in sorted(project_dir.glob("*.py")))
    return files


def load_plugin(path: Path) -> bool:
    """Import one Python plugin module; returns True when it loaded.

    Registers happen inside the plugin via ``seedcode.tools.register`` or the
    hook manager — the loader only imports the module. A module that fails to
    import is skipped with a logged traceback and the other plugins still
    load.
    """
    module_name = f"seedcode_plugin_{path.stem}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, str(path))
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load module spec for {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    except Exception:
        _log.exception("plugin %s failed to load — skipped", path)
        return False
    _log.info("loaded plugin %s", path)
    return True


def load_plugins(workspace: Path | None = None) -> list[str]:
    """Load every user plugin for the workspace (returns loaded file paths)."""
    loaded: list[str] = []
    for path, _source in _plugin_files(workspace):
        if load_plugin(path):
            loaded.append(str(path))
    return loaded
