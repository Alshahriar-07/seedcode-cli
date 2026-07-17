"""Application controller: startup dashboard, chat REPL, and the main menu.

Startup renders the dashboard once and drops straight into the chat prompt;
/exit from chat reaches the numbered menu (provider, key, model, settings).
Chat can only begin once setup is complete — otherwise the guided chain
provider -> API key -> validate -> fetch models -> select -> save runs
first. All actions are guarded: no failure may crash the application.
"""

from __future__ import annotations

from prompt_toolkit import PromptSession
from rich.table import Table

from .commands import CommandContext, dispatch, is_command
from .commands.about import show_about
from .commands.history import settings_menu
from .commands.provider import apikey_menu, select_model, select_provider
from .config import load_config
from .core.agent import AgentEngine, strip_tool_blocks
from .core.chat import ChatEngine, ChatError
from .core.models import AppConfig
from .core.providers import PROVIDERS, provider_label
from .core.providers.freemodel import AUTO_MODEL
from .memory import HistoryStore
from .tools import PermissionManager, PermissionMode
from .ui import UI
from .ui.prompts import PT_STYLE, prompt_label
from .utils.logger import get_logger

_log = get_logger("app")

_MENU_ITEMS = (
    ("1", "Start Chat"),
    ("2", "Provider"),
    ("3", "API Key"),
    ("4", "Model"),
    ("5", "Settings"),
    ("6", "About"),
    ("0", "Exit"),
)


def _provider_status(config: AppConfig) -> str:
    """Menu status line: the active provider, or 'Not Configured'."""
    ready = config.provider == "ollama" or bool(config.get_api_key().strip())
    return provider_label(config.provider) if ready else "Not Configured"


def _model_status(config: AppConfig) -> str:
    """Menu status line: the selected model, or 'Not Selected'."""
    if not config.model:
        return "Not Selected"
    if config.model == AUTO_MODEL:
        return "Auto (best free model)"
    return config.model


def _key_status(config: AppConfig) -> str:
    """Menu status line: the active provider's masked key."""
    provider = PROVIDERS.get(config.provider)
    if provider is not None and not provider.requires_key:
        return "(not required)"
    return config.masked_key()


def _connection_status(config: AppConfig) -> str:
    """Cached connection status for the active provider (no network I/O)."""
    provider = PROVIDERS.get(config.provider)
    return provider.status if provider is not None else "Unknown"


def _render_menu(ui: UI, config: AppConfig) -> None:
    table = Table.grid(padding=(0, 3))
    table.add_column(style="seed.dim", justify="right")
    table.add_column(style="seed.text")
    table.add_row("Provider", _provider_status(config))
    table.add_row("Status", _connection_status(config))
    table.add_row("API Key", _key_status(config))
    table.add_row("Model", _model_status(config))
    table.add_row("", "")
    for key, label in _MENU_ITEMS:
        table.add_row(f"[{key}]", label)
    ui.panel(table, title="Menu")


def _guided_setup(ui: UI, config: AppConfig) -> bool:
    """Provider -> API key -> validate -> fetch models -> select -> save.

    Reuses the exact /provider and /model flows so setup and mid-session
    switching behave identically. Returns True once chat is possible.
    """
    ui.info("Setup: choose a provider to get started.")
    if not select_provider(ui, config):
        return False
    if not config.model:
        select_model(ui, config)
    return config.is_configured()


def _handle_chat(ui: UI, engine: ChatEngine, history: HistoryStore, text: str) -> None:
    """Send a user turn to the model and stream the reply to screen."""
    engine.add_user(text)
    renderer = None
    try:
        chunks = engine.stream_reply()
        # Spinner until the first token, then hand off to the live renderer.
        first = ""
        with ui.thinking():
            for piece in chunks:
                first = piece
                break
        with ui.streaming() as renderer:
            if first:
                renderer.feed(first)
            for piece in chunks:
                renderer.feed(piece)
    except ChatError as exc:
        # Drop the unanswered user turn so a retry doesn't send two
        # consecutive user messages (strict APIs reject that shape).
        engine.drop_last_user()
        ui.error(str(exc))
        return
    except KeyboardInterrupt:
        # Ctrl+C cancels this response only — the session keeps going.
        ui.blank()
        ui.dim("(response cancelled)")

    reply = renderer.text if renderer is not None else ""
    if reply.strip():
        engine.add_assistant(reply)
        history.save(engine.transcript)
    else:
        # No reply (empty response, or cancelled before the first token):
        # forget the user turn so the transcript stays alternating.
        engine.drop_last_user()
        if renderer is not None:
            ui.dim("(no response)")


def _handle_agent(ui: UI, agent: AgentEngine, history: HistoryStore, text: str) -> None:
    """Run one full agent turn (tool loop) and render the final answer."""

    try:
        with ui.thinking("Working"):
            reply = agent.run_turn(text)
    except ChatError as exc:
        ui.error(str(exc))
        return
    except KeyboardInterrupt:
        # Ctrl+C aborts the remaining agent steps; work already done stays.
        ui.blank()
        ui.dim("(agent turn cancelled — completed tool actions were kept)")
        return

    final = strip_tool_blocks(reply)
    if final.strip():
        with ui.streaming() as renderer:
            renderer.feed(final)
    else:
        ui.dim("(no response)")
    history.save(agent.transcript)


