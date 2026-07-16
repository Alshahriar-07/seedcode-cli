"""Provider and model selection: /provider, /model.

Both commands are interactive: they render a live list fetched from the
backend and let the user pick by number, exact id, or a filter string.
The same flows are reused by first-run onboarding (:mod:`seedcode.app`),
so setup and mid-session switching behave identically.
"""

from __future__ import annotations

from prompt_toolkit import PromptSession
from rich.table import Table

from ..config import save_config
from ..core.providers import (
    PROVIDERS,
    ModelInfo,
    Provider,
    ProviderError,
    get_provider,
)
from ..ui.prompts import PT_STYLE, prompt_label
from . import CommandContext, CommandResult, command


def _prompt(text: str, *, password: bool = False) -> str | None:
    """Read one line of input; ``None`` means the user cancelled (Ctrl+C/D)."""
    session: PromptSession = PromptSession()
    try:
        return session.prompt(
            prompt_label(text), is_password=password, style=PT_STYLE
        ).strip()
    except (EOFError, KeyboardInterrupt):
        return None


# --- provider selection ------------------------------------------------------


def _resolve_provider(text: str) -> Provider | None:
    """Match user input against provider ids and labels (prefix-tolerant)."""
    t = text.strip().lower()
    if not t:
        return None
    for p in PROVIDERS.values():
        if t in (p.id, p.label.lower()):
            return p
    matches = [
        p
        for p in PROVIDERS.values()
        if p.id.startswith(t) or p.label.lower().startswith(t)
    ]
    return matches[0] if len(matches) == 1 else None


def _provider_menu(ui, config) -> Provider | None:
    """Render the provider list and prompt until a valid choice or cancel."""
    providers = list(PROVIDERS.values())
    table = Table.grid(padding=(0, 3))
    table.add_column(style="seed.accent", justify="right")
    table.add_column(style="seed.text")
    table.add_column(style="seed.dim")
    for idx, p in enumerate(providers, 1):
        marker = "● current" if p.id == config.provider else ""
        note = "no API key needed" if not p.requires_key else "API key required"
        table.add_row(str(idx), f"{p.label}  ({note})", marker)
    ui.panel(table, title="Providers")

    while True:
        raw = _prompt("Provider (number or name) > ")
        if raw is None or not raw:
            ui.dim("Cancelled.")
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(providers):
            return providers[int(raw) - 1]
        chosen = _resolve_provider(raw)
        if chosen is not None:
            return chosen
        ui.warning(f"Unknown provider '{raw}'. Enter 1-{len(providers)} or a name.")


def _collect_key(ui, config, provider: Provider, *, replacing: bool = False) -> bool:
    """Prompt for, validate, and save an API key for ``provider``.

    Returns False when the user cancels. Only this provider's entry is
    written — other providers' keys are never touched.
    """
    if replacing:
        ui.info(f"Enter a new API key for {provider.label}.")
        ui.dim(f"Current: {config.masked_key(provider.id)}")
    else:
        ui.info(f"{provider.label} needs an API key.")
    if provider.key_hint:
        ui.dim(f"Key: {provider.key_hint}")
    while True:
        key = _prompt("API Key > ", password=True)
        if key is None or not key:
            ui.dim("Cancelled — no key saved.")
            return False
        with ui.thinking("Validating key"):
            result = provider.validate_key(key)
        if result.ok:
            config.set_api_key(provider.id, key)
            save_config(config)
            ui.success(result.message)
            return True
        ui.error(result.message)
        ui.dim("Try again, or press Enter to cancel.")


def _ensure_ready(ui, config, provider: Provider) -> bool:
    """Make ``provider`` usable: collect+validate a key, or detect Ollama.

    Returns False only when the user cancels key entry; a stopped Ollama
    server is reported but not fatal (the user may start it later).
    """
    if not provider.requires_key:
        with ui.thinking("Checking Ollama"):
            running = provider.detect(config)
        if running:
            ui.success("Ollama server detected.")
        else:
            ui.warning(
                f"Ollama is not reachable at {config.ollama_host}. "
                "Start it with 'ollama serve' — chatting will fail until it runs."
            )
        return True

    if config.get_api_key(provider.id).strip():
        return True
    return _collect_key(ui, config, provider)


def select_provider(ui, config, target: str = "") -> bool:
    """Switch the active provider; returns True when the switch completed.

    Only ``active_provider`` changes — every provider keeps its own saved
    API key and model, so switching back restores them untouched.
    """
    chosen: Provider | None = None
    if target:
        chosen = _resolve_provider(target)
        if chosen is None:
            ui.warning(f"Unknown provider '{target}'.")
    if chosen is None:
        chosen = _provider_menu(ui, config)
    if chosen is None:
        return False

    previous = config.provider
    config.provider = chosen.id
    if not _ensure_ready(ui, config, chosen):
        config.provider = previous  # cancelled key entry: keep the old backend
        return False

    save_config(config)
    ui.success(f"Provider set to {chosen.label}.")
    # The provider's own saved model is active again automatically.
    if config.model:
        ui.dim(f"Model: {config.model}")
    else:
        ui.warning(f"No model selected for {chosen.label} yet — run /model.")
    return True


