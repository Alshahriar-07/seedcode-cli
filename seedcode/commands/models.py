"""/models — model management beyond the simple picker.

``/model`` selects one model from the active provider's live catalogue. This
command is the management surface around that choice:

* switch model (the live, searchable catalogue);
* use a custom model id (needed when a gateway does not expose ``/models``);
* maintain the ordered ``fallback_models`` list used by automatic failover;
* remove the active model;
* pin the execution profile globally or for the active model.

Every action changes the persisted ``AppConfig`` and the live engine reads the
same config, so a switch applies to the next request.
"""

from __future__ import annotations

from ..config import save_config
from ..core.model_profiles import PROFILES, profile_for
from ..core.providers import provider_label
from ..ui.menu import MenuItem, run_menu
from ..ui.textbox import read_text
from . import CommandContext, CommandResult, command
from .provider import select_model, set_model


# --- fallback list (pure helpers, used by the command and the tests) ---------


def add_fallback_model(config, model_id: str) -> tuple[bool, str]:
    """Append ``model_id`` to the failover list; returns (changed, message)."""
    candidate = (model_id or "").strip()
    if not candidate:
        return False, "A fallback model id is required."
    if candidate == (config.model or "").strip():
        return False, "The active model cannot also be its own fallback."
    if candidate in config.fallback_models:
        return False, f"'{candidate}' is already a fallback."
    config.fallback_models.append(candidate)
    return True, f"Added '{candidate}' to the fallback list."


def remove_fallback_model(config, model_id: str) -> bool:
    """Remove ``model_id`` from the failover list; returns whether it existed."""
    candidate = (model_id or "").strip()
    if candidate not in config.fallback_models:
        return False
    config.fallback_models = [m for m in config.fallback_models if m != candidate]
    return True


def clear_fallback_models(config) -> int:
    """Drop every fallback; returns how many were removed."""
    removed = len(config.fallback_models)
    config.fallback_models = []
    return removed


def pin_model_profile(config, model_id: str, profile_id: str) -> bool:
    """Pin one model to a profile (``model_profiles``); invalid input is a no-op."""
    model = (model_id or "").strip()
    profile = (profile_id or "").strip().lower()
    if not model or profile not in PROFILES:
        return False
    config.model_profiles[model] = profile
    return True


# --- screens ----------------------------------------------------------------


def _fallback_menu(ui, config) -> None:
    """Add/remove/clear the ordered model-failover list."""
    while True:
        items = [
            MenuItem(
                "Add fallback model…",
                "add",
                status=f"{len(config.fallback_models)} configured",
            )
        ]
        for index, model in enumerate(config.fallback_models, start=1):
            items.append(MenuItem(f"{index}. {model}", f"remove:{model}"))
        items.append(
            MenuItem(
                "Clear fallbacks",
                "clear",
                disabled=not config.fallback_models,
            )
        )
        items.append(MenuItem("Back", "back"))
        choice = run_menu(
            items,
            title="Fallback models",
            hint="tried in order after the active model fails   Esc back",
        )
        if choice is None or choice == "back":
            return
        if choice == "add":
            model = read_text("Fallback model id > ")
            if model is None:
                continue
            changed, message = add_fallback_model(config, model)
            if changed:
                save_config(config)
                ui.success(message)
            else:
                ui.warning(message)
        elif choice == "clear":
            removed = clear_fallback_models(config)
            save_config(config)
            ui.success(f"Removed {removed} fallback model(s).")
        elif str(choice).startswith("remove:"):
            model = str(choice).split(":", 1)[1]
            if remove_fallback_model(config, model):
                save_config(config)
                ui.success(f"Removed '{model}'.")


