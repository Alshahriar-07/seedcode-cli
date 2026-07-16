"""Application controller: onboarding flow and the interactive REPL.

Loads config, runs first-run onboarding when the active provider is not usable
yet, then enters the chat loop. The loop is defensive: unexpected errors become
friendly messages rather than tracebacks. :func:`run` is the single entry point
the CLI calls.
"""

from __future__ import annotations

from prompt_toolkit import PromptSession

from .commands import CommandContext, dispatch, is_command
from .commands.provider import select_model, select_provider
from .config import load_config
from .core.chat import ChatEngine, ChatError
from .core.models import AppConfig
from .memory import HistoryStore
from .ui import UI
from .ui.prompts import PT_STYLE, prompt_label


def _onboarding(ui: UI, config: AppConfig) -> None:
    """First-run experience: pick a provider, add credentials, pick a model.

    Uses the exact same flows as /provider and /model so setup and mid-session
    switching behave identically. Cancelling is fine — the user can finish
    setup later from inside the REPL.
    """
    ui.panel(
        "Welcome! Choose your AI provider to get started.\n"
        "You can switch anytime with /provider and pick models with /model.",
        title="Seed Code Setup",
    )
    if select_provider(ui, config) and not config.model:
        select_model(ui, config)
    if not config.is_configured():
        ui.dim("Setup incomplete — run /provider and /model when ready.")


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


def _repl(ui: UI, config: AppConfig) -> None:
    """Main interactive loop."""
    engine = ChatEngine(config)
    history = HistoryStore()
    ctx = CommandContext(ui=ui, config=config, engine=engine)
    session: PromptSession = PromptSession()

    while True:
        try:
            text = session.prompt(
                prompt_label(f"{config.username} > "),
                style=PT_STYLE,
            ).strip()
        except KeyboardInterrupt:
            # Ctrl+C cancels the current line, does not quit.
            ui.dim("(use /exit to quit)")
            continue
        except EOFError:
            # Ctrl+D exits cleanly.
            ui.blank()
            ui.dim("Goodbye — plant ideas, grow code.")
            return

        if not text:
            continue

        if is_command(text):
            try:
                result = dispatch(ctx, text)
            except (KeyboardInterrupt, EOFError):
                ui.dim("Cancelled.")
                continue
            except Exception as exc:  # a broken command must not kill the REPL
                ui.error(f"Command failed: {exc}")
                continue
            if result.should_exit:
                return
            continue

        ui.blank()
        _handle_chat(ui, engine, history, text)


def run(ui: UI) -> None:
    """Load config, run onboarding if needed, and enter the REPL."""
    config = load_config()
    ui.banner(config)

    if not config.is_configured():
        _onboarding(ui, config)

    _repl(ui, config)
