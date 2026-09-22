"""Application controller: startup dashboard, chat REPL, and the main menu.

Startup renders the dashboard once and drops straight into the chat prompt;
/exit from chat reaches the interactive main menu (arrow keys + fuzzy
filter — no numbers anywhere). Chat can only begin once setup is complete —
otherwise the guided chain provider -> API key -> validate -> fetch models
-> select -> save runs first. All actions are guarded: no failure may crash
the application.

Global shortcuts at the chat prompt: Ctrl+K command palette, Ctrl+P project
file search, Ctrl+R history, Ctrl+/ shortcut reference, Ctrl+, settings,
Ctrl+L clear screen.
"""

from __future__ import annotations

import sys

from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings

from . import __version__
from .commands import CommandContext, dispatch, is_command
from .commands.about import show_about
from .commands.help import show_shortcuts
from .commands.history import browse_history, settings_menu
from .commands.palette import open_file_search, open_palette
from .commands.provider import apikey_menu, select_model, select_provider
from .commands.theme import pick_theme
from .config import load_config
from .core.agent import AgentEngine, strip_tool_blocks
from .core.chat import ChatEngine, ChatError
from .core.lifecycle import LifecycleError, lifecycle
from .core.models import AppConfig
from .core.providers import PROVIDERS, provider_label, provider_ready
from .core.providers.freemodel import AUTO_MODEL
from .memory import HistoryStore
from .tools import PermissionManager, PermissionMode
from .ui import UI
from .ui.badges import badge_for_status
from .ui.menu import MenuItem, run_menu
from .ui.reference import CONTROLS_HINT, INPUT_HINT
from .ui.tasks import TaskFlow, TaskState
from .ui.textbox import prompt_label
from .ui.theme import pt_style, set_active_theme
from .utils.logger import get_logger

_log = get_logger("app")

# Sentinels returned by chat-prompt key bindings (never valid user text).
_KEY_ACTIONS = {
    "__palette__": "palette",
    "__files__": "files",
    "__history__": "history",
    "__shortcuts__": "shortcuts",
    "__settings__": "settings",
}


def _exit_application(ui: "UI", reason: str) -> None:
    """The single, explicit application-exit decision.

    Called only from unambiguous user actions (the menu's Exit item, leaving
    the menu with Esc/Ctrl+C/Ctrl+D). Marks the lifecycle, enters SHUTDOWN
    (running teardown hooks), and returns; :func:`run` then unwinds and
    ``main`` finishes normally. Task completion and errors never reach here.

    ``ui`` is passed in explicitly by :func:`run` — this module has no global
    UI instance, and referencing one from here was the root cause of the
    ``NameError: name 'ui' is not defined`` users saw on Exit.
    """
    lc = lifecycle()
    lc.request_exit(reason)
    if lc.phase.value != "shutdown":
        try:
            lc.shutdown()
        except Exception:  # a teardown hook must never block exiting
            pass
    ui.dim("Goodbye — plant ideas, grow code.")


def _release_desktop_resources() -> None:
    """Teardown hook: release desktop/session resources, never the process.

    Cleanup clears caches and closes driver connections only. It must never
    terminate SeedCode — historically a finally-block that "cleaned up" by
    exiting was one of the auto-exit paths.

    v7.1.0 additionally guarantees the other direction: cleanup never closes a
    user-facing application. Applications Seed Code opened stay open, which is
    why this hook releases only internal resources (sockets, drivers, caches).
    """
    try:
        from .computer import lifecycle_guard

        lifecycle_guard.release_internal()  # logs what is deliberately left open
    except Exception:
        pass
    try:
        from .computer.browser_cdp import reset as _cdp_reset

        _cdp_reset()
    except Exception:
        pass
    try:
        from .computer import browser_skills

        browser_skills.reset_engines()
    except Exception:
        pass
    try:
        from .computer.permissions import session_permissions

        session_permissions().reset()
    except Exception:
        pass


def _provider_status(config: AppConfig) -> str:
    """Menu status line: the active provider, or 'Not Configured'.

    Uses the shared provider-ready rule, so the menu never disagrees with the
    dashboard: Default and Ollama need no key, everyone else needs their own.
    """
    if provider_ready(config.provider, config):
        return provider_label(config.provider)
    return "Not Configured"


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


