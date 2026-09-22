"""Agent loop: lets the model act on the project through the tool engine.

Two tool-calling paths share one loop:

* **Native** (preferred): providers that support function/tool calling get
  the registry's JSON schemas via ``stream_chat_with_tools`` and stream back
  :class:`ToolCallEvent`s. History records the calls structurally
  (``Message.tool_calls`` / role=="tool" results) so each provider can
  serialize them to its own wire format.
* **Text protocol** (fallback): the model emits a fenced block

      ```tool
      {"tool": "read_file", "args": {"path": "main.py"}}
      ```

  which the loop regex-parses; results are fed back as a ``[TOOL RESULTS]``
  user message. Used when the provider has no native support or its first
  native attempt fails (the session then downgrades once and stays there).

Either way the loop is: detect calls → execute through the permission gate →
feed results back → ask again, until the model answers with no tool calls
(the final response) or the step budget runs out. Malformed calls are not
fatal: the parse error is fed back so the model can correct itself, and
consecutive failures are bounded.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable

from .chat import ChatEngine, ChatError
from .identity import build_system_prompt
from .models import AppConfig, Message, ToolCallRecord, clean_values
from .project import detect_project
from .providers import provider_label
from .providers.base import TextDelta, ToolCallEvent, ToolSpec
from .tasks import TurnEvidence
from ..tools import (
    TOOL_REGISTRY,
    PermissionError_,
    PermissionManager,
    ToolError,
    get_tool,
    tool_manifest,
)
from ..tools.base import ToolResult, tool_specs
from ..utils.logger import get_logger
from ..utils.text import safe_text

_log = get_logger("agent")

# Hard bounds so a confused model can never loop forever. These bound ONE
# model/tool turn; they are not a task budget. A Code Mode task is driven by
# the persistent session loop (seedcode.core.session), which keeps issuing
# turns until the task verifies — so the Code Mode turn budget is generous by
# design and its exhaustion continues the task instead of ending it.
MAX_STEPS = 15
CODEMODE_MAX_STEPS = 60
_MAX_CONSECUTIVE_FAILURES = 3
_MAX_CALLS_PER_STEP = 8
# When the conversation grows past this, older turns are folded into one
# summary message so context stays bounded without losing the working state.
_HISTORY_COMPACT_AT = 60
_HISTORY_KEEP_RECENT = 30
_CONTINUE_NOTE = (
    "[SYSTEM] This turn reached its step budget. The task is NOT finished. "
    "The session will continue it in the next model call — summarise nothing "
    "and keep working when prompted."
)

_TOOL_BLOCK = re.compile(r"```tool\s*\n(.*?)```", re.DOTALL)

_AGENT_PREAMBLE = (
    "\n\nYou are in AGENT MODE with access to the user's project at {workspace} "
    "(permission mode: {mode})."
)

_CODEMODE_PREAMBLE = (
    "\n\nCODE MODE is ON: {workspace} is the project workspace and all work "
    "stays inside it. You are a persistent software-engineering agent, not a "
    "chat assistant: one model response NEVER completes a task. Work the "
    "professional loop — UNDERSTAND, PLAN, IMPLEMENT, RUN, VERIFY, DEBUG, "
    "IMPROVE, TEST, COMPLETE.\n"
    "1. The session hands you one task at a time with its acceptance "
    "criteria. A task is finished only when those criteria are satisfied by "
    "real evidence: files that exist, commands that really ran and exited 0, "
    "tests that passed, and no unresolved error. Saying \"done\" proves "
    "nothing; verify it, then finish the reply with 'TASK <id> COMPLETE'.\n"
    "2. If the criteria are not met yet, keep going in the SAME reply: "
    "inspect the code, make the change, run the command or test, read the "
    "result, fix what fails, re-run. If a turn ends before you finish, say "
    "'CONTINUE TASK <id>' and give the next concrete action — the session "
    "will call you again with the state rebuilt.\n"
    "3. Never rewrite a file you have not read; prefer targeted edits over "
    "recreating a project. Consult the project memory and index first "
    "(.seedcode/), and use search_text/find_files instead of reading "
    "everything.\n"
    "4. A tool result is part of the loop, not the end of it: read it, "
    "decide, act again. A failed command is information to fix, never a "
    "reason to stop. Do not ask for permission for normal coding work; ask "
    "only when genuinely blocked.\n"
    "5. When you learn something durable about this project "
    "(architecture, conventions, decisions), note it in .seedcode memory."
)

_TEXT_PROTOCOL_INSTRUCTIONS = (
    " To use a tool, emit a fenced block exactly like:\n"
    '```tool\n{{"tool": "<name>", "args": {{...}}}}\n```\n'
    "Rules: any number of tool blocks per reply; results arrive in the next "
    "message as [TOOL RESULTS]; when the task is complete, reply WITHOUT tool "
    "blocks — that is your final answer. Never invent tool results. If a call "
    "fails, read the error and correct your next call.\n\n"
    "Available tools:\n{manifest}"
)

_NATIVE_INSTRUCTIONS = (
    " Use the provided tools to inspect and change the project. Rules: tool "
    "results arrive as tool messages; when the task is complete, answer "
    "WITHOUT calling tools — that is your final answer. Never invent tool "
    "results. If a call fails, read the error and correct your next call. "
    "Prefer editing existing files over rewriting them."
)

_DESKTOP_PROMPT_HEADER = (
    "\n\nCOMPUTER ENGINE is available: you can control this computer, but you "
    "are the PLANNER, not the hands. A deterministic engine does the work; you "
    "only decide WHAT to do. Rules:\n"
    "1. You NEVER produce coordinates, keystrokes, click sequences, or wait "
    "loops. You choose a skill (computer_run) or a semantic UI action "
    "(ui_click, ui_type, …) and describe the target in words — the engine "
    "resolves it, executes it, verifies the result, and recovers from failures "
    "on its own.\n"
    "2. Prefer a named skill from the catalog below; it already knows the full "
    "procedure. Use the ui_* actions only for UI a skill doesn't cover.\n"
    "3. BROWSER WORK IS ALWAYS A SKILL, NEVER A CLICK. State the goal once — "
    "computer_run(youtube_play, {\"query\": \"Love Me Thoda Aur\"}) — and the "
    "engine performs the whole workflow: opens the page, dismisses cookie / "
    "translate / sign-in popups, selects the right result, starts playback, "
    "and verifies it. Do NOT search and then click a result, and do NOT use "
    "ui_click / ui_type on a browser window — those are refused. When no "
    "browser skill fits, use open_url with an address you construct.\n"
    "4. Read computer_state instead of re-inspecting the screen — the engine "
    "remembers the focused app, pointer, clipboard, terminal directory, "
    "current project, and recent actions for you.\n"
    "5. Each tool result already reflects VERIFIED reality (the engine checked "
    "before reporting). Trust it; never claim success it didn't confirm.\n"
    "6. Call the engine again only when it reports failure with a replan hint, "
    "or when computer_see shows unexpected UI. Some actions need the user's "
    "confirmation and may be denied — respect denials, do not retry them.\n\n"
    "Available skills:\n"
)


def _inspection_label(call: ToolCall) -> str:
    """A short label for what a read-only tool looked at (v7.1.0).

    A file path when the call names one, otherwise the query/pattern it
    searched for. Text is normalized by the caller's evidence recorder, so a
    lone surrogate cannot reach the checkpoint.
    """
    args = call.args or {}
    for key in ("path", "source", "file", "target"):
        value = args.get(key)
        if value:
            return safe_text(str(value))[:160]
    for key in ("pattern", "query", "command"):
        value = args.get(key)
        if value:
            return f"{call.tool}: {safe_text(str(value))[:120]}"
    return call.tool


def _SafeObserver(observer):
    """Wrap an ``on_event`` callback so its detail text is always encodable."""

    def wrapped(kind: str, detail: str) -> None:
        observer(kind, safe_text(detail))

    return wrapped


def _desktop_prompt_section(max_level) -> str:
    """The Computer Engine prompt block, including the live skill catalog."""
    try:
        from ..computer.skills import REGISTRY
        from ..computer import catalog as _catalog  # noqa: F401 — populate registry

        manifest = REGISTRY.manifest(max_level=max_level)
    except Exception:
        manifest = "(catalog unavailable)"
    return _DESKTOP_PROMPT_HEADER + (manifest or "(no skills at this level)")


@dataclass(slots=True)
class ToolCall:
    """One parsed tool invocation from the model's reply."""

    tool: str
    args: dict
    error: str = ""  # parse/validation error, fed back to the model
    call_id: str = ""  # provider call id ("" for text-protocol calls)


