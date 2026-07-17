"""Prompt Toolkit styling and input helpers — keeps the green identity in prompts."""

from __future__ import annotations

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.styles import Style

# prompt_toolkit style for the input line.
PT_STYLE = Style.from_dict({"prompt": "#2ecc71 bold"})


def prompt_label(text: str) -> FormattedText:
    """Build a green, class-styled prompt label for prompt_toolkit."""
    return FormattedText([("class:prompt", text)])


def read_line(text: str, *, password: bool = False) -> str | None:
    """Read one line of styled input; ``None`` means cancelled (Ctrl+C/D)."""
    session: PromptSession = PromptSession()
    try:
        return session.prompt(
            prompt_label(text), is_password=password, style=PT_STYLE
        ).strip()
    except (EOFError, KeyboardInterrupt):
        return None