def _mode_status(config: AppConfig) -> str:
    """Menu status for the Code Mode item (ON only when really active)."""
    try:
        from .codemode_state import codemode_state

        if codemode_state().enabled:
            return "ON"
    except Exception:
        pass
    return "ON" if config.agent_mode else "OFF"


def _main_menu(config: AppConfig):
    """The interactive main menu; returns an action id or None (exit).

    v6.2.5 reference layout: the six mode/setup actions carry Ctrl+1..Ctrl+6
    shortcuts that execute the real handlers (no decorative items), followed
    by the chat/setup actions kept from earlier releases.
    """
    provider = PROVIDERS.get(config.provider)
    badge = badge_for_status(provider.status if provider is not None else "")
    return run_menu(
        [
            MenuItem("Code Mode", "codemode", status=_mode_status(config), shortcut="1"),
            MenuItem("Agent Mode", "agent", shortcut="2"),
            MenuItem("Assist Mode", "assist", shortcut="3"),
            MenuItem("Project Memory", "memory", shortcut="4"),
            MenuItem("Settings", "settings", shortcut="5"),
            MenuItem("Exit", "exit", shortcut="6"),
            MenuItem("Start Chat", "chat", status=_model_status(config), badge=badge),
            MenuItem("Provider", "provider", status=_provider_status(config)),
            MenuItem("API Key", "apikey", status=_key_status(config)),
            MenuItem("Model", "model", status=_model_status(config)),
            MenuItem("Theme", "theme", status=config.theme),
            MenuItem("About", "about"),
        ],
        title=f"Seed Code v{__version__}",
        hint=CONTROLS_HINT,
        initial="chat",
    )


def _dispatch_command(ui: UI, config: AppConfig, engine, command: str) -> None:
    """Run a slash command from a menu action through the real router."""
    dispatch(CommandContext(ui=ui, config=config, engine=engine), command)


def _report_command_error(ui: UI, exc: Exception) -> None:
    """Render a failed command/action without leaking a raw traceback.

    A transport failure becomes one actionable ``[Network Error]`` line, mapped
    by :mod:`seedcode.core.http` (which never includes request headers, so an
    API key can never surface here). Anything else keeps its own message; the
    full traceback is already written to the log file.
    """
    try:
        import httpx

        from .core.http import friendly_error

        if isinstance(exc, httpx.HTTPError):
            ui.error(f"[Network Error] {friendly_error(exc)}")
            ui.dim("Action: check your network connection or provider configuration.")
            return
    except Exception:
        pass  # error reporting must never itself raise
    ui.error(f"[Command Error] {exc}")


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


