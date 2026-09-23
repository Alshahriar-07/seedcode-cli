"""Provider and model selection: /provider, /model.

Both commands are fully interactive: arrow keys move, typing fuzzy-filters
the live list, Enter confirms, Esc cancels. The provider selector shows
status badges, the backend, and each provider's current model; the model
selector groups the catalogue by family. The same flows are reused by
first-run onboarding (:mod:`seedcode.app`), so setup and mid-session
switching behave identically.
"""

from __future__ import annotations

from ..config import save_config
from ..core.models import CustomProviderConfig, valid_base_url
from ..core.providers import (
    PROVIDERS,
    ModelInfo,
    Provider,
    ProviderError,
    get_provider,
    is_custom_provider_id,
    sync_custom_providers,
    visible_provider_ids,
)
from ..core.providers.base import STATUS_CONNECTED, STATUS_OFFLINE
from ..core.providers.freemodel import AUTO_MODEL
from ..ui.badges import badge_for_status
from ..ui.menu import MenuItem, run_menu
from ..ui.selector import Option, select
from ..ui.textbox import read_text
from . import CommandContext, CommandResult, command

#: Sentinel returned by the provider picker for "add a custom provider".
_ADD_CUSTOM = "__add_custom__"


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


# Selector groups, in display order. Providers are grouped by what they need
# from the user, so "Default" is never mistaken for "OpenRouter" (they can
# share an underlying service but are separate choices with separate config).
_GROUP_BUILTIN = "Built-in  ·  no API key needed"
_GROUP_BYOK = "Your own API key"
_GROUP_LOCAL = "Local  ·  runs on this machine"
_GROUP_CUSTOM = "Custom  ·  your own endpoint"


def _provider_group(provider: Provider) -> str:
    if is_custom_provider_id(provider.id):
        return _GROUP_CUSTOM
    if provider.local:
        return _GROUP_LOCAL
    if provider.id == "default":
        return _GROUP_BUILTIN
    return _GROUP_BYOK


def _provider_backend(provider: Provider, config) -> str:
    """Human connection type: 'Local' for on-machine backends, else the API family."""
    if provider.local:
        return "Local"
    return provider.backend_label or f"{provider.label} API"


def _provider_key_column(provider: Provider, config) -> str:
    """What this provider needs from the user: no key, a masked key, or a prompt.

    This is the column that makes the API-key rules visible before switching:
    Default and Ollama read "no API key", configured providers show their
    masked key, and the rest ask for one. A key is never shown in full.
    """
    if not provider.requires_key:
        return "no API key"
    if config.get_api_key(provider.id).strip():
        return config.masked_key(provider.id)
    return "API key needed"


def _provider_model_column(config, provider_id: str) -> str:
    """The provider's currently selected model, or an em dash."""
    custom = config.custom_provider(provider_id)
    if custom is not None:
        model = custom.model
    else:
        entry = config.providers.get(provider_id)
        model = entry.model if entry else ""
    if model == AUTO_MODEL:
        return "Auto"
    return model or "—"


def _provider_menu(ui, config) -> str | None:
    """Interactive provider selector: badge, backend, model, and key state.

    Offers the v7.2.5 list — OpenRouter, Ollama and every saved custom
    provider — plus an entry to add a new custom endpoint. A legacy built-in
    appears only while the user is on it (backward compatibility).
    """
    options = []
    for pid in visible_provider_ids(config):
        p = PROVIDERS.get(pid)
        if p is None:
            continue
        options.append(
            Option(
                p.label,
                value=p.id,
                badge=badge_for_status(p.status),
                columns=(
                    _provider_backend(p, config),
                    _provider_model_column(config, p.id),
                    _provider_key_column(p, config),
                ),
                group=_provider_group(p),
            )
        )
    options.append(
        Option(
            "Add a custom provider…",
            value=_ADD_CUSTOM,
            detail="any OpenAI-compatible API: name, base URL, key",
            group=_GROUP_CUSTOM,
        )
    )
    chosen = select(
        options,
        title="Provider",
        hint="↑↓ move   type to filter   Enter select   Esc cancel",
        initial=config.provider,
    )
    if chosen is None:
        ui.dim("Cancelled.")
        return None
    return str(chosen)