# --- model selection ---------------------------------------------------------


def _set_model(ui, config, model_id: str) -> None:
    # Written into the ACTIVE provider's own slot — other providers keep theirs.
    config.model = model_id
    save_config(config)
    ui.success(f"Model set to {model_id}")


def _match_model(models: list[ModelInfo], text: str) -> ModelInfo | None:
    """Exact id match first, then a unique case-insensitive substring."""
    t = text.strip().lower()
    for m in models:
        if m.id.lower() == t:
            return m
    partial = [m for m in models if t in m.id.lower() or t in m.label.lower()]
    return partial[0] if len(partial) == 1 else None


def _render_models(ui, models: list[ModelInfo], total: int, provider_label: str) -> None:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="seed.accent", justify="right")
    table.add_column(style="seed.text")
    table.add_column(style="seed.dim")
    for idx, m in enumerate(models, 1):
        info = m.detail
        if m.label and m.label != m.id:
            info = f"{m.label}   {m.detail}".strip()
        table.add_row(str(idx), m.id, info)
    shown = f"{len(models)} of {total}" if len(models) != total else str(total)
    ui.panel(table, title=f"{provider_label} models ({shown})")


def select_model(ui, config, target: str = "") -> None:
    """Browse the live model catalogue of the active provider and pick one."""
    try:
        provider = get_provider(config.provider)
    except ProviderError as exc:
        ui.error(str(exc))
        return
    if provider.requires_key and not config.get_api_key(provider.id).strip():
        ui.warning(f"{provider.label} has no API key yet — run /provider first.")
        return

    try:
        with ui.thinking("Fetching models"):
            models = provider.list_models(config)
    except ProviderError as exc:
        if target and provider.id == "aerolink":
            # AeroLink may not expose /v1/models; accept the typed id as-is.
            _set_model(ui, config, target)
            ui.dim("(model list unavailable — id saved without verification)")
        else:
            ui.error(str(exc))
        return

    if target:
        m = _match_model(models, target)
        if m is not None:
            _set_model(ui, config, m.id)
        else:
            ui.warning(f"No model matching '{target}'. Run /model to browse.")
        return

    shown = models
    while True:
        _render_models(ui, shown, total=len(models), provider_label=provider.label)
        raw = _prompt("Model (number, id, or filter) > ")
        if raw is None or not raw:
            ui.dim("Cancelled.")
            return
        if raw.isdigit() and 1 <= int(raw) <= len(shown):
            _set_model(ui, config, shown[int(raw) - 1].id)
            return
        exact = next((m for m in models if m.id.lower() == raw.lower()), None)
        if exact is not None:
            _set_model(ui, config, exact.id)
            return
        filtered = [
            m
            for m in models
            if raw.lower() in m.id.lower() or raw.lower() in m.label.lower()
        ]
        if len(filtered) == 1:
            _set_model(ui, config, filtered[0].id)
            return
        if not filtered:
            ui.warning(f"No models match '{raw}'.")
            continue
        shown = filtered  # narrow the list and ask again


# --- command handlers --------------------------------------------------------


@command("provider", "Select the active provider (OpenRouter, AeroLink, Ollama)")
def _provider_cmd(ctx: CommandContext, arg: str) -> CommandResult:
    select_provider(ctx.ui, ctx.config, arg.strip())
    return CommandResult()


@command("model", "Browse and select a model for the active provider", aliases=("show",))
def _model_cmd(ctx: CommandContext, arg: str) -> CommandResult:
    target = arg.strip()
    # Support the documented "/show model" phrasing.
    if target.lower().startswith("model"):
        target = target[len("model"):].strip()
    select_model(ctx.ui, ctx.config, target)
    return CommandResult()


@command("apikey", "Set or replace the API key for the active provider", aliases=("key",))
def _apikey_cmd(ctx: CommandContext, arg: str) -> CommandResult:
    try:
        provider = get_provider(ctx.config.provider)
    except ProviderError as exc:
        ctx.ui.error(str(exc))
        return CommandResult()

    if not provider.requires_key:
        ctx.ui.info(f"{provider.label} does not use an API key.")
        return CommandResult()

    key = arg.strip()
    if key:
        # Key given inline: validate and save it directly.
        with ctx.ui.thinking("Validating key"):
            result = provider.validate_key(key)
        if result.ok:
            ctx.config.set_api_key(provider.id, key)
            save_config(ctx.config)
            ctx.ui.success(result.message)
        else:
            ctx.ui.error(result.message)
        return CommandResult()

    _collect_key(ctx.ui, ctx.config, provider, replacing=True)
    return CommandResult()
