"""The canonical mode system (v8.2.5): exactly two user-facing modes.

Seed Code exposes **two** modes and nothing else:

* :attr:`Mode.CHAT`  — conversation: questions, explanations, brainstorming.
  It never runs project/system actions.
* :attr:`Mode.AGENT` — the unified autonomous workspace/coding agent: inspect,
  plan, edit, run, verify and keep working until the task is genuinely done,
  using every available tool (filesystem, terminal, git, browser, keyboard,
  mouse, windows, vision/OCR, desktop automation).

The former **Code Mode** is no longer a separate mode: its workspace-aware
coding capabilities (the ``.seedcode`` project memory and index, the
plan → execute → verify session loop) are now native capabilities of Agent
Mode. ``"code"``, ``"codemode"`` and ``"code-mode"`` are still accepted as
input aliases but resolve to :attr:`Mode.AGENT`, so no third mode can be
created or displayed anywhere.

This module is the single source of truth for mode parsing and labels. Every
surface (``/mode``, ``/chat``, ``/agent``, ``/codemode``, the dashboard, the
status bar, the header, the task header) resolves the mode through here so they
can never disagree.
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
    """The two canonical runtime modes."""

    CHAT = "chat"
    AGENT = "agent"


#: User-facing label for each mode (the only names the UI may show).
MODE_LABELS: dict[Mode, str] = {
    Mode.CHAT: "Chat Mode",
    Mode.AGENT: "Agent Mode",
}

#: One-line responsibility statement for each mode.
MODE_DESCRIPTIONS: dict[Mode, str] = {
    Mode.CHAT: "Conversation — questions, explanations, brainstorming. No project actions.",
    Mode.AGENT: (
        "Autonomous workspace agent — inspect, plan, edit, run and verify "
        "using every available tool until the task is done."
    ),
}

#: Legacy spellings accepted on input and mapped to a canonical mode. The
#: retired Code Mode (and its ``codemode`` command spelling) is Agent Mode now:
#: its workspace coding capabilities are native to Agent Mode.
_ALIASES: dict[str, Mode] = {
    "chat": Mode.CHAT,
    # Assist Mode no longer exists as a mode; it is Agent Mode now.
    "assist": Mode.AGENT,
    "agent": Mode.AGENT,
    "desktop": Mode.AGENT,
    # Code Mode is no longer a separate mode; it is Agent Mode now.
    "code": Mode.AGENT,
    "codemode": Mode.AGENT,
    "code-mode": Mode.AGENT,
}


def parse_mode(value: object, default: Mode = Mode.CHAT) -> Mode:
    """Resolve any stored/typed mode value to a canonical :class:`Mode`.

    An empty or unrecognised value yields ``default`` (never an error), so a
    corrupt config or a typo can never create a third mode or crash startup.
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
    """Whether a mode may act on the project/system (Agent Mode only)."""
    return parse_mode(mode) is Mode.AGENT


def active_mode(config: object) -> Mode:
    """The mode the session is really in, from live state (v8.2.5).

    Agent Mode is the unified workspace agent: when the ``.seedcode`` workspace
    capability is enabled the session is acting as an agent, and the stored
    configuration decides otherwise. Nothing else is consulted, so there is no
    way to end up in an unnamed third mode.
    """
    try:
        from ..codemode_state import codemode_state

        if codemode_state().enabled:
            return Mode.AGENT
    except Exception:
        pass  # an import/state problem must never break mode resolution
    return parse_mode(getattr(config, "mode", Mode.CHAT))


def mode_label(config: object) -> str:
    """The active mode's user-facing label (shared by every surface)."""
    return mode_title(active_mode(config))