class _TaskPresenter:
    """Bridges engine activity to the live step view (and plain narration).

    One presenter per chat session; ``flow`` is set for the duration of a turn
    so the engine's callbacks land in that turn's task view. Everything here is
    best-effort: the presenter can never fail a task.
    """

    # Live command output is echoed compactly; the full output still goes to
    # the model and the log file, and errors are never hidden.
    MAX_ECHO_LINES = 8

    def __init__(self, ui: UI) -> None:
        self.ui = ui
        self.flow: TaskFlow | None = None
        self._echo_lines = 0
        self._echo_elided = False

    # --- one command's live output ------------------------------------------
    def reset_echo(self) -> None:
        self._echo_lines = 0
        self._echo_elided = False

    def on_output(self, line: str) -> None:
        """Compact live echo: the first few lines, then a single elision note."""
        if self._echo_lines < self.MAX_ECHO_LINES:
            self.ui.dim(f"  │ {line[:200]}")
        elif not self._echo_elided:
            self._echo_elided = True
            self.ui.dim(
                f"  │ … output continues (first {self.MAX_ECHO_LINES} lines shown; "
                "the agent and the log file get it all)"
            )
        self._echo_lines += 1

    # --- narration (human-readable) -----------------------------------------
    def on_event(self, kind: str, detail: str) -> None:
        flow = self.flow
        if kind == "say" and flow is not None:
            flow.observe_text(detail)
            return
        if kind == "error":
            self.ui.dim(f"  ✖ {detail.splitlines()[0][:120]}")
        elif kind == "limit":
            self.ui.warning(f"Assist stopped: {detail}")
        elif kind == "call" and flow is None:
            # With a task view on screen the step list already shows the tool
            # work; without one, narrate it (the plain/legacy experience).
            self.ui.dim(f"  ⚒ {detail}")

    # --- structured activity -> step states ---------------------------------
    def on_step(self, payload: dict) -> None:
        flow = self.flow
        if flow is None:
            return
        phase = str(payload.get("phase") or "")
        name = str(payload.get("name") or "")
        args = payload.get("args") or {}
        if phase == "tool_start":
            self.reset_echo()
            flow.observe_tool_start(name, args)
        elif phase == "tool_done":
            flow.observe_tool_done(
                name,
                bool(payload.get("ok")),
                str(payload.get("output") or ""),
                args,
            )
        elif phase == "say":
            flow.observe_text(str(payload.get("text") or ""))


    # --- persistent session events (Code Mode) ------------------------------
    def on_session_event(self, kind: str, detail: str) -> None:
        """Render compact progress for the persistent session (v7.1.0).

        Only what the user needs: the plan, the task that just finished, the
        live action, a repair in progress, and the outcome. Raw model output is
        never dumped here.
        """
        from .core import session as session_mod

        flow = self.flow
        session = session_mod.current_session()
        if kind == "plan":
            self.ui.dim("  planning the work…")
            return
        if kind == "plan_ready":
            self.ui.dim(f"  plan: {detail}")
        elif kind == "task_start":
            active = None
            if flow is not None and session is not None:
                flow.attach_plan(session.graph)
                active = session.graph.active() or session.graph.next_task()
                if active is not None:
                    flow.set_progress(active.id, session.graph.total, active.title)
                    # The live action line reports the real phase (v7.1.0).
                    flow.set_activity(f"Working on: {active.title}")
            self.ui.dim(f"  → {detail}")
        elif kind == "verify":
            # A real phase, not decoration: the session is checking acceptance
            # criteria against observed evidence right now.
            if flow is not None:
                flow.set_activity("Verifying acceptance criteria")
        elif kind == "task_done":
            if flow is not None and session is not None:
                # Refresh the checklist so the finished task shows as done.
                flow.attach_plan(session.graph)
            self.ui.success(detail)
        elif kind == "recovery":
            if flow is not None:
                flow.set_activity("Diagnosing the failure and repairing")
            self.ui.warning(detail)
        elif kind == "task_blocked":
            self.ui.warning(detail)
        elif kind == "task_failed":
            self.ui.error(detail)
        elif kind == "final_verify":
            self.ui.dim("  final verification…")
            if flow is not None:
                flow.set_activity("Verifying the project (tests / build)")
        elif kind in ("retry", "continue"):
            self.ui.dim(f"  {detail}")
        if flow is not None and session is not None:
            flow.note_call(session.model_calls)
        state = {
            "session_paused": "paused",
            "session_cancelled": "cancelled",
            "session_failed": "failed",
            "session_completed": "completed",
        }.get(kind)
        if flow is not None and state is not None:
            if session is not None:
                flow.attach_plan(session.graph)  # final, real plan state
            flow.set_state(state)


def _advance(lc, method: str) -> None:
    """Best-effort lifecycle transition around one turn.

    The REPL owns the turn span (``PLANNING`` → … → ``IDLE``); this lets the
    turn *narrate* where it is without ever turning a phase mismatch into a
    task failure — a lifecycle bookkeeping problem must not stop the work.
    """
    try:
        getattr(lc, method)()
    except LifecycleError:
        _log.debug("lifecycle transition %s skipped", method)


def _codemode_enabled() -> bool:
    """Whether Code Mode is active (the persistent-session path)."""
    try:
        from . import codemode_state as _cms

        state = _cms.codemode_state()
        return bool(state.enabled and state.workspace is not None)
    except Exception:
        return False


