"""The canonical mode system (v8.1.0): exactly three user-facing modes.

Seed Code exposes **three** modes and nothing else:

* :attr:`Mode.CHAT`  — conversation: questions, explanations, brainstorming.
  It never runs project/system actions.
* :attr:`Mode.CODE`  — a real coding agent for the current workspace: inspect,
  plan, edit, run, verify, and keep working until the task is genuinely done.
* :attr:`Mode.AGENT` — general-purpose execution: multi-step tasks that use the
  available tools, verify what they did, and report what actually happened
  instead of pretending.

The old *Assist Mode* is no longer a separate mode: its behaviour is folded into
Agent Mode. ``"assist"`` is still accepted as an input alias (a stored legacy
value, a slash command) but it resolves to :attr:`Mode.AGENT`, so no fourth mode
can be created anywhere in the application.

This module is the single source of truth for mode parsing and labels. Every
surface (``/mode``, ``/chat``, ``/assist``, ``/agent``, ``/codemode``, the
dashboard, the status bar, the task header) resolves the mode through here so
they can never disagree.
"""

from __future__ import annotations

import enum

__all__ = [
    "Mode",
    "MODE_LABELS",
    "MODE_DESCRIPTIONS",
    "active_mode",
    "agentic",
    "mode_description",
    "mode_label",
    "mode_title",
    "parse_mode",
]


@enum.unique
class Mode(str, enum.Enum):
    """The three canonical runtime modes."""

    CHAT = "chat"
    CODE = "code"
    AGENT = "agent"


#: User-facing label for each mode (the only names the UI may show).
MODE_LABELS: dict[Mode, str] = {
    Mode.CHAT: "Chat Mode",
    Mode.CODE: "Code Mode",
    Mode.AGENT: "Agent Mode",
}

#: One-line responsibility statement for each mode.
MODE_DESCRIPTIONS: dict[Mode, str] = {
    Mode.CHAT: "Conversation — questions, explanations, brainstorming. No project actions.",
    Mode.CODE: "Coding agent — inspect, plan, edit, run and verify until the task is done.",
    Mode.AGENT: "General execution — multi-step tasks using the available tools, then verified.",
}

#: Legacy spellings accepted on input and mapped to a canonical mode.
_ALIASES: dict[str, Mode] = {
    "chat": Mode.CHAT,
    "code": Mode.CODE,
    "codemode": Mode.CODE,
    "code-mode": Mode.CODE,
    # Assist Mode no longer exists as a mode; it is Agent Mode now.
    "assist": Mode.AGENT,
    "agent": Mode.AGENT,
    "desktop": Mode.AGENT,
}


def parse_mode(value: object, default: Mode = Mode.CHAT) -> Mode:
    """Resolve any stored/typed mode value to a canonical :class:`Mode`.

    An empty or unrecognised value yields ``default`` (never an error), so a
    corrupt config or a typo can never create a fourth mode or crash startup.
    """
    if isinstance(value, Mode):
        return value
    text = str(value or "").strip().lower()
    if not text:
        return default
    return _ALIASES.get(text, default)


def mode_title(mode: Mode | str) -> str:
    """The user-facing label for a mode (``"Agent Mode"``)."""
    return MODE_LABELS[parse_mode(mode)]


def mode_description(mode: Mode | str) -> str:
    """The responsibility statement for a mode."""
    return MODE_DESCRIPTIONS[parse_mode(mode)]


def agentic(mode: Mode | str) -> bool:
    """Whether a mode may act on the project/system (Code or Agent)."""
    return parse_mode(mode) in (Mode.CODE, Mode.AGENT)


def active_mode(config: object) -> Mode:
    """The mode the session is really in, from live state (v8.1.0).

    Code Mode is a workspace-aware session: while the Code Mode workspace is
    enabled it wins, because the engine is operating as a coding agent. Beyond
    that the mode stored on the configuration decides. Nothing else is
    consulted, so there is no way to end up in an unnamed fourth mode.
    """
    try:
        from ..codemode_state import codemode_state

        if codemode_state().enabled:
            return Mode.CODE
    except Exception:
        pass  # an import/state problem must never break mode resolution
    return parse_mode(getattr(config, "mode", Mode.CHAT))


def mode_label(config: object) -> str:
    """The active mode's user-facing label (shared by every surface)."""
    return mode_title(active_mode(config))