def _collect_key(ui, config, provider: Provider, *, replacing: bool = False) -> bool:
    """Prompt for, validate, and save an API key for ``provider``.

    Returns False when the user cancels. Only this provider's entry is
    written — other providers' keys are never touched.
    """
    provider.prepare(config)  # bind validation to the configured sub-backend
    if replacing:
        ui.info(f"Enter a new API key for {provider.label}.")
        ui.dim(f"Current: {config.masked_key(provider.id)}")
    else:
        ui.info(f"{provider.label} needs an API key.")
    if provider.key_hint:
        ui.dim(f"Key: {provider.key_hint}")
    while True:
        key = read_text("API Key > ", password=True)
        if key is None or not key:
            ui.dim("Cancelled — no key saved.")
            return False
        with ui.thinking("Validating key"):
            result = provider.validate_key(key)
        if result.ok:
            # Only a key that passed real authentication is ever saved.
            config.set_api_key(provider.id, key)
            save_config(config)
            provider.status = STATUS_CONNECTED
            ui.success(result.message)
            return True
        ui.error(result.message)
        ui.dim("Try again, or press Esc to cancel.")


def _ensure_ready(ui, config, provider: Provider) -> bool:
    """Make ``provider`` usable: collect+validate a key, or probe a key-less one.

    Returns False only when the user cancels key entry. An unreachable
    key-less backend (local Ollama, or the built-in Default connection in a
    build that carries no credential) is reported with its own actionable
    message but is never fatal — Ollama is auto-started (v7.2.5), and the
    built-in Default connection can be replaced by another provider later.
    """
    if not provider.requires_key:
        if provider.id == "ollama":
            return _ensure_ollama(ui, config, provider)
        # Use the provider's own status refresh so a build with no built-in
        # credential reports "No API Key" (a setup state) instead of a false
        # "Offline" (v7.2.5 pip-install fix).
        with ui.thinking(f"Checking {provider.label}"):
            status = provider.refresh_status(config)
        if status == STATUS_CONNECTED:
            ui.success(f"{provider.label} is ready.")
        else:
            ui.warning(provider.unavailable_hint(config))
        return True

    if config.get_api_key(provider.id).strip():
        # Existing key: refresh this provider's connection status with a
        # real request so the selector badge reflects reality immediately.
        with ui.thinking(f"Checking {provider.label}"):
            provider.refresh_status(config)
        return True
    return _collect_key(ui, config, provider)


def select_provider(ui, config, target: str = "") -> bool:
    """Switch the active provider; returns True when the switch completed.

    Only ``active_provider`` changes — every provider keeps its own saved
    API key and model, so switching back restores them untouched. Custom
    providers are synchronised into the registry first so they resolve.
    """
    sync_custom_providers(config)
    chosen: Provider | None = None
    if target:
        chosen = _resolve_provider(target)
        if chosen is None:
            entry = config.custom_provider(target)
            if entry is not None:
                chosen = get_provider(entry.id)
        if chosen is None:
            ui.warning(f"Unknown provider '{target}'.")
    if chosen is None:
        picked = _provider_menu(ui, config)
        if picked is None:
            return False
        if picked == _ADD_CUSTOM:
            entry = _add_custom_flow(ui, config)
            if entry is None:
                return False
            chosen = get_provider(entry.id)
        else:
            chosen = PROVIDERS.get(picked)
    if chosen is None:
        return False

    previous = config.provider
    config.provider = chosen.id
    if not _ensure_ready(ui, config, chosen):
        config.provider = previous  # cancelled key entry: keep the old backend
        return False

    # An explicit selection clears any prior failure state, so a provider the
    # user deliberately chose is tried again even if it had cooled down.
    from ..core.providers import health as health_mod

    health_mod.tracker().reset(chosen.id)
    save_config(config)
    ui.success(f"Provider set to {chosen.label}.")
    # The provider's own saved model is active again automatically.
    if config.model:
        ui.dim(f"Model: {config.model}")
    else:
        ui.warning(f"No model selected for {chosen.label} yet — run /model.")
    return True


# --- Ollama auto-start -------------------------------------------------------


def _ensure_ollama(ui, config, provider: Provider) -> bool:
    """Ensure the local Ollama server is running, starting it if needed.

    Starting the server never fails the selection: if it cannot be started,
    the user is told exactly what to do and may pick another provider.
    """
    from ..core.providers.ollama_start import ensure_running

    if provider.detect(config):
        provider.status = STATUS_CONNECTED
        ui.success(f"{provider.label} is ready.")
        return True

    ui.info("Ollama server not detected — starting it…")
    ok, message = ensure_running(config, on_status=lambda line: ui.dim(f"  {line}"))
    if ok:
        provider.status = STATUS_CONNECTED
        ui.success(message)
    else:
        provider.status = STATUS_OFFLINE
        ui.warning(message)
    return True


