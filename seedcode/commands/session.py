"""/session, /pause, /resume, /stop — Code Mode session control (v7.1.0).

Long-running does not mean uncontrolled. This is the user's hand on the
steering wheel, plus the way to *see* what the agent actually did:

* ``/session`` — inspect the current (or last checkpointed) session: state,
  progress, calls, evidence, and one row per task with its state, verification
  result and execution record (files inspected/affected, commands, tests,
  retries). The details the live task view deliberately keeps off screen.
* ``/pause``   — ask the running session to stop at the next safe boundary
  (between tasks/cycles). State, plan, checkpoint and project files are kept.
* ``/resume``  — continue a paused (or interrupted) session from its
  checkpoint, restarting the task that was in flight instead of the project.
* ``/stop``    — safely terminate the autonomous session. Files and the
  checkpoint stay; nothing is closed and no application is touched.

None of them exits the CLI, and none of them closes an application.
"""

from __future__ import annotations

from . import CommandContext, CommandResult, command


def _session_state():
    """The live Code Mode session and its checkpoint, best-effort."""
    from ..core import session as session_mod

    return session_mod, session_mod.current_session()


@command("pause", "Pause the running Code Mode session. Usage: /pause")
def _pause(ctx: CommandContext, arg: str) -> CommandResult:
    session_mod, session = _session_state()
    if session is None:
        ctx.ui.dim("Nothing is running. A paused session can be continued with /resume.")
        return CommandResult()
    session_mod.pause(session)
    ctx.ui.warning("Pausing at the next safe point — plan and files are kept.")
    return CommandResult()


@command("resume", "Resume a paused Code Mode session. Usage: /resume")
def _resume(ctx: CommandContext, arg: str) -> CommandResult:
    from .. import app

    if not app._codemode_enabled():
        ctx.ui.warning("Code Mode is off — enable it with /codemode on.")
        return CommandResult()
    try:
        resumed = app.resume_codemode_session(ctx.ui, ctx.config)
    except (KeyboardInterrupt, EOFError):
        ctx.ui.dim("Cancelled.")
        return CommandResult()
    except Exception as exc:  # a broken resume must not kill the REPL
        ctx.ui.error(f"[Command Error] {exc}")
        return CommandResult()
    if not resumed:
        ctx.ui.dim("No paused Code Mode session to resume.")
    return CommandResult()


def _checkpoint() -> dict | None:
    """The persisted Code Mode checkpoint, when Code Mode is enabled."""
    try:
        from ..codemode_state import codemode_state

        state = codemode_state()
        if state.enabled and state.store is not None:
            return state.store.load_checkpoint()
    except Exception:
        pass
    return None


@command(
    "session",
    "Show Code Mode session state and per-task records. Usage: /session",
)
def _session_report(ctx: CommandContext, arg: str) -> CommandResult:
    """The detailed view: real session state plus each task's own record.

    Reads the live session when one is running, otherwise the checkpoint of the
    last one. Every value comes from the engine's observed evidence — a model's
    claim of success is never shown as a verification result.
    """
    from ..core import session as session_mod
    from ..core.tasks import TaskGraph
    from ..ui.layout import supports_unicode
    from ..ui.session_view import session_table, tasks_table

    live = session_mod.current_session()
    checkpoint = _checkpoint()

    graph = None
    if live is not None:
        graph = live.graph
    elif checkpoint:
        graph = TaskGraph.from_dict(checkpoint.get("plan") or {})

    if live is None and graph is None:
        ctx.ui.dim(
            "No Code Mode session to inspect — /codemode on to start one, or "
            "/resume to continue a stopped one."
        )
        return CommandResult()

    console = getattr(ctx.ui, "console", None)
    legacy = console is not None and not supports_unicode(console)
    # A checkpoint of the *running* session is the same plan twice: only show it
    # when nothing is live (i.e. the session was stopped or paused).
    ctx.ui.panel(
        session_table(live, checkpoint=None if live is not None else checkpoint,
                      legacy=legacy),
        title="Code Mode Session",
    )
    if graph is not None and graph.tasks:
        done, total = graph.progress()
        ctx.ui.panel(
            tasks_table(graph, legacy=legacy),
            title=f"Tasks — {done}/{total} verified",
        )
    return CommandResult()


@command("stop", "Stop the running Code Mode session. Usage: /stop")
def _stop(ctx: CommandContext, arg: str) -> CommandResult:
    session_mod, session = _session_state()
    if session is None:
        ctx.ui.dim("Nothing is running.")
        return CommandResult()
    session_mod.stop(session)
    ctx.ui.warning(
        "Stopping the session at the next safe point — completed work and the "
        "project files are kept."
    )
    return CommandResult()