def resume_codemode_session(ui: UI, config: AppConfig, history=None) -> bool:
    """Continue a paused/checkpointed Code Mode session (used by /resume).

    Returns whether a session was actually resumed. The agent is rebuilt for
    the current config (exactly as a fresh turn would), so a resume after a
    provider or permission change still runs with the live settings.
    """
    from .core import session as session_mod
    from .core.session import CodeSession

    workspace, store = _codemode_workspace()
    if store is None:
        return False
    agent = _make_agent(ui, config, _TaskPresenter(ui))
    presenter = agent_presenter(agent)
    session = CodeSession.resume(
        agent,
        store=store,
        workspace=workspace,
        on_event=presenter.on_session_event if presenter is not None else None,
    )
    if session is None:
        return False
    session.control.clear()
    _handle_codemode(
        ui,
        agent,
        history if history is not None else HistoryStore(provider_id=config.provider),
        session.request,
        presenter,
        session=session,
    )
    session_mod.set_current_session(None)
    return True


def agent_presenter(agent: AgentEngine) -> _TaskPresenter | None:
    """The presenter an engine was built with (used by /resume)."""
    presenter = getattr(agent, "presenter", None)
    return presenter if isinstance(presenter, _TaskPresenter) else None


def _task_mode_label(config: AppConfig) -> str:
    """The mode named in a task header (Code Mode sharpens Assist Mode)."""
    try:
        from .codemode_state import codemode_state

        if codemode_state().enabled:
            return "Code Mode"
    except Exception:
        pass
    return "Assist Mode"


def _save_codemode_summary(agent: AgentEngine, text: str, outcome: str) -> None:
    """v6.2.0: Code Mode sessions leave a compact summary in .seedcode/sessions."""
    try:
        from . import codemode_state as _cms

        state = _cms.codemode_state()
        if state.enabled and state.store is not None:
            state.store.save_session_summary({
                "goal": text[:200],
                "outcome": (outcome or "(no response)")[:400],
                "tool_calls": sum(1 for m in agent.messages if m.role == "tool"),
            })
    except Exception:
        pass


def _codemode_workspace():
    """The active Code Mode workspace + store, or (CWD, None) when unavailable."""
    try:
        from . import codemode_state as _cms

        state = _cms.codemode_state()
        if state.enabled and state.workspace is not None:
            return state.workspace, state.store
    except Exception:
        _log.exception("could not read the Code Mode workspace")
    from pathlib import Path

    return Path.cwd(), None


def _handle_codemode(
    ui: UI,
    agent: AgentEngine,
    history: HistoryStore,
    text: str,
    presenter: _TaskPresenter | None = None,
    *,
    session=None,
) -> None:
    """Run one persistent Code Mode session: plan → tasks → verification.

    Unlike a single agent turn, this keeps working until every planned task is
    verified (or the session is paused, cancelled, or blocked), then runs final
    verification. The task view shows the plan and real progress, and the CLI
    is ready for the next task in every outcome.
    """
    from .core import session as session_mod
    from .core.session import CodeSession, SessionStatus

    lc = lifecycle()
    workspace, store = _codemode_workspace()
    flow = TaskFlow.for_ui(ui, mode_label="Code Mode", task=text)
    if presenter is not None:
        presenter.flow = flow
    if flow is not None:
        flow.begin()
        flow.start()

    if session is None:
        session = CodeSession(
            agent,
            workspace=workspace,
            request=text,
            store=store,
            on_event=presenter.on_session_event if presenter is not None else None,
        )
    elif presenter is not None:
        session.set_observer(presenter.on_session_event)

    session_mod.set_current_session(session)
    status = SessionStatus.IDLE
    try:
        _advance(lc, "to_executing")
        status = session.run()
        _advance(lc, "to_verifying")
    except KeyboardInterrupt:
        session_mod.stop(session)
        status = SessionStatus.CANCELLED
        session.reason = "cancelled by the user"
    except Exception as exc:  # an engine bug must not kill the session either
        status = SessionStatus.FAILED
        session.reason = f"{type(exc).__name__}: {exc}"
        _log.exception("code mode session failed")
    finally:
        if presenter is not None:
            presenter.flow = None

    if session.store is not None and status is not SessionStatus.PAUSED:
        _save_codemode_summary(agent, text, session.reason or status.value)

    if status is SessionStatus.COMPLETED:
        _advance(lc, "to_responding")
        history.save(agent.transcript)
        _report_session_summary(ui, session)
        if flow is not None:
            flow.finish("completed")
        return

    if status is SessionStatus.PAUSED:
        # State is preserved and checkpointed: /resume continues this session.
        if flow is not None:
            flow.set_state("paused")
            flow.stop()
        ui.warning("Session paused — state saved. Send /resume to continue.")
        session_mod.set_current_session(session)
        return

    session_mod.set_current_session(None)
    if status is SessionStatus.CANCELLED:
        ui.blank()
        # v7.1.0: state is checkpointed on the way out, so say so — the session
        # is resumable rather than lost (the terminal itself stays open).
        ui.dim(
            "(session stopped — the plan, completed work and the project files "
            "were kept; /resume continues from the checkpoint)"
        )
        if flow is not None:
            flow.finish("cancelled")
        return
    reason = session.reason or "the session could not finish the plan"
    ui.error(reason)
    if flow is not None:
        flow.finish("failed", reason)