# --- custom providers --------------------------------------------------------


def custom_providers_menu(ui, config) -> None:
    """Manage the saved custom providers: add, use, edit, test, reorder, delete."""
    sync_custom_providers(config)
    while True:
        entries = config.ordered_custom_providers()
        items = [MenuItem("Add a custom provider…", "add", badge="new")]
        for entry in entries:
            state = "enabled" if entry.enabled else "disabled"
            items.append(
                MenuItem(
                    entry.name,
                    f"pick:{entry.id}",
                    status=f"{entry.base_url} · {state} · {config.masked_key(entry.id)}",
                )
            )
        items.append(MenuItem("Back", "back"))
        choice = run_menu(
            items,
            title=f"Custom providers ({len(entries)})",
            hint="↑↓ move   Enter select   Esc back",
        )
        if choice is None or choice == "back":
            return
        if choice == "add":
            _add_custom_flow(ui, config)
            continue
        _custom_actions(ui, config, str(choice).split(":", 1)[1])


def _add_custom_flow(ui, config) -> CustomProviderConfig | None:
    """Prompt for Name, Base URL, API Key (and optional model); save + test."""
    ui.info("Add a custom provider — any OpenAI-compatible API.")
    while True:
        name = read_text("Name > ")
        if name is None or not name.strip():
            ui.dim("Cancelled — no provider added.")
            return None
        base_url = read_text("Base URL > ")
        if base_url is None or not base_url.strip():
            ui.dim("Cancelled — no provider added.")
            return None
        if not valid_base_url(base_url):
            ui.error("Base URL must start with http:// or https://")
            continue
        key = read_text("API Key > ", password=True)
        if key is None:
            ui.dim("Cancelled — no provider added.")
            return None
        model = read_text("Model (optional) > ") or ""
        try:
            entry = config.add_custom_provider(name, base_url, key, model)
        except ValueError as exc:
            ui.error(str(exc))
            continue
        sync_custom_providers(config)
        save_config(config)
        ui.success(f"Saved custom provider '{entry.name}'.")
        _test_connection(ui, config, entry.id)
        return entry


def _edit_custom_flow(ui, config, provider_id: str) -> None:
    """Edit a saved custom provider; an empty answer keeps the current value."""
    entry = config.custom_provider(provider_id)
    if entry is None:
        return
    ui.info(f"Editing '{entry.name}' — press Enter to keep the current value.")
    name = read_text(f"Name [{entry.name}] > ")
    base_url = read_text(f"Base URL [{entry.base_url}] > ")
    key = read_text("API Key (keep current) > ", password=True)
    model = read_text(f"Model [{entry.model or 'none'}] > ")
    try:
        config.update_custom_provider(
            provider_id,
            name=(name.strip() if name and name.strip() else None),
            base_url=(base_url.strip() if base_url and base_url.strip() else None),
            api_key=(key.strip() if key and key.strip() else None),
            model=(model.strip() if model and model.strip() else None),
        )
    except ValueError as exc:
        ui.error(str(exc))
        return
    save_config(config)
    sync_custom_providers(config)
    ui.success("Saved.")


def _test_connection(ui, config, provider_id: str) -> bool:
    """Validate a provider's own credential with a real request."""
    from ..core.providers import health as health_mod

    try:
        provider = get_provider(provider_id)
    except ProviderError as exc:
        ui.error(str(exc))
        return False
    with ui.thinking(f"Testing {provider.label}"):
        result = provider.validate_key(config.get_api_key(provider_id))
    if result.ok:
        provider.status = STATUS_CONNECTED
        health_mod.tracker().record_success(provider_id)
        ui.success(result.message)
    else:
        provider.status = STATUS_OFFLINE
        health_mod.tracker().record_failure(provider_id, reason=result.message)
        ui.warning(result.message)
    return result.ok