def parse_tool_calls(text: str) -> list[ToolCall]:
    """Extract tool calls from a model reply (malformed ones carry .error)."""
    calls: list[ToolCall] = []
    for match in _TOOL_BLOCK.finditer(text):
        raw = match.group(1).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            calls.append(ToolCall("", {}, error=f"Invalid JSON in tool block: {exc}"))
            continue
        if not isinstance(data, dict) or not isinstance(data.get("tool"), str):
            calls.append(
                ToolCall("", {}, error='Tool block must be {"tool": "<name>", "args": {...}}.')
            )
            continue
        args = data.get("args") or {}
        if not isinstance(args, dict):
            calls.append(ToolCall(data["tool"], {}, error='"args" must be a JSON object.'))
            continue
        # v7.1.0: arguments are normalized before execution, so a lone
        # surrogate in a model-supplied path or body can never reach a file
        # write, a shell command, or an HTTP request.
        calls.append(ToolCall(data["tool"].strip().lower(), clean_values(args)))
    return calls


def strip_tool_blocks(text: str) -> str:
    """Reply text with the tool blocks removed (what the user should see)."""
    return _TOOL_BLOCK.sub("", text).strip()


def _downgrade_for_text(messages: list[Message]) -> list[Message]:
    """Render native-era history into text-protocol form.

    Providers' plain ``stream_chat``/``to_api()`` never learned the tool
    roles, so before any text-protocol request: assistant ``tool_calls``
    become appended ```` ```tool ```` blocks, and consecutive role=="tool"
    results merge into one ``[TOOL RESULTS]`` user message.
    """
    out: list[Message] = []
    pending_results: list[str] = []

    def flush_results() -> None:
        if pending_results:
            out.append(
                Message(
                    role="user",
                    content="[TOOL RESULTS]\n" + "\n\n".join(pending_results),
                )
            )
            pending_results.clear()

    for message in messages:
        if message.role == "tool":
            pending_results.append(f"{message.tool_name} -> {message.content}")
            continue
        flush_results()
        if message.role == "assistant" and message.tool_calls:
            blocks = "\n".join(
                "```tool\n"
                + json.dumps(
                    {"tool": call.name, "args": call.arguments}, ensure_ascii=False
                )
                + "\n```"
                for call in message.tool_calls
            )
            content = (message.content + "\n" + blocks).strip()
            out.append(
                Message(role="assistant", content=content, images=message.images)
            )
        else:
            out.append(message)
    flush_results()
    return out