def _report_session_summary(ui: UI, session) -> None:
    """The final block: the evidence, in one compact summary (v7.1.0).

    Completion is evidence-gated, so the closing block reports the evidence
    itself rather than a restatement of the model's replies: the verification
    result, the tests that actually ran, the commands that actually succeeded,
    and the files the engine really touched. Lines that have nothing to report
    are omitted instead of being filled in with a placeholder.
    """
    graph = session.graph
    done, total = graph.progress()
    ui.success(f"Project completed — {done}/{total} tasks verified")
    evidence = session.session_evidence
    if evidence.tests:
        # What matters is the final state: the last test run passed.
        passed = evidence.tests[-1].ok
        ui.dim(f"  ✓ Verification: {'accepted' if passed else 'tests NOT passing'}")
        ui.dim(f"  ✓ Tests: {'passed' if passed else 'FAILED'}")
    else:
        ui.dim("  ✓ Verification: accepted (no test run in this project)")
    if session.state.changed_files:
        ui.dim(f"  ✓ Files: {len(session.state.changed_files)} affected")
    commands = evidence.commands
    if commands:
        ok = sum(1 for record in commands if record.ok)
        ui.dim(f"  ✓ Commands: {ok}/{len(commands)} ok")
    if session.state.inspected:
        ui.dim(f"  • Inspected: {len(session.state.inspected)} item(s)")