def _custom_actions(ui, config, provider_id: str) -> None:
    """Per-provider actions: use, edit, test, enable/disable, reorder, delete."""
    while True:
        entry = config.custom_provider(provider_id)
        if entry is None:
            return
        state = "enabled" if entry.enabled else "disabled"
        choice = run_menu(
            [
                MenuItem("Use this provider", "use"),
                MenuItem("Edit", "edit"),
                MenuItem("Test connection", "test"),
                MenuItem("Disable" if entry.enabled else "Enable", "toggle"),
                MenuItem("Move up", "up"),
                MenuItem("Move down", "down"),
                MenuItem("Delete", "delete"),
                MenuItem("Back", "back"),
            ],
            title=f"{entry.name} — {state}",
            hint="↑↓ move   Enter select   Esc back",
        )
        if choice is None or choice == "back":
            return
        if choice == "use":
            select_provider(ui, config, entry.id)
            return
        if choice == "edit":
            _edit_custom_flow(ui, config, provider_id)
        elif choice == "test":
            _test_connection(ui, config, provider_id)
        elif choice == "toggle":
            config.set_custom_enabled(provider_id, not entry.enabled)
            save_config(config)
            sync_custom_providers(config)
            ui.success(f"'{entry.name}' {'disabled' if entry.enabled else 'enabled'}.")
        elif choice in ("up", "down"):
            moved = config.move_custom_provider(
                provider_id, -1 if choice == "up" else 1
            )
            save_config(config)
            sync_custom_providers(config)
            ui.dim("Reordered." if moved else "Already at the edge.")
        elif choice == "delete":
            from ..ui.dialog import confirm_dialog

            name = entry.name
            if confirm_dialog(
                f"Delete '{name}'?", yes_label="Delete", no_label="Keep", danger=True
            ):
                config.remove_custom_provider(provider_id)
                save_config(config)
                sync_custom_providers(config)
                ui.success(f"Deleted '{name}'.")
                return


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


# Family keywords for grouping the model selector (checked in order).
_FAMILIES: tuple[tuple[str, str], ...] = (
    ("codex", "Codex"),
    ("claude", "Claude"),
    ("gpt", "GPT"),
    ("o1", "GPT"),
    ("o3", "GPT"),
    ("qwen", "Qwen"),
    ("deepseek", "DeepSeek"),
    ("gemini", "Gemini"),
    ("gemma", "Gemini"),
    ("llama", "Llama"),
    ("mistral", "Mistral"),
    ("mixtral", "Mistral"),
)


def _model_group(model: ModelInfo) -> str:
    """Family header for the grouped model selector."""
    hay = f"{model.id} {model.label}".lower()
    for needle, family in _FAMILIES:
        if needle in hay:
            return family
    if "/" in model.id:
        vendor = model.id.split("/", 1)[0]
        return vendor.replace("-", " ").title()
    return "Other"


def _model_options(models: list[ModelInfo], current: str) -> list[Option]:
    """Grouped, badge-carrying options for the model selector."""
    grouped: dict[str, list[ModelInfo]] = {}
    for m in models:
        grouped.setdefault(_model_group(m), []).append(m)
    options: list[Option] = []
    for family in sorted(grouped, key=lambda g: (g == "Other", g.lower())):
        for m in grouped[family]:
            detail = m.detail
            if m.label and m.label != m.id:
                detail = f"{m.label}   {m.detail}".strip()
            options.append(
                Option(
                    m.id,
                    value=m.id,
                    detail=detail,
                    group=family,
                    badge="ready" if m.id == current else "",
                )
            )
    return options


def _pick_model_interactive(ui, config, provider: Provider, models: list[ModelInfo]) -> None:
    """The interactive grouped model selector (plus Auto and OpenRouter modes)."""
    options: list[Option] = []
    if provider.supports_auto:
        options.append(
            Option(
                "Auto",
                value=AUTO_MODEL,
                detail="best free model picked per request",
                group="Modes",
                badge="ready" if config.model == AUTO_MODEL else "",
            )
        )
    if provider.id == "openrouter":
        mode = provider.extra_settings(config).get("mode", "free")
        other = "pro" if mode == "free" else "free"
        options.append(
            Option(
                f"Switch to {other.title()} models",
                value=f"__mode__{other}",
                detail=f"currently showing {mode} models",
                group="Modes",
            )
        )
    options.extend(_model_options(models, config.model))

    chosen = select(
        options,
        title=f"Model — {provider.label} ({len(models)} available)",
        hint="type to filter (fuzzy)   ↑↓ move   Enter select   Esc cancel",
        initial=config.model or None,
        max_rows=14,
    )
    if chosen is None:
        ui.dim("Cancelled.")
        return
    choice = str(chosen)
    if choice.startswith("__mode__"):
        ok, message = provider.set_extra_setting(config, "mode", choice[len("__mode__"):])
        if not ok:
            ui.warning(message)
            return
        save_config(config)
        ui.success(message)
        try:
            with ui.thinking("Fetching models"):
                refreshed = provider.list_models(config)
        except ProviderError as exc:
            ui.error(str(exc))
            return
        _pick_model_interactive(ui, config, provider, refreshed)
        return
    if choice == AUTO_MODEL:
        _set_model(ui, config, AUTO_MODEL)
        ui.dim("(Auto mode: the best free model is picked per request)")
        return
    _set_model(ui, config, choice)