class AgentEngine(ChatEngine):
    """ChatEngine that runs the detect → execute → validate → retry loop.

    Two observers, both optional and UI-agnostic:

    * ``on_event(kind, detail)`` reports human-readable progress ('call',
      'result', 'error', 'limit') for narration;
    * ``on_step(payload)`` reports the SAME activity structurally —
      ``{"phase": "tool_start"|"tool_done"|"say", ...}`` — so a step-by-step
      task view can be driven by what actually happened instead of guesswork.
    """

    def __init__(
        self,
        config: AppConfig,
        permissions: PermissionManager,
        on_event: Callable[[str, str], None] | None = None,
        on_step: Callable[[dict[str, Any]], None] | None = None,
        max_steps: int | None = None,
    ) -> None:
        super().__init__(config)
        self.permissions = permissions
        # Narrated details come straight from model/tool text; normalize them at
        # the observer boundary so no embedder (UI, tests, log) can receive text
        # that fails to encode (v7.1.0).
        self._on_event = _SafeObserver(on_event) if on_event else (lambda k, d: None)
        self._on_step = on_step or (lambda payload: None)
        # Native tool calling: None = untried, False = fell back to the text
        # protocol for this session, True = at least one native step worked.
        self._native: bool | None = None
        # Evidence observed during the turn in flight (v7.1.0): the persistent
        # Code Mode session verifies tasks against this, never against the
        # model's own claim of success.
        self.last_evidence = TurnEvidence()
        # Code Mode gets a generous per-turn budget (the session continues the
        # task afterwards); callers may override it explicitly.
        if max_steps is not None:
            self.max_steps = max(1, int(max_steps))
        else:
            self.max_steps = CODEMODE_MAX_STEPS if self._codemode_active() else MAX_STEPS
        self.messages[0] = Message(role="system", content=self._system_prompt())

    def _system_prompt(self) -> str:
        desktop_section = ""
        if self._desktop_active():
            desktop_section = _desktop_prompt_section(self.permissions.level)

        # Build Seed Code identity + agent instructions
        base_identity = build_system_prompt(
            provider_label(self.config.provider),
            self.config.model or "unspecified"
        )
        preamble = _AGENT_PREAMBLE.format(
            workspace=self.permissions.workspace,
            mode=self.permissions.mode.label,
        )
        if self._codemode_active():
            # The behavioural instruction that makes the model a coding agent
            # (not just a chat model with tools) — previously defined but
            # never injected, so Code Mode lost its workflow guidance.
            preamble += _CODEMODE_PREAMBLE.format(
                workspace=self.permissions.workspace
            )
            preamble += self._codemode_context()
        preamble += self._terminal_context()
        if self._native_active():
            # The API carries the tool schemas; no manifest needed.
            instructions = _NATIVE_INSTRUCTIONS
        else:
            instructions = _TEXT_PROTOCOL_INSTRUCTIONS.format(
                manifest=tool_manifest(self._groups())
            )
        return (
            base_identity
            + preamble
            + instructions
            + desktop_section
            + self._project_context()
        )

    def _codemode_active(self) -> bool:
        """Code Mode is a session-level toggle read lazily (no import cycle)."""
        try:
            from ..codemode_state import codemode_state

            state = codemode_state()
            return bool(state.enabled and state.workspace is not None)
        except Exception:
            return False

    def _codemode_context(self) -> str:
        """Workspace/index/memory context for the coding agent prompt.

        Compact by design: the file map lives in .seedcode and is searched via
        tools; the prompt only carries orientation (top files, memories), so a
        big repository never floods the context window.
        """
        try:
            from ..codemode_state import codemode_state

            state = codemode_state()
            store = state.store
            if store is None:
                return ""
            sections: list[str] = []
            memories = store.list_memories()
            if memories:
                sections.append(
                    "Project memory notes available: " + ", ".join(memories[:8])
                )
            file_map = store.load_file_map()
            if file_map:
                top = sorted(file_map.items(), key=lambda kv: -kv[1].get("lines", 0))[:15]
                listed = ", ".join(rel for rel, _ in top)
                sections.append(
                    f"Indexed {len(file_map)} files (largest: {listed}). "
                    "Use find_files/search_text to locate code; read only what you need."
                )
            recent = store.latest_session_summaries(limit=2)
            if recent:
                goals = "; ".join(
                    str(s.get("goal", ""))[:80] for s in recent if s.get("goal")
                )
                if goals:
                    sections.append(f"Recent session goals: {goals}")
            if not sections:
                return ""
            return "\n\nPROJECT MEMORY & INDEX:\n" + "\n".join(
                f"- {s}" for s in sections
            )
        except Exception:
            _log.exception("codemode context failed")
            return ""

    def _terminal_context(self) -> str:
        """Where the agent is running: host + active shell.

        Telling the model the shell lets it emit POSIX or PowerShell syntax
        correctly instead of guessing from the platform. Best-effort and
        never fatal — an unknown host simply yields no extra text.
        """
        try:
            from ..utils.terminal_env import detect_terminal

            env = detect_terminal()
            shell = env.shell or "system default"
            return (
                f"\n\nTERMINAL: running inside {env.host} with {shell}. "
                "Use the run_command tool for builds/tests; pass its `shell` "
                "argument when a command needs POSIX (bash) or PowerShell "
                "syntax instead of the platform default."
            )
        except Exception:
            return ""

    def _project_context(self) -> str:
        """Ambient project summary; detection must never break construction."""
        try:
            info = detect_project(self.permissions.workspace)
            return f"\n\nPROJECT CONTEXT:\n{info.summary}" if info.summary else ""
        except Exception:
            _log.exception("project detection failed")
            return ""

    def _groups(self) -> tuple[str, ...]:
        return ("core", "desktop") if self._desktop_active() else ("core",)

    def _desktop_active(self) -> bool:
        """Desktop tools are advertised only when enabled AND runnable here."""
        desktop = self.permissions.desktop
        if desktop is None or not desktop.enabled:
            return False
        from ..computer import is_available

        return is_available()[0]

    def _native_active(self) -> bool:
        """Whether this session should use native tool calling right now."""
        if self._native is False:
            return False
        try:
            from .providers import get_provider

            return get_provider(self.config.provider).supports_tools(self.config)
        except Exception:
            return False

    def refresh_system_prompt(self) -> None:
        """Re-render the system prompt (after a permission-mode change)."""
        self.messages[0] = Message(role="system", content=self._system_prompt())

    # --- one whole agent turn ------------------------------------------------
    def run_turn(self, user_text: str) -> str:
        """Run the full agent loop for one user request; returns final text.

        Raises :class:`ChatError` only when the provider itself fails; tool
        failures are fed back to the model as retryable results.

        v7.1.0: :attr:`last_evidence` is reset here and filled from the tool
        activity of this turn, and exhausting the turn's step budget marks the
        evidence ``incomplete`` instead of ending a Code Mode task.
        """
        self.last_evidence = TurnEvidence()
        self.add_user(user_text)
        failures = 0

        step = 0
        while step < self.max_steps:
            step += 1
            if self._native_active():
                outcome, payload = self._native_step(failures)
                if outcome == "fallback":
                    # Downgrade once for the session and retry the SAME step
                    # through the text protocol.
                    _log.warning("native tool calling failed; falling back to text protocol")
                    self._native = False
                    self.refresh_system_prompt()
                    step -= 1
                    continue
            else:
                outcome, payload = self._text_step(failures)

            if outcome == "final":
                # v7.1.0: the final text is normalized before it leaves the
                # engine (a lone surrogate must never reach the console, the
                # history file, or a JSON request).
                return safe_text(payload) or "Done."
            failures = failures + 1 if outcome == "failed" else 0

        self._on_event("limit", f"step budget ({self.max_steps}) reached")
        self.last_evidence.incomplete = True
        if self._codemode_active():
            # Not a task outcome: the persistent session issues another call.
            self.last_evidence.note_error(_CONTINUE_NOTE)
            return (
                "This turn reached its step budget; the task continues in the "
                "next cycle with the state rebuilt."
            )
        return (
            "I hit the agent step limit before finishing. Progress so far is "
            "applied; ask me to continue to keep going."
        )

    # --- context management --------------------------------------------------
    def compact_history(
        self,
        *,
        compact_at: int | None = None,
        keep_recent: int = _HISTORY_KEEP_RECENT,
    ) -> bool:
        """Fold older history into one summary message; True when it compacted.

        Code Mode sessions run for many cycles, and unlimited raw history would
        grow the context window without limit. The *working state* is what the
        session rebuilds into each call, so raw history beyond the recent
        window is replaced by a short structural summary. The cut is always made
        at a safe boundary (never between an assistant's tool call and its tool
        results), so strict providers still receive paired messages.
        """
        threshold = compact_at or _HISTORY_COMPACT_AT
        if len(self.messages) <= threshold:
            return False

        system = self.messages[0] if self.messages and self.messages[0].role == "system" else None
        body = self.messages[1:] if system is not None else list(self.messages)
        keep = max(4, min(keep_recent, len(body)))
        cut = len(body) - keep
        # Never split a tool result from the assistant call that produced it.
        while cut < len(body) and body[cut].role == "tool":
            cut += 1
        if cut <= 0:
            return False

        dropped = body[:cut]
        kept = body[cut:]
        summary = self._history_summary(dropped)
        self.messages = ([system] if system is not None else []) + [
            Message(role="user", content=summary)
        ] + kept
        _log.info("compacted %d message(s) into a session summary", len(dropped))
        return True

    def _history_summary(self, dropped: list[Message]) -> str:
        """A compact, factual summary of the turns being dropped."""
        files: list[str] = []
        commands: list[str] = []
        for message in dropped:
            if message.role != "assistant" or not message.tool_calls:
                continue
            for call in message.tool_calls:
                args = call.arguments or {}
                path = str(args.get("path") or args.get("source") or "").strip()
                if path and path not in files:
                    files.append(path)
                command = str(args.get("command") or "").strip()
                if command and command not in commands:
                    commands.append(command)
        lines = [
            "[SESSION SUMMARY] Earlier turns were compacted to keep the context "
            "bounded. Nothing here replaces the acceptance criteria."
        ]
        if files:
            lines.append("Files touched: " + ", ".join(files[-20:]))
        if commands:
            lines.append("Commands already run: " + "; ".join(commands[-10:]))
        lines.append(f"Turns compacted: {len(dropped)}")
        return "\n".join(lines)

    # --- native path ---------------------------------------------------------
    def _native_step(self, failures: int) -> tuple[str, str | None]:
        """One step over the native tool-calling API.

        Returns ("final", text) | ("ok", None) | ("failed", None) |
        ("fallback", None).
        """
        specs = [ToolSpec(**s) for s in tool_specs(self._groups())]
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        try:
            for event in self.stream_reply_events(specs):
                if isinstance(event, TextDelta):
                    text_parts.append(event.text)
                elif isinstance(event, ToolCallEvent):
                    calls.append(
                        ToolCall(
                            tool=(event.name or "").strip().lower(),
                            args=clean_values(event.arguments),
                            error=event.error,
                            call_id=event.id,
                        )
                    )
        except ChatError as exc:
            if self._native is None:
                # Never succeeded natively — treat any failure on the first
                # attempt as "tools unsupported" and fall back gracefully.
                _log.info("first native step failed (%s)", exc)
                return ("fallback", None)
            self.drop_last_user()
            raise

        reply = "".join(text_parts)

        if not calls:
            # Belt-and-braces: some models emit the TEXT protocol even when
            # given native tools — honour it rather than ending the turn.
            text_calls = parse_tool_calls(reply)
            if not text_calls:
                self._native = True
                self.add_assistant(reply)
                final = reply.strip()
                return ("final", final if final else "Done.")
            self._native = True
            return self._execute_text_style(reply, text_calls, failures)

        self._native = True
        executed = calls[:_MAX_CALLS_PER_STEP]
        deferred = calls[_MAX_CALLS_PER_STEP:]

        self.messages.append(
            Message(
                role="assistant",
                content=reply,
                tool_calls=[
                    ToolCallRecord(id=c.call_id, name=c.tool, arguments=c.args)
                    for c in calls
                ],
            )
        )
        shown = reply.strip()
        if shown:
            self._on_event("say", shown)
            self._notify("say", text=shown)

        results, step_failed = self._execute_calls(executed)
        images = self._drain_images()
        for call, result_text in zip(executed, results):
            self.messages.append(
                Message(
                    role="tool",
                    content=result_text,
                    tool_call_id=call.call_id,
                    tool_name=call.tool,
                    images=images,
                )
            )
            images = []  # attach pending screenshots to the first result only
        # Every issued call id must be answered (strict APIs 400 otherwise).
        for call in deferred:
            self.messages.append(
                Message(
                    role="tool",
                    content=(
                        f"(not executed: only the first {_MAX_CALLS_PER_STEP} tool "
                        "calls run per step; issue this again next step)"
                    ),
                    tool_call_id=call.call_id,
                    tool_name=call.tool,
                )
            )

        if self._limit_reached(step_failed, failures):
            # Fold the nudge into the last tool result rather than adding a
            # user turn — strict APIs require every tool_use answered by
            # tool_results in one block, with no interleaved user text.
            last = self.messages[-1]
            last.content += (
                "\n\n[SYSTEM] Multiple consecutive steps failed. Stop calling "
                "tools and summarise the problem for the user."
            )
        self.compact_history()  # bounded context across many cycles
        _log.info("agent native step: %d call(s), failed=%s", len(calls), step_failed)
        return ("failed" if step_failed else "ok", None)

    # --- text-protocol path --------------------------------------------------
    def _text_step(self, failures: int) -> tuple[str, str | None]:
        """One step over the text protocol (fenced ```tool blocks)."""
        # Providers' plain path never learned the tool roles; render any
        # native-era messages into text form for this request.
        original = self.messages
        self.messages = _downgrade_for_text(original)
        try:
            reply = "".join(self.stream_reply())
        except ChatError:
            self.messages = original
            self.drop_last_user()
            raise
        self.messages = original

        calls = parse_tool_calls(reply)
        if not calls:
            self.add_assistant(reply)
            final = reply.strip()
            # Some models complete tool work without emitting a summary line.
            # Return a minimal acknowledgement so the UI never shows "(no response)".
            return ("final", final if final else "Done.")

        return self._execute_text_style(reply, calls, failures)

    def _execute_text_style(
        self, reply: str, calls: list[ToolCall], failures: int
    ) -> tuple[str, str | None]:
        """Execute calls and append text-protocol style feedback messages."""
        self.add_assistant(reply)
        shown = strip_tool_blocks(reply)
        if shown:
            self._on_event("say", shown)
            self._notify("say", text=shown)

        results, step_failed = self._execute_calls(calls[:_MAX_CALLS_PER_STEP])
        if len(calls) > _MAX_CALLS_PER_STEP:
            results.append(
                f"(only the first {_MAX_CALLS_PER_STEP} tool calls were run; "
                "issue the rest next step)"
            )

        if self._limit_reached(step_failed, failures):
            results.append(
                "[SYSTEM] Multiple consecutive steps failed. Stop calling tools "
                "and summarise the problem for the user."
            )

        feedback = "[TOOL RESULTS]\n" + "\n\n".join(results)
        _log.info("agent text step: %d call(s), failed=%s", len(calls), step_failed)
        self.messages.append(
            Message(role="user", content=feedback, images=self._drain_images())
        )
        self.compact_history()  # bounded context across many cycles
        return ("failed" if step_failed else "ok", None)

    def _record_evidence(self, call: ToolCall, result: ToolResult) -> None:
        """Record what a tool really did, for task verification (v7.1.0).

        Only observed facts go in: a mutating tool that succeeded names the file
        it changed, a read-only tool names what it inspected, ``run_command``
        records the command with its real exit status, and a failed call records
        the error. Verification and the persistent session state are built from
        these entries rather than from the model's description of them.
        """
        evidence = self.last_evidence
        evidence.tools += 1
        args = call.args or {}
        if call.tool == "run_command":
            evidence.note_command(str(args.get("command") or ""), result.ok, result.output)
        elif result.ok:
            tool = TOOL_REGISTRY.get(call.tool)
            if tool is not None and tool.mutates:
                for key in ("path", "source", "destination", "file"):
                    value = args.get(key)
                    if value:
                        evidence.note_file(str(value))
            else:
                # Read-only tool: this is the "files inspected" half of the
                # session context, so a resumed task knows what was looked at.
                evidence.note_inspected(_inspection_label(call))
        if not result.ok:
            evidence.note_error(f"{call.tool}: {result.output.splitlines()[0][:200]}")

    def _limit_reached(self, step_failed: bool, failures: int) -> bool:
        """True when this failure crosses the consecutive-failure bound."""
        if not step_failed:
            return False
        if failures + 1 >= _MAX_CONSECUTIVE_FAILURES:
            self._on_event("limit", "too many consecutive tool failures")
            return True
        return False

    def _drain_images(self) -> list[str]:
        """Pending desktop screenshots — attached only for vision providers.

        Screenshots are queued by desktop_see/desktop_screenshot; when the
        active provider cannot take images they are simply dropped (the UIA
        text snapshot in the tool result carries the information instead).
        """
        desktop = self.permissions.desktop
        if desktop is None or not desktop.pending_images:
            return []
        images = list(desktop.pending_images)
        desktop.pending_images.clear()
        try:
            from .providers import get_provider

            if get_provider(self.config.provider).supports_images(self.config):
                return images
        except Exception:  # provider lookup must never break the loop
            pass
        return []

    def _notify(self, phase: str, **fields: Any) -> None:
        """Report structured progress to the UI (a UI bug must never break a turn)."""
        try:
            self._on_step(
                {
                    key: (safe_text(value) if isinstance(value, str) else value)
                    for key, value in {"phase": phase, **fields}.items()
                }
            )
        except Exception:
            _log.exception("step observer failed")

    def _execute_one(self, call: ToolCall) -> ToolResult:
        """Run one tool call through the registry, converting failures."""
        try:
            return get_tool(call.tool).run(self.permissions, call.args)
        except (ToolError, PermissionError_) as exc:
            return ToolResult(False, str(exc))
        except Exception as exc:  # a tool bug must not kill the loop
            _log.exception("tool crashed: %s", call.tool)
            return ToolResult(False, f"Tool crashed: {exc}")

    def _execute_calls(self, calls: list[ToolCall]) -> tuple[list[str], bool]:
        """Execute parsed calls; returns (results-for-model, any_failed).

        v6.2.0: when EVERY call in the step is read-only (``mutates=False``),
        the calls run in a thread pool — reads touch different files and
        cannot conflict, so ordering cannot affect correctness. Any mutating
        call in the batch forces the whole step sequential: a read placed
        after a write in the same step may legitimately depend on it.
        """
        results: list[str] = []
        any_failed = False

        # Narrate every call up-front (in issue order) so the transcript reads
        # the same whether execution was parallel or not.
        runnable: list[tuple[int, ToolCall]] = []
        for index, call in enumerate(calls):
            if call.error:
                continue
            label = f"{call.tool}({json.dumps(call.args, ensure_ascii=False)[:120]})"
            self._on_event("call", label)
            self._notify("tool_start", name=call.tool, args=call.args)
            runnable.append((index, call))

        outcomes: dict[int, ToolResult] = {}
        parallel = (
            len(runnable) >= 2
            and all(
                (tool := TOOL_REGISTRY.get(call.tool)) is not None
                and not tool.mutates
                for _, call in runnable
            )
        )
        if parallel:
            with ThreadPoolExecutor(max_workers=min(4, len(runnable))) as pool:
                futures = {
                    index: pool.submit(self._execute_one, call)
                    for index, call in runnable
                }
                for index, future in futures.items():
                    outcomes[index] = future.result()
        else:
            for index, call in runnable:
                outcomes[index] = self._execute_one(call)

        for index, call in enumerate(calls):
            if call.error:
                any_failed = True
                self.last_evidence.note_error(call.error)
                results.append(f"[ERROR] {call.error}")
                self._on_event("error", call.error)
                self._notify(
                    "tool_done", name=call.tool, args=call.args, ok=False,
                    output=call.error,
                )
                continue
            result = outcomes[index]
            if not result.ok:
                any_failed = True
            self._record_evidence(call, result)
            self._on_event("result" if result.ok else "error", result.output[:200])
            self._notify(
                "tool_done", name=call.tool, args=call.args, ok=result.ok,
                output=result.output,
            )
            results.append(f"{call.tool} -> {result.for_model()}")
        return results, any_failed