def _handle_agent(
    ui: UI,
    agent: AgentEngine,
    history: HistoryStore,
    text: str,
    presenter: _TaskPresenter | None = None,
) -> None:
    """Run one full Assist/Code turn (tool loop) and render the final answer.

    The turn is shown as a compact live task flow whose step states follow real
    engine activity (see :mod:`seedcode.ui.tasks`) — never a fake progress bar.

    Lifecycle note: every outcome — success, tool failure, provider error,
    Ctrl+C — ends by printing a status, finishing the task view and RETURNING.
    Nothing here may terminate the process; the surrounding ``task_span``
    guarantees the lifecycle returns to IDLE and the REPL keeps prompting, and
    the explicit task-flow finish line always says the CLI is ready for more.
    """
    if _codemode_enabled():
        _handle_codemode(ui, agent, history, text, presenter)
        return

    lc = lifecycle()
    flow = TaskFlow.for_ui(
        ui, mode_label=_task_mode_label(agent.config), task=text
    )
    if presenter is not None:
        presenter.flow = flow
    if flow is not None:
        flow.begin()
        flow.start()

    outcome = "completed"
    reason = ""
    reply: str | None = None
    try:
        if flow is None:  # the task view already shows that work is happening
            with ui.thinking("Working"):
                _advance(lc, "to_executing")
                reply = agent.run_turn(text)
        else:
            _advance(lc, "to_executing")
            reply = agent.run_turn(text)
        _advance(lc, "to_verifying")
    except ChatError as exc:
        outcome, reason = "failed", str(exc)
    except KeyboardInterrupt:
        # Ctrl+C aborts the remaining steps; work already done stays.
        outcome = "cancelled"
    except Exception as exc:  # an engine bug must not kill the session either
        outcome, reason = "failed", f"{type(exc).__name__}: {exc}"
        _log.exception("assist turn failed")
    finally:
        if presenter is not None:
            presenter.flow = None

    if outcome != "completed":
        if reason:
            ui.error(reason)
        else:
            ui.blank()
            ui.dim("(task cancelled — completed tool actions were kept)")
        _save_codemode_summary(agent, text, reason or "task cancelled")
        if flow is not None:
            flow.finish(outcome, reason)
        return

    _advance(lc, "to_responding")
    if flow is not None:
        flow.update("verify", TaskState.RUNNING, "checking the result")
        # Hand the screen back before streaming the answer: one live display at
        # a time, and the answer deserves the full width.
        flow.stop()
    final = strip_tool_blocks(reply or "")
    if final.strip():
        with ui.streaming() as renderer:
            renderer.feed(final)
    else:
        ui.dim("(no response)")
    if flow is not None:
        flow.update("verify", TaskState.COMPLETED, "answer produced")
    history.save(agent.transcript)
    _save_codemode_summary(agent, text, final.strip() or "(no response)")
    if flow is not None:
        # Prints the persistent final block and leaves the prompt ready for the
        # next task — the terminal is never closed from here.
        flow.finish("completed")


def _make_agent(
    ui: UI, config: AppConfig, presenter: "_TaskPresenter | None" = None
) -> AgentEngine:
    """Build an Assist engine bound to the CWD and the configured permissions."""
    # Lazy import (matching _make_desktop_session): the optional, platform-
    # specific computer package stays out of app.py's top-level import graph.
    from .computer import is_available

    permissions = PermissionManager(level=PermissionMode.parse(config.permission_mode))
    # Desktop capability is a property of the permission level now: attach the
    # Computer Engine gate whenever the level is Desktop or higher and the
    # engine is actually available on this machine.
    if permissions.level.allows_desktop and is_available()[0]:
        permissions.desktop = _make_desktop_session(ui)
    permissions.gate = _make_action_gate(ui)

    presenter = presenter or _TaskPresenter(ui)
    # Live terminal output: a compact echo of what a running command prints.
    permissions.on_output = presenter.on_output

    engine = AgentEngine(
        config,
        permissions,
        on_event=presenter.on_event,
        on_step=presenter.on_step,
    )
    # Attached so /resume can continue a paused session with the same view.
    engine.presenter = presenter  # type: ignore[attr-defined]
    return engine


def _make_action_gate(ui: UI):
    """Dangerous-action gate wired to the interactive permission dialog.

    Note: the Assist engine (and thus this gate) is rebuilt on permission-mode
    changes, so session "Always" grants reset then — conservative on purpose.
    """
    from .tools.permissions import ACTION_LABELS, ActionGate, ActionGrant

    def confirm(category: str, description: str) -> ActionGrant:
        label = ACTION_LABELS.get(category, category)
        answer = ui.confirm_tool_action(label, description)
        return {
            "y": ActionGrant.ONCE,
            "a": ActionGrant.ALWAYS,
        }.get(answer, ActionGrant.DENY)

    return ActionGate(confirm=confirm)


def _make_desktop_session(ui: UI):
    """Desktop Control gate wired to the interactive permission dialog.

    The session is bound to the process-wide
    :class:`~seedcode.computer.SessionPermissionManager` so a permission the
    user granted once at ``/assist on`` keeps holding even though this gate is
    rebuilt whenever the permission level changes.
    """
    from .computer import DesktopGrant, DesktopSession, session_permissions
    from .computer.permissions import CATEGORY_LABELS

    def confirm(category: str, description: str) -> DesktopGrant:
        label = CATEGORY_LABELS.get(category, category)
        answer = ui.confirm_desktop(label, description)
        return {
            "y": DesktopGrant.ONCE,
            "a": DesktopGrant.ALWAYS,
        }.get(answer, DesktopGrant.DENY)

    return DesktopSession(
        enabled=True, confirm=confirm, session=session_permissions()
    )


