"""The Seed Code startup header (v6.2.5 minimal layout).

The launch screen is a small, borderless block — no ASCII logo, no box, no
decorative art, no duplicated information::

    Seed Code CLI v6.2.5
    Provider  Default
    Model     nvidia/nemotron-3-super-120b-a12b:free
    Mode      Chat
    Status    ● Ready

Design rules:

* the runtime state (provider / model / mode / status) is shown **exactly
  once**, here — never repeated in a footer;
* every value is read from live application state; nothing is hardcoded and
  "Ready" is never faked for an unconfigured session;
* the ``API Key`` row appears **only** for providers that require a key, so
  Default and Ollama never show an API-key line at all (their keys are not
  required — see :mod:`seedcode.core.providers`);
* the block is five or six short rows, so it stays inside a standard 80x24
  terminal with room to spare.

The chat prompt (``You >``) is owned by the prompt session in
:mod:`seedcode.app` and stays outside this module.
"""

from __future__ import annotations

from rich.console import Console
from rich.text import Text

from .. import APP_NAME, __version__
from ..core.models import AppConfig
from ..core.providers import PROVIDERS, provider_label, provider_ready, provider_requires_key
from ..core.providers.base import STATUS_CONNECTED, STATUS_UNKNOWN

# Label column: the longest label ("Provider") plus one separating space.
_LABEL_WIDTH = 9

# Widest provider/model value shown before the display value is clipped. The
# configured value is never modified — only the rendering is ellipsized.
_VALUE_CLIP = 48


# --- dynamic session values (real application state only) -------------------
def _provider_value(config: AppConfig) -> str:
    """Provider display label ('Not configured' before setup)."""
    if provider_ready(config.provider, config):
        return provider_label(config.provider)
    return "Not configured"


def _model_value(config: AppConfig) -> str:
    if not config.model:
        return "no model"
    if config.model == "auto":
        return "Auto"
    return config.model


def _mode_value(config: AppConfig) -> Text:
    """The user-facing mode, read from real session state.

    Code Mode sharpens Assist Mode; both are shown verbatim (never the legacy
    Agent/Desktop names). Imported lazily so the dashboard has no import-cycle
    cost at startup, and guarded so a state error can never break the banner.
    """
    try:
        from ..codemode_state import codemode_state

        if codemode_state().enabled:
            return Text("Code Mode", style="seed.accent")
    except Exception:
        pass
    if config.agent_mode:
        return Text("Assist Mode", style="seed.accent")
    return Text("Chat", style="seed.accent")


def _status_value(config: AppConfig, ready_mark: str, idle_mark: str) -> Text:
    """Connection status from the provider's session cache (no network I/O)."""
    if not config.is_configured():
        return Text("Setup needed", style="seed.dim")
    provider = PROVIDERS.get(config.provider)
    status = provider.status if provider is not None else STATUS_UNKNOWN
    if status == STATUS_CONNECTED:
        return Text(f"{ready_mark} Connected", style="seed.success")
    if status == STATUS_UNKNOWN:
        # Configured but not probed yet — ready to chat, not yet verified.
        return Text(f"{ready_mark} Ready", style="seed.success")
    return Text(f"{idle_mark} {status}", style="seed.dim")


def _requires_key(config: AppConfig) -> bool:
    """Whether the active provider uses an API key (Default and Ollama do not)."""
    return provider_requires_key(config.provider)


def _api_key_value(config: AppConfig) -> Text:
    """The active provider's key state — masked, never the key itself.

    Only rendered for providers that require a key, so no API-key line is ever
    shown for the built-in Default connection or for local Ollama.
    """
    if config.get_api_key().strip():
        return Text(config.masked_key(), style="seed.dim")
    return Text("Not set — /apikey", style="seed.warning")


def _clip(value: str, limit: int = _VALUE_CLIP) -> str:
    """Ellipsize a *display* value; the configured value is never modified."""
    if len(value) <= limit:
        return value
    return value[: max(limit - 1, 1)] + "…"


# --- rendering ---------------------------------------------------------------
def _row(label: str, value: Text, *, legacy: bool) -> Text:
    """One ``Label    value`` line, clipped to the terminal by the console."""
    line = Text(no_wrap=True, overflow="crop")
    line.append(f"{label:<{_LABEL_WIDTH}}", style="seed.dim")
    line.append_text(value)
    return line


def _rows(config: AppConfig, *, legacy: bool) -> list[Text]:
    """The information rows, in display order, from real state only."""
    ready_mark = "o" if legacy else "●"
    idle_mark = "-" if legacy else "○"

    rows = [
        _row(
            "Provider",
            Text(_clip(_provider_value(config)), style="seed.primary"),
            legacy=legacy,
        ),
        _row("Model", Text(_clip(_model_value(config)), style="seed.text"), legacy=legacy),
        _row("Mode", _mode_value(config), legacy=legacy),
    ]
    if _requires_key(config):
        # Only key-requiring providers get an API-key row: Default (built-in
        # connection) and Ollama (local) never show one.
        rows.append(_row("API Key", _api_key_value(config), legacy=legacy))
    rows.append(
        _row("Status", _status_value(config, ready_mark, idle_mark), legacy=legacy)
    )
    return rows


def console_size(console: Console) -> int:
    """Current terminal width (80 when unknown, e.g. tests/CI)."""
    return console.size.width or 80


def render_dashboard(console: Console, config: AppConfig) -> None:
    """Render the startup header to ``console`` (once, at launch)."""
    legacy = console.legacy_windows
    title = Text(
        f"{APP_NAME} CLI v{__version__}", style="bold seed.primary", no_wrap=True
    )
    title.overflow = "crop"
    console.print(title)
    for row in _rows(config, legacy=legacy):
        console.print(row)
