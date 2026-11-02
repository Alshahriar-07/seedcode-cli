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
import threading
import time
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings

from . import __version__
from .commands import CommandContext, CommandResult, dispatch, is_command
from .commands.about import show_about
from .commands.help import show_shortcuts
from .commands.history import browse_history, settings_menu
from .commands.palette import open_file_search, open_palette
from .commands.provider import apikey_menu, select_model, select_provider
from .commands.theme import pick_theme
from .config import load_config
from .core.agent import AgentEngine, strip_tool_blocks
from .core.chat import ChatEngine, ChatError
from .core.intent import Intent, classify
from .core.lifecycle import LifecycleError, lifecycle
from .core.models import AppConfig
from .core.providers import (
    PROVIDERS,
    provider_label,
    provider_ready,
    sync_custom_providers,
)
from .core.providers.freemodel import AUTO_MODEL
from .memory import HistoryStore
from .tools import PermissionManager, PermissionMode
from .ui import UI
from .ui.badges import badge_for_status
from .ui.menu import MenuItem, run_menu
from .ui.reference import CONTROLS_HINT, INPUT_HINT
from .ui.state import AppState, Status
from .workspace import active_workspace
from .ui.tasks import TaskFlow, TaskState, activity_for
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
    """Teardown hook: release internal session resources, never the process.

    v9.2.0: desktop control is removed from the product, so there is nothing
    from the Computer Engine to release any more. The hook remains as the one
    teardown point the lifecycle registered at startup.
    """
    return None


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


def _workspace_status(config: AppConfig) -> str:
    """Menu status for the active project (ON only when really bound)."""
    try:
        from .codemode_state import codemode_state

        return "ON" if codemode_state().enabled else "OFF"
    except Exception:
        return "OFF"