def _run_key_action(ui: UI, ctx: CommandContext, config: AppConfig, action: str) -> None:
    """Dispatch one chat-prompt shortcut sentinel."""
    try:
        if action == "palette":
            open_palette(ctx)
        elif action == "files":
            open_file_search(ctx)
        elif action == "history":
            browse_history(ui, config)
        elif action == "shortcuts":
            show_shortcuts(ui)
        elif action == "settings":
            settings_menu(ui, config)
    except (KeyboardInterrupt, EOFError):
        ui.dim("Cancelled.")
    except Exception as exc:  # a broken picker must not kill the REPL
        _log.exception("shortcut action failed: %s", action)
        ui.error(f"Something went wrong: {exc}")


def _chat_loop(
    ui: UI,
    config: AppConfig,
    engine: ChatEngine,
    history: HistoryStore,
    session: PromptSession | _PlainSession,
) -> None:
    """Interactive chat until /exit (returns to the main menu).

    Only an explicit exit leaves here: a finished task (Code Mode, Assist
    Mode, Agent Mode or a plain chat turn) returns to the prompt with
    ``Ready for next task.`` and the session keeps running.
    """
    ctx = CommandContext(ui=ui, config=config, engine=engine)
    ui.dim(INPUT_HINT)

    # The Assist engine is built lazily on the first assist-mode turn and
    # rebuilt when the permission or desktop mode changes (its system
    # prompt and permission gates reflect both). The presenter that owns the
    # live step view lives for the whole chat session, so each turn can bind
    # its own task flow to the same engine callbacks.
    agent: AgentEngine | None = None
    presenter = _TaskPresenter(ui)
    agent_perm = config.permission_mode
    # v6.2.0: track Code Mode so a /codemode toggle rebuilds the agent with
    # workspace context (the engine is constructed lazily per turn).
    try:
        from . import codemode_state as _cms

        agent_codemode = _cms.enabled
    except Exception:
        agent_codemode = False

    while True:
        try:
            raw = session.prompt(
                prompt_label(f"{config.username} > "),
                style=pt_style(),
            )
        except KeyboardInterrupt:
            # Ctrl+C cancels the current line, does not quit.
            ui.dim("(use /exit for the menu)")
            continue
        except EOFError:
            # Ctrl+D returns to the menu.
            ui.blank()
            return

        if raw in _KEY_ACTIONS:
            _run_key_action(ui, ctx, config, _KEY_ACTIONS[raw])
            continue

        text = raw.strip()
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
                _report_command_error(ui, exc)
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
        # One whole user turn inside the lifecycle span: whatever happens —
        # success, failure, cancellation, even an unexpected crash — the
        # span's ``finally`` returns the state machine to IDLE and the REPL
        # prompts again. A task can never end the app.
        with lifecycle().task_span():
            if config.agent_mode:
                try:
                    from . import codemode_state as _cms

                    codemode_now = _cms.enabled
                except Exception:
                    codemode_now = False
                if (
                    agent is None
                    or agent_perm != config.permission_mode
                    or agent_codemode != codemode_now
                ):
                    agent = _make_agent(ui, config, presenter)
                    agent_perm = config.permission_mode
                    agent_codemode = codemode_now
                # One task flow per turn: the engine reports real activity into
                # it, and the REPL keeps prompting when the turn ends.
                _handle_agent(ui, agent, history, text, presenter)
            else:
                _handle_chat(ui, engine, history, text)


def _interactive() -> bool:
    """True when a real console is attached (mirrors ``ui.selector._interactive``).

    prompt_toolkit needs a console/PTY it can drive. Piped, redirected, or
    console-less hosts (CI, ``seedcode < file``, some Git Bash/MSYS sessions)
    cannot provide one, and constructing a prompt there raises instead of
    degrading — so every interactive entry point checks this first.
    """
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