def _profile_menu(ui, config) -> None:
    """Choose the execution profile, or pin one to the active model."""
    current = str(getattr(config, "prompt_profile", "auto") or "auto").lower()
    options = [
        MenuItem("Automatic (from model metadata)", "auto", status="current" if current == "auto" else ""),
    ]
    for profile_id in ("small", "standard", "advanced"):
        profile = PROFILES[profile_id]
        options.append(
            MenuItem(
                f"{profile.label} — {profile.description}",
                profile_id,
                status="current" if current == profile_id else "",
            )
        )
    options.append(MenuItem("Pin active model to a profile…", "pin"))
    options.append(MenuItem("Back", "back"))
    choice = run_menu(options, title="Execution profile", initial=current)
    if choice is None or choice == "back":
        return
    if choice == "pin":
        _pin_profile_menu(ui, config)
        return
    config.prompt_profile = str(choice)
    save_config(config)
    resolved = profile_for(config)
    ui.success(f"Execution profile set to {resolved.label}.")
    ui.dim(
        f"Steps {resolved.max_steps} · calls/step {resolved.max_calls_per_step} "
        f"· output cap {resolved.max_output_tokens or 'none'}"
    )


def _pin_profile_menu(ui, config) -> None:
    """Pin the current model to one profile in ``model_profiles``."""
    model = (config.model or "").strip()
    if not model:
        ui.warning("Select a model before pinning a profile.")
        return
    choice = run_menu(
        [
            MenuItem(PROFILES[pid].label, pid)
            for pid in ("small", "standard", "advanced")
        ]
        + [MenuItem("Back", "back")],
        title=f"Profile for {model}",
    )
    if choice is None or choice == "back":
        return
    if pin_model_profile(config, model, str(choice)):
        save_config(config)
        ui.success(f"Pinned '{model}' to {PROFILES[str(choice)].label}.")


def model_management_menu(ui, config) -> None:
    """The /models screen; every entry performs a real action."""
    while True:
        profile = profile_for(config)
        items = [
            MenuItem("Switch model…", "switch", status=config.model or "none"),
            MenuItem(
                "Use a custom model id…",
                "custom",
                status="for gateways without a catalogue",
            ),
            MenuItem(
                "Fallback models…",
                "fallback",
                status=f"{len(config.fallback_models)} configured",
            ),
            MenuItem(
                "Execution profile…",
                "profile",
                status=profile.label,
            ),
            MenuItem(
                "Remove active model",
                "remove",
                disabled=not bool(config.model),
            ),
            MenuItem("Back", "back"),
        ]
        choice = run_menu(
            items,
            title=f"Models — {provider_label(config.provider)}",
            hint="↑↓ move   Enter select   Esc back",
            initial="switch",
        )
        if choice is None or choice == "back":
            return
        if choice == "switch":
            select_model(ui, config)
        elif choice == "custom":
            model = read_text("Model id > ")
            if model:
                set_model(ui, config, model)
        elif choice == "fallback":
            _fallback_menu(ui, config)
        elif choice == "profile":
            _profile_menu(ui, config)
        elif choice == "remove":
            set_model(ui, config, "")
            ui.success("Active model cleared. Run /models to select another.")


def _print_model_summary(ui, config) -> None:
    profile = profile_for(config)
    ui.print(f"  Provider: {provider_label(config.provider)}")
    ui.print(f"  Model: {config.model or '(none selected)'}")
    ui.print(f"  Profile: {profile.label} ({profile.id})")
    if config.fallback_models:
        ui.print("  Fallbacks: " + ", ".join(config.fallback_models))
    else:
        ui.dim("  No fallback models configured.")
    if config.model_profiles:
        pinned = ", ".join(f"{m}={p}" for m, p in config.model_profiles.items())
        ui.dim(f"  Pinned profiles: {pinned}")


@command(
    "models",
    "Manage models: switch, custom ids, fallbacks, and execution profiles",
)
def _models_cmd(ctx: CommandContext, arg: str) -> CommandResult:
    text = arg.strip()
    lowered = text.lower()
    if not text:
        model_management_menu(ctx.ui, ctx.config)
    elif lowered in ("fallback", "fallbacks"):
        _fallback_menu(ctx.ui, ctx.config)
    elif lowered in ("profile", "profiles"):
        _profile_menu(ctx.ui, ctx.config)
    elif lowered == "list":
        _print_model_summary(ctx.ui, ctx.config)
    else:
        set_model(ctx.ui, ctx.config, text)
    return CommandResult()