def _make_agent(ui: UI, config: AppConfig) -> AgentEngine:
    """Build an agent engine bound to the CWD and the configured permissions."""
    permissions = PermissionManager(mode=PermissionMode.parse(config.permission_mode))

    def narrate(kind: str, detail: str) -> None:
        if kind == "call":
            ui.dim(f"  ⚒ {detail}")
        elif kind == "error":
            ui.dim(f"  ✖ {detail.splitlines()[0][:120]}")
        elif kind == "limit":
            ui.warning(f"Agent stopped: {detail}")

    return AgentEngine(config, permissions, on_event=narrate)


def _chat_loop(
    ui: UI,
    config: AppConfig,
    engine: ChatEngine,
    history: HistoryStore,
    session: PromptSession,
) -> None:
    """Interactive chat until /exit (returns to the main menu)."""
    ctx = CommandContext(ui=ui, config=config, engine=engine)
    ui.dim("Type /help for commands, /exit to return to the menu.")

    # The agent engine is built lazily on the first agent-mode turn and
    # rebuilt when the permission mode changes (its system prompt names it).
    agent: AgentEngine | None = None
    agent_perm = config.permission_mode

    while True:
        try:
            text = session.prompt(
                prompt_label(f"{config.username} > "),
                style=PT_STYLE,
            ).strip()
        except KeyboardInterrupt:
            # Ctrl+C cancels the current line, does not quit.
            ui.dim("(use /exit for the menu)")
            continue
        except EOFError:
            # Ctrl+D returns to the menu.
            ui.blank()
            return

        if not text:
            continue

        if is_command(text):
            backend_before = config.provider
            try:
                result = dispatch(ctx, text)
            except (KeyboardInterrupt, EOFError):
                ui.dim("Cancelled.")
                continue
            except Exception as exc:  # a broken command must not kill the REPL
                _log.exception("command failed: %s", text.split()[0])
                ui.error(f"Command failed: {exc}")
                continue
            if result.should_exit:
                return
            if config.provider != backend_before:
                # Provider switched mid-chat: the whole backend state
                # (client, models, history) refreshes — start fresh.
                ui.dim("(provider changed — returning to the menu)")
                return
            continue

        ui.blank()
        if config.agent_mode:
            if agent is None or agent_perm != config.permission_mode:
                agent = _make_agent(ui, config)
                agent_perm = config.permission_mode
            _handle_agent(ui, agent, history, text)
        else:
            _handle_chat(ui, engine, history, text)


def run(ui: UI) -> None:
    """Show the startup dashboard, drop straight into chat, then the menu.

    The dashboard renders exactly once at launch; the chat prompt follows
    immediately (after guided setup when nothing is configured yet). The
    numbered menu remains available via /exit for provider/model/settings.
    """
    config = load_config()
    ui.banner(config)
    _log.info(
        "started: provider=%s model=%s configured=%s",
        config.provider,
        config.model or "(none)",
        config.is_configured(),
    )

    active_backend = config.provider
    engine = ChatEngine(config)
    history = HistoryStore(provider_id=active_backend)
    menu_session: PromptSession = PromptSession()
    chat_session: PromptSession = PromptSession()

    # Straight into chat after the dashboard — the menu is one /exit away.
    try:
        if config.is_configured() or _guided_setup(ui, config):
            if config.provider != active_backend:
                # Guided setup switched providers: rebuild the backend state.
                active_backend = config.provider
                engine = ChatEngine(config)
                history = HistoryStore(provider_id=active_backend)
            _chat_loop(ui, config, engine, history, chat_session)
        else:
            ui.dim("Setup incomplete — chat needs a provider and a model.")
    except (KeyboardInterrupt, EOFError):
        ui.dim("Cancelled.")
    except Exception as exc:  # startup chat must never crash the app
        _log.exception("startup chat failed")
        ui.error(f"Something went wrong: {exc}")

    while True:
        if config.provider != active_backend:
            # Provider switched: rebuild everything below it — fresh chat
            # backend/context and the new provider's own history store.
            _log.info("backend switched: %s -> %s", active_backend, config.provider)
            active_backend = config.provider
            engine = ChatEngine(config)
            history = HistoryStore(provider_id=active_backend)
        _render_menu(ui, config)
        try:
            choice = menu_session.prompt(
                prompt_label("Select > "), style=PT_STYLE
            ).strip().lower()
        except KeyboardInterrupt:
            ui.dim("(enter 0 to exit)")
            continue
        except EOFError:
            ui.blank()
            ui.dim("Goodbye — plant ideas, grow code.")
            return

        try:
            if choice in ("1", "chat", "start"):
                if not config.is_configured() and not _guided_setup(ui, config):
                    ui.dim("Setup incomplete — chat needs a provider and a model.")
                    continue
                _chat_loop(ui, config, engine, history, chat_session)
            elif choice in ("2", "provider"):
                select_provider(ui, config)
            elif choice in ("3", "apikey", "api key", "key"):
                apikey_menu(ui, config)
            elif choice in ("4", "model"):
                select_model(ui, config)
            elif choice in ("5", "settings"):
                settings_menu(ui, config)
            elif choice in ("6", "about"):
                show_about(ui, config)
            elif choice in ("0", "exit", "quit", "q"):
                ui.dim("Goodbye — plant ideas, grow code.")
                return
            elif not choice:
                continue
            else:
                ui.warning(f"Unknown option '{choice}'. Enter 0-6.")
        except (KeyboardInterrupt, EOFError):
            ui.dim("Cancelled.")
        except Exception as exc:  # menu actions must never crash the app
            _log.exception("menu action failed: %s", choice)
            ui.error(f"Something went wrong: {exc}")