class _PlainSession:
    """Line-based stand-in for :class:`PromptSession` on non-console hosts.

    The chat loop only needs ``prompt()``, so this shim keeps Seed Code usable
    where prompt_toolkit cannot start. Reading raises ``EOFError`` /
    ``KeyboardInterrupt`` exactly like the real session, so the loop's existing
    Ctrl+C / Ctrl+D handling is unchanged.
    """

    def prompt(self, message=None, **_kwargs) -> str:
        return input(_plain_text(message) or "> ")


def _plain_text(message) -> str:
    """Literal text of a prompt message (a prompt_toolkit ``FormattedText``)."""
    if isinstance(message, str):
        return message
    try:
        return "".join(fragment[1] for fragment in message)
    except Exception:
        return ""


def _make_chat_session(ui: UI):
    """A prompt_toolkit session when a console exists, else the plain shim."""
    if not _interactive():
        return _PlainSession()
    try:
        return _build_chat_session(ui)
    except Exception:
        # A console that exists but still cannot be driven (e.g. a MSYS/mintty
        # TERM against a non-Windows console). Never fatal — degrade instead.
        _log.warning("prompt_toolkit unavailable; using line-based input")
        return _PlainSession()


def _build_chat_session(ui: UI) -> PromptSession:
    """The chat PromptSession with the global shortcut bindings attached."""
    kb = KeyBindings()

    kb.add("c-k")(lambda e: e.app.exit(result="__palette__"))
    kb.add("c-p")(lambda e: e.app.exit(result="__files__"))
    kb.add("c-r")(lambda e: e.app.exit(result="__history__"))
    # Ctrl+/ reaches terminals as Ctrl+_; bind both spellings.
    kb.add("c-_")(lambda e: e.app.exit(result="__shortcuts__"))

    @kb.add("c-l")
    def _(event) -> None:
        ui.console.clear()
        event.app.renderer.clear()

    # Ctrl+, (settings) — where the terminal delivers it distinctly.
    try:
        kb.add("c-,")(lambda e: e.app.exit(result="__settings__"))
    except (ValueError, KeyError):
        pass  # terminals without a distinct Ctrl+, sequence

    return PromptSession(key_bindings=kb)


def run(ui: UI) -> None:
    """Show the startup dashboard, drop straight into chat, then the menu.

    The dashboard renders exactly once at launch; the chat prompt follows
    immediately (after guided setup when nothing is configured yet). The
    interactive menu remains available via /exit for provider/model/settings.
    """
    config = load_config()
    set_active_theme(config.theme)
    ui.apply_theme(config.theme)
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
    chat_session = _make_chat_session(ui)

    # Best-effort teardown for the one legitimate shutdown path.
    lifecycle().on_shutdown(_release_desktop_resources)

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

        try:
            choice = _main_menu(config)
        except (KeyboardInterrupt, EOFError):
            # The user explicitly left the menu — the app-exit decision.
            _exit_application(ui, "menu interrupt")
            return

        try:
            if choice == "chat":
                if not config.is_configured() and not _guided_setup(ui, config):
                    ui.dim("Setup incomplete — chat needs a provider and a model.")
                    continue
                _chat_loop(ui, config, engine, history, chat_session)
            elif choice == "provider":
                select_provider(ui, config)
            elif choice == "apikey":
                apikey_menu(ui, config)
            elif choice == "model":
                select_model(ui, config)
            elif choice == "codemode":
                _dispatch_command(ui, config, engine, "/codemode on")
            elif choice == "agent":
                _dispatch_command(ui, config, engine, "/agent on")
            elif choice == "assist":
                _dispatch_command(ui, config, engine, "/assist on")
            elif choice == "memory":
                _dispatch_command(ui, config, engine, "/codemode status")
            elif choice == "settings":
                settings_menu(ui, config)
            elif choice == "theme":
                pick_theme(ui, config)
            elif choice == "about":
                show_about(ui, config)
            elif choice in ("exit", None):
                _exit_application(ui, "menu exit")
                return
        except (KeyboardInterrupt, EOFError):
            ui.dim("Cancelled.")
        except Exception as exc:  # menu actions must never crash the app
            _log.exception("menu action failed: %s", choice)
            _report_command_error(ui, exc)