def _main_menu(config: AppConfig):
    """The interactive main menu; returns an action id or None (exit).

    v9.2.0 layout — one coding agent, no mode items: the session starts in
    the agent directly (``Start Coding Session``), the project workspace
    (folder selection + memory/index) is a first-class item, and the setup
    actions remain.
    """
    provider = PROVIDERS.get(config.provider)
    badge = badge_for_status(provider.status if provider is not None else "")
    return run_menu(
        [
            MenuItem(
                "Start Coding Session", "chat",
                status=_model_status(config), badge=badge, shortcut="1",
            ),
            MenuItem(
                "Project Workspace", "memory",
                status=_workspace_status(config), shortcut="2",
            ),
            MenuItem("Settings", "settings", shortcut="3"),
            MenuItem("Exit", "exit", shortcut="4"),
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


def _retrieve_context(ui: UI, intent: Intent, text: str) -> str:
    """Fetch fresh external information for a research request (v9.1.1).

    Returns a bounded context block, or '' when retrieval is disabled, fails,
    or finds nothing. The caller shows a lightweight status and continues — a
    failed lookup must never stop the answer. Nothing here is fatal.
    """
    try:
        from .core import internet

        if internet.internet_disabled():
            return ""
        ui.dim("[Accessing internet...]")
        result = internet.research(intent.target or text)
        if not result.ok:
            ui.dim("[No online sources available]")
            return ""
        if result.excerpts:
            ui.dim("[Reading relevant sources...]")
        return internet.context_block(result)
    except Exception:  # retrieval must never break a turn
        _log.exception("internet retrieval failed")
        return ""


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
        # Event ids already rendered: the backend may re-report a step through
        # both observers, and the feed must never duplicate a message.
        self._seen_events: set[str] = set()
        # This turn's observed operations (the final report's data). Every
        # entry comes from a real structured event, never from the model.
        self._turn_ops = self._fresh_turn()

    # --- live status (persistent TUI only) ----------------------------------
    def _set_status(self, status: Status, operation: str | None = None) -> None:
        """Reflect a real phase on the live header; a no-op on the plain UI.

        Only the TUI exposes ``state``, so the sequential console is untouched.
        """
        state = getattr(self.ui, "state", None)
        if state is None:
            return
        try:
            state.set_status(status, operation)
        except Exception:
            pass  # a cosmetic status must never break a task

    # --- one command's live output ------------------------------------------
    def reset_echo(self) -> None:
        self._echo_lines = 0
        self._echo_elided = False

    def on_output(self, line: str) -> None:
        """Compact live echo: the first few lines, then a single elision note."""
        if self._echo_lines == 0:
            self._set_status(Status.EXECUTING, "running a command")
        if self._echo_lines < self.MAX_ECHO_LINES:
            self.ui.dim(f"  │ {line[:200]}")
        elif not self._echo_elided:
            self._echo_elided = True
            self.ui.dim(
                f"  │ … output continues (first {self.MAX_ECHO_LINES} lines shown; "
                "the agent and the log file get it all)"
            )
        self._echo_lines += 1

    # --- turn operation tracking (the final report's data) -------------------
    @staticmethod
    def _fresh_turn() -> dict:
        """Empty operation buckets for one turn."""
        return {
            "created": [],
            "modified": [],
            "deleted": [],
            "commands": [],  # (command, ok) in order
            "tests": [],  # ok flags in run order
            "research": [],  # queries and fetched urls
            "errors": [],
        }

    def _record_op(self, payload: dict) -> None:
        """Fold one real backend event into the turn's report data."""
        kind = str(payload.get("type") or "")
        turn = self._turn_ops
        args = payload.get("args") or {}
        path = str(payload.get("path") or "")
        if kind == "file_created":
            turn["created"].append(path)
        elif kind == "file_updated":
            turn["modified"].append(path)
        elif kind in ("file_deleted", "folder_deleted"):
            turn["deleted"].append(path)
        elif kind in ("terminal_completed", "test_completed"):
            ok = bool(payload.get("ok"))
            command = str(args.get("command") or "").strip()
            if not command:
                return
            turn["commands"].append((command, ok))
            if kind == "test_completed":
                turn["tests"].append(ok)
            record = getattr(self.ui, "record_command", None)
            if callable(record):
                record(command, ok, str(payload.get("output") or ""))
        elif kind == "internet_search":
            query = str(args.get("query") or "").strip()
            if query:
                turn["research"].append(query)
        elif kind == "internet_result":
            if path:
                turn["research"].append(path)
        elif kind == "error":
            detail = str(payload.get("message") or "").strip()
            if detail:
                turn["errors"].append(detail)

    def _flush_report(self, state: str) -> None:
        """Render the turn's final execution report, built from observed events.

        A turn that performed no file/command/test/research work prints no
        report at all — sections are never filled in with placeholders. The
        buckets reset either way so the next turn starts clean.
        """
        append_report = getattr(self.ui, "append_report", None)
        turn = self._turn_ops
        empty = (
            not turn["created"]
            and not turn["modified"]
            and not turn["deleted"]
            and not turn["commands"]
            and not turn["tests"]
            and not turn["research"]
            and not turn["errors"]
        )
        if callable(append_report) and not empty:
            try:
                append_report({"state": state, **turn})
            except Exception:
                pass  # a display bug must never end a task
        self._turn_ops = self._fresh_turn()

    # --- narration (human-readable) -----------------------------------------
    def on_event(self, kind: str, detail: str) -> None:
        flow = self.flow
        if kind == "say" and flow is not None:
            flow.observe_text(detail)
            return
        if kind == "thinking":
            # v9.2.0: model-exposed reasoning (real provider channel only).
            # Streams into the bounded thinking panel; ignored elsewhere.
            tui = getattr(self.ui, "tui", None)
            if tui is not None:
                try:
                    tui.state.append_thinking(detail)
                except Exception:  # pragma: no cover - display-only
                    pass
            return
        if kind == "error":
            self.ui.dim(f"  ✖ {detail.splitlines()[0][:120]}")
            self._set_status(Status.ERROR, "an operation failed")
        elif kind == "switching_provider":
            # A provider request failed and a healthy fallback took over; the
            # task continues rather than restarting (v7.2.5).
            self.ui.warning(f"  Switching provider: {detail}")
            if flow is not None:
                flow.set_activity("Switching provider")
        elif kind == "retry":
            self.ui.dim(f"  {detail}")
        elif kind == "limit":
            self.ui.warning(f"Agent Mode stopped: {detail}")
        elif kind == "call" and flow is None and not getattr(self.ui, "is_tui", False):
            # With a task view on screen the step list already shows the tool
            # work; without one, narrate it (the plain/legacy experience).
            # The TUI renders the richer structured event instead.
            self.ui.dim(f"  ⚒ {detail}")
            self._set_status(Status.EXECUTING, detail)

    # --- structured activity -> step states ---------------------------------
    def on_step(self, payload: dict) -> None:
        event_id = str(payload.get("event_id") or "")
        if event_id:
            if event_id in self._seen_events:
                return
            self._seen_events.add(event_id)
            if len(self._seen_events) > 4000:
                self._seen_events.clear()
        phase = str(payload.get("phase") or "")
        name = str(payload.get("name") or "")
        args = payload.get("args") or {}
        if getattr(self.ui, "is_tui", False):
            self._tui_activity(payload, phase, name, args)
        flow = self.flow
        if flow is None:
            return
        if phase == "tool_start":
            self.reset_echo()
            flow.observe_tool_start(name, args)
            self._set_status(Status.EXECUTING, activity_for(name, args))
        elif phase == "tool_done":
            flow.observe_tool_done(
                name,
                bool(payload.get("ok")),
                str(payload.get("output") or ""),
                args,
            )
            self._set_status(Status.WORKING, "waiting for the model response")
        elif phase == "say":
            flow.observe_text(str(payload.get("text") or ""))

    def _tui_activity(
        self, payload: dict, phase: str, name: str, args: dict
    ) -> None:
        """Render one structured backend event as a persistent TUI message.

        The TUI has no transient step view, so this is its activity feed: every
        message comes from a real event the engine emitted. File changes are
        followed by the actual diff (collapsed to a bounded preview; Ctrl+E
        expands the last one).
        """
        append = getattr(self.ui, "append_activity", None)
        if not callable(append):
            return
        message = str(payload.get("message") or "")
        if phase == "task_started":
            self._turn_ops = self._fresh_turn()
            append("note", "Analyzing the task and inspecting the workspace...")
            self._set_status(Status.THINKING, "analyzing the task")
            return
        if phase == "terminal_output":
            # The command's live echo is already handled by on_output; the
            # structured event is for event consumers, not a second echo.
            return
        if phase == "say":
            text = str(payload.get("text") or "").strip()
            if text:
                append("note", text)
            return
        self._record_op(payload)
        if phase == "tool_start":
            self.reset_echo()
            append("action", activity_for(name, args))
            self._set_status(Status.EXECUTING, activity_for(name, args))
            return
        if phase == "tool_done":
            ok = bool(payload.get("ok"))
            # One operation, one line: a real file change is rendered by the
            # diff view (summary + accurate line numbers), so the generic
            # event message is not printed a second time on top of it.
            if ok and payload.get("diff"):
                append_change = getattr(self.ui, "append_change", None)
                if callable(append_change):
                    append_change(payload)
                else:
                    append("success", message or f"{name} finished")
            else:
                append("success" if ok else "error", message or f"{name} finished")
            self._set_status(Status.WORKING, "waiting for the model response")
            return
        if phase == "task_completed":
            append("success", "Task completed.")
            self._set_status(Status.COMPLETED, "")
            self._flush_report("completed")
            return
        if phase == "task_failed":
            append("error", message or "Task failed.")
            self._set_status(Status.ERROR, message or "task failed")
            self._flush_report("failed")


    # Kinds after which the right-side TASK PLAN panel is refreshed with the
    # graph's real snapshot — the same event the activity line uses.
    _PLAN_PUBLISH_KINDS = frozenset(
        {"plan_ready", "task_start", "task_done", "task_failed", "task_blocked",
         "task_skipped", "recovery"}
    )

    def _publish_plan_rows(self, graph, total: int | None) -> None:
        """Push the task graph's snapshot into the TUI's plan panel.

        ``None`` clears the panel (no active plan). Best-effort: a rendering
        problem never touches execution.
        """
        tui = getattr(self.ui, "tui", None)
        if tui is None or not hasattr(tui, "state"):
            return
        try:
            rows: list[tuple[str, str]] = []
            if total is not None and callable(getattr(graph, "checklist", None)):
                rows = list(graph.checklist(limit=12))
            tui.state.set_plan(rows)
        except Exception:  # pragma: no cover - display-only
            pass

    # --- live TODO/plan lines (TUI) -----------------------------------------
    def _tui_plan_update(self, kind: str, detail: str) -> None:
        """One real plan-state line in the persistent TUI feed.

        States come from the session's own events (the task graph is touched
        by the session before each announcement), so the visible checklist
        always reflects actual execution state, never an animation.
        """
        try:
            from .core import session as session_mod

            session = session_mod.current_session()
            graph = getattr(session, "graph", None)
            total = int(getattr(graph, "total", 0) or 0)
        except Exception:
            return
        if kind == "plan_ready":
            text = f"Plan: {total} task(s)" if total else "Plan ready"
            if detail:
                text = f"{text} — {str(detail).strip()[:120]}"
            self.ui.append_activity("action", text)
            self._publish_plan_rows(graph, total)
            return
        self._publish_plan_rows(
            graph, total if kind in self._PLAN_PUBLISH_KINDS else None
        )
        states = {
            "task_start": ("\u25cf", "action", "Working on"),
            "task_done": ("\u2713", "success", "Completed"),
            "task_failed": ("\u2717", "error", "Failed"),
            "task_blocked": ("\u2717", "error", "Blocked"),
            "task_skipped": ("\u2013", "note", "Skipped"),
            "recovery": ("\u25cf", "action", "Repairing"),
        }
        info = states.get(kind)
        if info is None or total <= 0:
            return
        mark, level, label = info
        step = getattr(graph, "checklist", None)
        index = ""
        if callable(step) and detail:
            try:
                rows = list(graph.checklist())
                for row_index, (_state, title) in enumerate(rows, start=1):
                    if str(title).strip() and str(title).strip() in str(detail):
                        index = f"[{row_index}/{len(rows)}] "
                        break
            except Exception:
                index = ""
        text = f"{mark} {label}: {(detail or '').strip()[:140]}"
        if index:
            text = f"{index}{text}"
        self.ui.append_activity(level, text.strip())

    # --- persistent session events (Code Mode) ------------------------------
    def on_session_event(self, kind: str, detail: str) -> None:
        """Render compact progress for the persistent session (v7.1.0).

        Only what the user needs: the plan, the task that just finished, the
        live action, a repair in progress, and the outcome. Raw model output is
        never dumped here.
        """
        from .core import session as session_mod

        # The persistent TUI gets its plan state as real feed lines (the
        # TODO view): one line per observed state change, never an animation.
        if getattr(self.ui, "is_tui", False):
            self._tui_plan_update(kind, detail)
        flow = self.flow
        session = session_mod.current_session()
        # Reflect the real session phase on the live header (TUI only).
        phase = {
            "plan": (Status.THINKING, "planning the work"),
            "plan_ready": (Status.WORKING, "plan ready"),
            "task_start": (Status.WORKING, detail),
            "verify": (Status.WORKING, "verifying acceptance criteria"),
            "recovery": (Status.WORKING, "diagnosing and repairing"),
            "reconnect": (Status.THINKING, "reconnecting to the provider"),
            "network_lost": (Status.ERROR, detail),
            "final_verify": (Status.EXECUTING, "final verification"),
            "task_blocked": (Status.ERROR, detail),
            "task_failed": (Status.ERROR, detail),
        }.get(kind)
        if phase is not None:
            self._set_status(*phase)
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
        elif kind == "task_skipped":
            # A skipped task is resolved, never verified work (v7.2.5).
            self.ui.dim(f"  – {detail}")
        elif kind == "final_verify":
            self.ui.dim("  final verification…")
            if flow is not None:
                flow.set_activity("Verifying the project (tests / build)")
        elif kind == "reconnect":
            # The connection dropped; the session is reconnecting and the task
            # is preserved rather than failed (v7.2.5).
            self.ui.warning(f"  {detail}")
            if flow is not None:
                flow.set_activity("Reconnecting to the provider")
        elif kind == "reconnected":
            self.ui.dim(f"  {detail}")
        elif kind == "network_lost":
            self.ui.warning(f"Network: {detail}")
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
    """The mode named in a task header (v9.2.0: one unified Agent Mode)."""
    return "Agent Mode"


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
    config = getattr(agent, "config", None)
    label = _task_mode_label(config) if config is not None else "Agent Mode"
    flow = TaskFlow.for_ui(ui, mode_label=label, task=text)
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
        # Surface the real reason (a network pause says so) but keep the
        # actionable instruction identical for every pause.
        reason = (session.reason or "").strip()
        if reason and "connection" in reason.lower():
            ui.warning(f"{reason}")
        else:
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
    skipped = graph.skipped_count()
    if skipped:
        # A skipped task is resolved, never counted as verified work.
        ui.dim(f"  • Skipped: {skipped} task(s) not needed")
    evidence = session.session_evidence
    changed = list(session.state.changed_files)
    if evidence.tests:
        # What matters is the final state: the last test run passed.
        passed = evidence.tests[-1].ok
        ui.dim(f"  ✓ Verification: {'accepted' if passed else 'tests NOT passing'}")
        ui.dim(f"  ✓ Tests: {'passed' if passed else 'FAILED'}")
    elif changed:
        # Completion is evidence-gated (a task never verifies with zero
        # observed work), so "accepted" here means files were really written.
        ui.dim(
            f"  ✓ Verification: accepted ({len(changed)} file(s) created/modified)"
        )
    else:
        ui.dim("  ✓ Verification: accepted (commands ran and succeeded)")
    if changed:
        shown = ", ".join(changed[-5:])
        ui.dim(f"  ✓ Files: {len(changed)} affected — {shown}")
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
    """Run one full coding-agent turn (tool loop) and render the final answer.

    The turn is shown as a compact live task flow whose step states follow real
    engine activity (see :mod:`seedcode.ui.tasks`) — never a fake progress bar.
    With an active project workspace the turn continues as the persistent
    plan-session loop (plan → implement → verify); without one it is still a
    full agent turn on the loose files around the prompt.

    Lifecycle note: every outcome — success, tool failure, provider error,
    Ctrl+C — ends by printing a status, finishing the task view and RETURNING.
    Nothing here may terminate the process; the surrounding ``task_span``
    guarantees the lifecycle returns to IDLE and the REPL keeps prompting, and
    the explicit task-flow finish line always says the CLI is ready for more.
    """
    if _codemode_enabled():
        _handle_codemode(ui, agent, history, text, presenter)
        return

    # v9.2.0: a fresh bounded thinking panel for every turn, collapsed only
    # when the parseable answer has been produced.
    _tui = getattr(ui, "tui", None)
    if _tui is not None:
        try:
            _tui.state.reset_thinking()
        except Exception:  # pragma: no cover - display-only
            pass

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
            with ui.thinking("Generating a response"):
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
        if _tui is not None:
            try:
                _tui.state.collapse_thinking()
            except Exception:  # pragma: no cover - display-only
                pass
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
    if _tui is not None:
        try:
            _tui.state.collapse_thinking()
        except Exception:  # pragma: no cover - display-only
            pass
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
    """Build an Agent Mode engine bound to the ACTIVE WORKSPACE and permissions.

    The workspace is the folder the user selected at startup (or via
    ``/workspace``), so every file operation the engine performs resolves
    against it and stays inside it unless the permission system explicitly
    authorizes an outside write.
    """
    permissions = PermissionManager(
        level=PermissionMode.parse(config.permission_mode),
        workspace=active_workspace(),
    )
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


# The process-wide dangerous-action gate (v9.1.1). It is created once, not
# once per engine: the agent engine is rebuilt whenever the permission level or
# workspace changes, and re-creating the gate there was resetting the user's
# "Allow" / "Allow All" answers mid-task — the repeated-permission-prompt bug.
# A grant now survives every tool call, every multi-step task and every engine
# rebuild, exactly as the session-scope answer promises.
_ACTION_GATE = None
_ACTION_GATE_LOCK = threading.Lock()


def _make_action_gate(ui: UI):
    """The session-scoped dangerous-action gate (created once per process).

    Answers stick for the rest of the session: ``Always Allow`` per category,
    and ``Allow All`` across every category. Nothing is persisted to disk, so
    a new process starts clean — the scope is deliberately session-only.
    """
    global _ACTION_GATE
    from .tools.permissions import ACTION_LABELS, ActionGate, ActionGrant

    def confirm(category: str, description: str) -> ActionGrant:
        label = ACTION_LABELS.get(category, category)
        answer = ui.confirm_tool_action(label, description)
        return {
            "y": ActionGrant.ONCE,
            "a": ActionGrant.ALWAYS,
            "A": ActionGrant.ALL,
        }.get(answer, ActionGrant.DENY)

    with _ACTION_GATE_LOCK:
        if _ACTION_GATE is None:
            _ACTION_GATE = ActionGate(confirm=confirm)
        return _ACTION_GATE


def reset_action_gate() -> None:
    """Forget all session grants (test isolation; also an explicit reset)."""
    global _ACTION_GATE
    with _ACTION_GATE_LOCK:
        _ACTION_GATE = None



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

    Only an explicit exit leaves here: a finished task (Code Mode, Agent
    Mode or a plain chat turn) returns to the prompt with
    ``Ready for next task.`` and the session keeps running.
    """
    ctx = CommandContext(ui=ui, config=config, engine=engine)
    ui.dim(INPUT_HINT)

    # The Agent engine is built lazily on the first agent-mode turn and
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
            intent = classify(text)
            # v9.2.0 unified engine: every user request — a coding task, a
            # research question, a plain conversation — is one agent turn.
            # The engine decides from the request (``Intent``) which tools it
            # needs; there is no chat/agent split to choose between.
            # Build (or rebuild) the Agent engine for the live config. A
            # rebuild is needed whenever the permission level or the bound
            # workspace changed since the engine was constructed.
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
            # Research requests fetch fresh sources first and prepend them to
            # the turn — the same behaviour the old chat path had.
            if intent.needs_internet:
                block = _retrieve_context(ui, intent, text)
                if block:
                    text = block + "\n\n" + text
            try:
                agent.set_intent(intent)
            except Exception:
                pass
            _handle_agent(ui, agent, history, text, presenter)


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


# ---------------------------------------------------------------------------
# Persistent TUI session (v9.1.0)
# ---------------------------------------------------------------------------


def _terminal_size() -> tuple[int, int]:
    """The real terminal size (80x24 when unknown, e.g. tests/CI)."""
    import os

    try:
        size = os.get_terminal_size()
        return max(20, int(size.columns)), max(6, int(size.lines))
    except Exception:
        return 80, 24


class _TuiController:
    """Drives the persistent session.

    A message runs its turn on a worker thread while the prompt_toolkit
    application keeps the screen, so the header stays fixed and output streams
    into the middle region. Commands return control for one dispatch (a selector
    or menu needs the terminal to itself), then the session resumes.
    """

    def __init__(self, ui, tui, config: AppConfig) -> None:
        self.ui = ui
        self.tui = tui
        self.config = config
        self.engine = ChatEngine(config, on_event=self._on_provider_event)
        self.history = HistoryStore(provider_id=config.provider)
        self.presenter = _TaskPresenter(ui)
        self.ctx = CommandContext(ui=ui, config=config, engine=self.engine)
        self.agent: AgentEngine | None = None
        self.agent_perm = config.permission_mode
        self.agent_codemode = _codemode_enabled()
        #: The workspace the live engine was built for. A `/workspace` change
        #: moves the process working directory, so the engine (whose permission
        #: manager scopes every file operation to one root) must be rebuilt —
        #: otherwise writes would keep resolving against the old folder.
        self.agent_workspace = str(Path.cwd())
        self._backend = config.provider
        #: Guards the agent engine pointer against a rebuild while a turn is
        #: reading it (turns run on worker threads).
        self._agent_lock = threading.RLock()
        tui.on_submit = self.submit
        # Commands run in place: no application exit, no rebuild.
        tui.on_command = self._inplace_command
        tui.on_cancel_init = self._cancel_initialization

    # --- provider/backend --------------------------------------------------
    def _on_provider_event(self, kind: str, detail: str) -> None:
        if kind == "switching_provider":
            self.ui.warning(f"Switching provider: {detail}")
        elif kind == "retry":
            self.ui.dim(f"  {detail}")
        elif kind == "provider_stay":
            # v9.2.0 provider faithfulness: the selected local provider was
            # NOT switched away from — say so instead of hiding it.
            self.ui.warning(f"  {detail}")

    def _rebuild_backend(self) -> None:
        """Rebuild the chat backend/history after a provider change."""
        self.engine = ChatEngine(self.config, on_event=self._on_provider_event)
        self.history = HistoryStore(provider_id=self.config.provider)
        self.ctx = CommandContext(ui=self.ui, config=self.config, engine=self.engine)
        with self._agent_lock:
            # A new backend means a new engine; drop the old one.
            self.agent = None
        self._backend = self.config.provider

    # --- one turn (worker thread) ------------------------------------------
    def submit(self, text: str) -> None:
        """Run one user turn to completion (never raises into the TUI)."""
        with lifecycle().task_span():
            # v9.2.0 unified engine: every request is one coding-agent turn;
            # the engine reads the request's intent itself.
            intent = classify(text)
            self._ensure_agent()
            if self.agent is not None:
                if intent.needs_internet:
                    block = _retrieve_context(self.ui, intent, text)
                    if block:
                        text = block + "\n\n" + text
                try:
                    self.agent.set_intent(intent)
                except Exception:
                    pass
                _handle_agent(self.ui, self.agent, self.history, text, self.presenter)
        try:
            self.tui.sync_from_config(self.config)
        except Exception:
            pass

    # --- in-place commands ---------------------------------------------------
    def _cancel_initialization(self) -> None:
        """Cancel a background workspace preparation (Ctrl+C, Esc, shutdown)."""
        self.tui.end_initialization()
        self.tui.state.set_status(Status.READY, "")
        self.ui.dim("(preparation cancelled — it will resume when needed)")

    def _switch_finished(self, _generation: int, _key: str, _result) -> None:
        """The background preparation completed (called on the worker)."""
        with self._agent_lock:
            self.agent = None  # rebuilt lazily from the now-final config
        self.tui.end_initialization()
        try:
            self.tui.sync_from_config(self.config)
        except Exception:
            pass

    def _switch_failed(self, _generation: int, _key: str, error: str) -> None:
        """A failed preparation is *reported*, never turned into a freeze.

        The terminal keeps running, the header keeps working, and Chat Mode is
        one command away — exactly the contract for a failure in a mode switch.
        """
        with self._agent_lock:
            self.agent = None
        self.tui.end_initialization()
        self.tui.state.set_status(Status.ERROR, "initialization failed")
        self.ui.error(f"[Agent Mode] Initialization failed: {error}")
        self.ui.dim("Chat Mode is unaffected — /chat on returns to it.")
        self.tui.sync_from_config(self.config)

    def _inplace_command(self, text: str) -> None:
        """Dispatch a command that does not change the mode, in place.

        The engine/agent are rebuilt only when the provider actually changed,
        and the header is refreshed from live config — a pure state transition
        with no application exit and no rebuild.
        """
        backend_before = self.config.provider
        try:
            dispatch(self.ctx, text)
        except (KeyboardInterrupt, EOFError):
            self.ui.dim("Cancelled.")
        except Exception as exc:  # a broken command must not kill the session
            _report_command_error(self.ui, exc)
        if self.config.provider != backend_before:
            self._rebuild_backend()
        self.tui.sync_from_config(self.config)

    def _ensure_agent(self) -> None:
        """Build the Agent engine for the live config (on the turn worker).

        Runs on the turn's own worker thread, so waiting for a rebuild costs
        the interface nothing, and one engine is never built underneath
        another: the whole rebuild happens under the agent lock.
        """
        codemode_now = _codemode_enabled()
        workspace_now = str(Path.cwd())
        with self._agent_lock:
            if (
                self.agent is None
                or self.agent_perm != self.config.permission_mode
                or self.agent_codemode != codemode_now
                or self.agent_workspace != workspace_now
            ):
                self.agent = _make_agent(self.ui, self.config, self.presenter)
                self.agent_perm = self.config.permission_mode
                self.agent_codemode = codemode_now
                self.agent_workspace = workspace_now

    # --- the session loop --------------------------------------------------
    def run(self) -> None:
        while True:
            kind, payload = self.tui.run_once()
            if kind == "exit":
                self._shutdown()
                _exit_application(self.ui, "tui exit")
                return
            if kind == "key":
                _run_key_action(
                    self.ui, self.ctx, self.config, _KEY_ACTIONS.get(payload or "", "")
                )
                self.tui.sync_from_config(self.config)
                continue
            if kind == "toolbar":
                # A header control was activated: run the real application
                # action (the selector owns the terminal for one step).
                self._toolbar_action(payload or "")
                self.tui.sync_from_config(self.config)
                continue
            if kind != "command" or not payload:
                continue
            backend_before = self.config.provider
            try:
                result = dispatch(self.ctx, payload)
            except (KeyboardInterrupt, EOFError):
                self.ui.dim("Cancelled.")
                continue
            except Exception as exc:  # a broken command must not kill the session
                parts = payload.split()
                _log.exception("command failed: %s", parts[0] if parts else payload)
                _report_command_error(self.ui, exc)
                continue
            if result.should_exit:
                if self._menu():
                    self._shutdown()
                    _exit_application(self.ui, "menu exit")
                    return
                continue
            if self.config.provider != backend_before:
                self._rebuild_backend()
            self.tui.sync_from_config(self.config)

    def _shutdown(self) -> None:
        """Release the session and stop the application."""
        self.tui.shutdown()

    def _toolbar_action(self, action: str) -> None:
        """Run a real action behind a header control (project/provider/model).

        The control is real UI, so the action is the same one the equivalent
        slash command performs: provider/model open their selectors and the
        workspace control opens the project selector. A provider change
        rebuilds the chat backend exactly as a mid-session switch does.
        """
        try:
            if action == "provider":
                select_provider(self.ui, self.config)
            elif action == "model":
                select_model(self.ui, self.config)
            elif action == "mode":
                # v9.2.0: the header's third control is the PROJECT picker.
                dispatch(self.ctx, "/workspace select")
        except (KeyboardInterrupt, EOFError):
            self.ui.dim("Cancelled.")
        except Exception as exc:  # a control must never crash the session
            _log.exception("toolbar action failed: %s", action)
            _report_command_error(self.ui, exc)
        if self.config.provider != self._backend:
            self._rebuild_backend()

    def _menu(self) -> bool:
        """The interactive main menu; returns True when the user quits."""
        while True:
            if self.config.provider != self._backend:
                self._rebuild_backend()
            try:
                choice = _main_menu(self.config)
            except (KeyboardInterrupt, EOFError):
                return True
            if choice == "chat":
                return False
            try:
                if choice == "provider":
                    select_provider(self.ui, self.config)
                elif choice == "apikey":
                    apikey_menu(self.ui, self.config)
                elif choice == "model":
                    select_model(self.ui, self.config)
                elif choice == "memory":
                    self._command("/workspace status")
                elif choice == "settings":
                    settings_menu(self.ui, self.config)
                elif choice == "theme":
                    pick_theme(self.ui, self.config)
                elif choice == "about":
                    show_about(self.ui, self.config)
                elif choice in ("exit", None):
                    return True
            except (KeyboardInterrupt, EOFError):
                self.ui.dim("Cancelled.")
            except Exception as exc:  # menu actions must never crash the app
                _log.exception("menu action failed: %s", choice)
                _report_command_error(self.ui, exc)
            self.tui.sync_from_config(self.config)

    def _command(self, text: str) -> None:
        try:
            dispatch(self.ctx, text)
        except Exception:
            _log.exception("menu command failed: %s", text)


def run_persistent(*, input_device=None, output_device=None) -> None:
    """Start the persistent three-region TUI (interactive consoles only).

    Guided setup runs *before* the TUI owns the screen, because its selectors
    need the terminal to themselves and its output must be visible.
    """
    from .ui.tui import ChatTUI, TuiUI
    from .utils.terminal_env import workspace_root

    config = load_config()
    sync_custom_providers(config)
    set_active_theme(config.theme)

    if not config.is_configured():
        setup_ui = UI()
        setup_ui.apply_theme(config.theme)
        if not _guided_setup(setup_ui, config):
            setup_ui.dim("Setup incomplete — a provider and a model are required.")
            return

    workspace = str(workspace_root())
    columns, rows = _terminal_size()
    state = AppState.from_config(config, workspace=workspace)
    tui = ChatTUI(
        state,
        workspace=workspace,
        width=columns,
        height=rows,
        input_device=input_device,
        output_device=output_device,
    )
    ui = TuiUI(tui)
    ui.apply_theme(config.theme)
    tui.banner_text()

    # Best-effort teardown for the one legitimate shutdown path.
    lifecycle().on_shutdown(_release_desktop_resources)

    _log.info(
        "persistent TUI started: provider=%s model=%s mode=%s",
        config.provider,
        config.model or "(none)",
        getattr(config, "mode", "chat"),
    )
    _TuiController(ui, tui, config).run()


def run(ui: UI) -> None:
    """Show the startup dashboard, drop straight into chat, then the menu.

    The dashboard renders exactly once at launch; the chat prompt follows
    immediately (after guided setup when nothing is configured yet). The
    interactive menu remains available via /exit for provider/model/settings.
    """
    config = load_config()
    # Register the saved custom providers before anything resolves a provider
    # id (v7.2.5): the registry must reflect config.json from the first turn.
    sync_custom_providers(config)
    set_active_theme(config.theme)
    ui.apply_theme(config.theme)
    ui.banner(config)
    _log.info(
        "started: provider=%s model=%s configured=%s",
        config.provider,
        config.model or "(none)",
        config.is_configured(),
    )

    def presenter_on_event(kind: str, detail: str) -> None:
        """Surface provider retry/failover on the plain chat prompt (v7.2.5)."""
        if kind == "switching_provider":
            ui.warning(f"Switching provider: {detail}")
        elif kind == "retry":
            ui.dim(f"  {detail}")

    active_backend = config.provider
    engine = ChatEngine(config, on_event=presenter_on_event)
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
                engine = ChatEngine(config, on_event=presenter_on_event)
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
            engine = ChatEngine(config, on_event=presenter_on_event)
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
            elif choice == "agent":
                _dispatch_command(ui, config, engine, "/workspace status")
            elif choice == "memory":
                _dispatch_command(ui, config, engine, "/workspace status")
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