def select_model(ui, config, target: str = "") -> None:
    """Browse the live model catalogue of the active provider and pick one.

    Providers with ``supports_auto`` additionally offer Auto mode: the best
    model is resolved from the live catalogue on every request.
    """
    try:
        provider = get_provider(config.provider)
    except ProviderError as exc:
        ui.error(str(exc))
        return
    if provider.requires_key and not config.get_api_key(provider.id).strip():
        ui.warning(f"{provider.label} has no API key yet — run /provider first.")
        return

    if target and provider.supports_auto and target.lower() in ("auto", "a"):
        _set_model(ui, config, AUTO_MODEL)
        ui.dim("(Auto mode: the best free model is picked per request)")
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

    _pick_model_interactive(ui, config, provider, models)


# --- command handlers --------------------------------------------------------


@command(
    "provider",
    "Select the active provider (OpenRouter, Ollama, or a saved custom provider)",
)
def _provider_cmd(ctx: CommandContext, arg: str) -> CommandResult:
    select_provider(ctx.ui, ctx.config, arg.strip())
    return CommandResult()


@command(
    "custom",
    "Manage custom providers (add, edit, test, reorder, enable, delete)",
    aliases=("custom-providers", "providers"),
)
def _custom_cmd(ctx: CommandContext, arg: str) -> CommandResult:
    custom_providers_menu(ctx.ui, ctx.config)
    return CommandResult()


@command("model", "Browse and select a model for the active provider", aliases=("show",))
def _model_cmd(ctx: CommandContext, arg: str) -> CommandResult:
    target = arg.strip()
    # Support the documented "/show model" phrasing.
    if target.lower().startswith("model"):
        target = target[len("model"):].strip()
    select_model(ctx.ui, ctx.config, target)
    return CommandResult()


def apikey_menu(ui, config) -> None:
    """Manage the ACTIVE provider's API key: view, replace, remove, validate."""
    try:
        provider = get_provider(config.provider)
    except ProviderError as exc:
        ui.error(str(exc))
        return
    if not provider.requires_key:
        ui.info(f"{provider.label} does not use an API key.")
        return

    while True:
        has_key = bool(config.get_api_key(provider.id).strip())
        choice = run_menu(
            [
                MenuItem("View", "view", status=config.masked_key(provider.id)),
                MenuItem("Replace", "replace"),
                MenuItem("Remove", "remove", disabled=not has_key),
                MenuItem("Validate", "validate", disabled=not has_key),
            ],
            title=f"API Key — {provider.label}",
            hint="↑↓ move   Enter select   Esc back",
        )
        if choice is None:
            return
        if choice == "view":
            if has_key:
                ui.info(f"{provider.label} key: {config.masked_key(provider.id)}")
            else:
                ui.dim("No key saved yet.")
        elif choice == "replace":
            _collect_key(ui, config, provider, replacing=has_key)
        elif choice == "remove":
            from ..ui.dialog import confirm_dialog

            if confirm_dialog(
                "Remove the saved key?", yes_label="Remove", no_label="Keep", danger=True
            ):
                config.set_api_key(provider.id, "")
                save_config(config)
                ui.success(f"{provider.label} key removed.")
            else:
                ui.dim("Key kept.")
        elif choice == "validate":
            provider.prepare(config)
            with ui.thinking("Validating key"):
                result = provider.validate_key(config.get_api_key(provider.id))
            if result.ok:
                ui.success(result.message)
            else:
                ui.error(result.message)


@command("apikey", "View, replace, remove, or validate the active provider's key",
         aliases=("key",))
def _apikey_cmd(ctx: CommandContext, arg: str) -> CommandResult:
    key = arg.strip()
    if key:
        # Key given inline: validate and save it directly.
        try:
            provider = get_provider(ctx.config.provider)
        except ProviderError as exc:
            ctx.ui.error(str(exc))
            return CommandResult()
        if not provider.requires_key:
            ctx.ui.info(f"{provider.label} does not use an API key.")
            return CommandResult()
        provider.prepare(ctx.config)
        with ctx.ui.thinking("Validating key"):
            result = provider.validate_key(key)
        if result.ok:
            ctx.config.set_api_key(provider.id, key)
            save_config(ctx.config)
            ctx.ui.success(result.message)
        else:
            ctx.ui.error(result.message)
        return CommandResult()

    apikey_menu(ctx.ui, ctx.config)
    return CommandResult()
