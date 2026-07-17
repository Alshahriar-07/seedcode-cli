"""Agent loop: lets the model act on the project through the tool engine.

Providers only stream text, so tool use is a text protocol: the model emits a
fenced block

    ```tool
    {"tool": "read_file", "args": {"path": "main.py"}}
    ```

The loop detects such blocks, executes the calls through the permission gate,
feeds the results back as a user-role message, and asks again — until the
model answers with no tool calls (the final response) or the step budget runs
out. Malformed calls are not fatal: the parse error is fed back so the model
can correct itself (retry), and consecutive failures are bounded.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable

from .chat import ChatEngine, ChatError, SYSTEM_PROMPT
from .models import AppConfig, Message
from ..tools import PermissionError_, PermissionManager, ToolError, get_tool, tool_manifest
from ..tools.base import ToolResult
from ..utils.logger import get_logger

_log = get_logger("agent")

# Hard bounds so a confused model can never loop forever.
MAX_STEPS = 15
_MAX_CONSECUTIVE_FAILURES = 3
_MAX_CALLS_PER_STEP = 8

_TOOL_BLOCK = re.compile(r"```tool\s*\n(.*?)```", re.DOTALL)

_AGENT_PROMPT_TEMPLATE = (
    SYSTEM_PROMPT
    + "\n\nYou are in AGENT MODE with access to the user's project at {workspace} "
    "(permission mode: {mode}). To use a tool, emit a fenced block exactly like:\n"
    '```tool\n{{"tool": "<name>", "args": {{...}}}}\n```\n'
    "Rules: any number of tool blocks per reply; results arrive in the next "
    "message as [TOOL RESULTS]; when the task is complete, reply WITHOUT tool "
    "blocks — that is your final answer. Never invent tool results. If a call "
    "fails, read the error and correct your next call.\n\n"
    "Available tools:\n{manifest}"
)


@dataclass(slots=True)
class ToolCall:
    """One parsed tool invocation from the model's reply."""

    tool: str
    args: dict
    error: str = ""  # parse/validation error, fed back to the model


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
        calls.append(ToolCall(data["tool"].strip().lower(), args))
    return calls


def strip_tool_blocks(text: str) -> str:
    """Reply text with the tool blocks removed (what the user should see)."""
    return _TOOL_BLOCK.sub("", text).strip()


class AgentEngine(ChatEngine):
    """ChatEngine that runs the detect → execute → validate → retry loop.

    ``on_event(kind, detail)`` reports progress ('call', 'result', 'error',
    'limit') so the UI can narrate tool activity without this module
    importing any UI code.
    """

    def __init__(
        self,
        config: AppConfig,
        permissions: PermissionManager,
        on_event: Callable[[str, str], None] | None = None,
    ) -> None:
        super().__init__(config)
        self.permissions = permissions
        self._on_event = on_event or (lambda kind, detail: None)
        self.messages[0] = Message(role="system", content=self._system_prompt())

    def _system_prompt(self) -> str:
        return _AGENT_PROMPT_TEMPLATE.format(
            workspace=self.permissions.workspace,
            mode=self.permissions.mode.label,
            manifest=tool_manifest(),
        )

    def refresh_system_prompt(self) -> None:
        """Re-render the system prompt (after a permission-mode change)."""
        self.messages[0] = Message(role="system", content=self._system_prompt())

    # --- one whole agent turn ------------------------------------------------
    def run_turn(self, user_text: str) -> str:
        """Run the full agent loop for one user request; returns final text.

        Raises :class:`ChatError` only when the provider itself fails; tool
        failures are fed back to the model as retryable results.
        """
        self.add_user(user_text)
        failures = 0

        for step in range(1, MAX_STEPS + 1):
            try:
                reply = "".join(self.stream_reply())
            except ChatError:
                self.drop_last_user()
                raise

            calls = parse_tool_calls(reply)
            if not calls:
                self.add_assistant(reply)
                return reply.strip()

            self.add_assistant(reply)
            shown = strip_tool_blocks(reply)
            if shown:
                self._on_event("say", shown)

            results, step_failed = self._execute_calls(calls[:_MAX_CALLS_PER_STEP])
            if len(calls) > _MAX_CALLS_PER_STEP:
                results.append(
                    f"(only the first {_MAX_CALLS_PER_STEP} tool calls were run; "
                    "issue the rest next step)"
                )

            failures = failures + 1 if step_failed else 0
            if failures >= _MAX_CONSECUTIVE_FAILURES:
                self._on_event("limit", "too many consecutive tool failures")
                results.append(
                    "[SYSTEM] Multiple consecutive steps failed. Stop calling tools "
                    "and summarise the problem for the user."
                )

            feedback = "[TOOL RESULTS]\n" + "\n\n".join(results)
            _log.info("agent step %d: %d call(s), failed=%s", step, len(calls), step_failed)
            self.add_user(feedback)

        self._on_event("limit", f"step budget ({MAX_STEPS}) reached")
        return (
            "I hit the agent step limit before finishing. Progress so far is "
            "applied; ask me to continue to keep going."
        )

    def _execute_calls(self, calls: list[ToolCall]) -> tuple[list[str], bool]:
        """Execute parsed calls; returns (results-for-model, any_failed)."""
        results: list[str] = []
        any_failed = False
        for call in calls:
            if call.error:
                any_failed = True
                results.append(f"[ERROR] {call.error}")
                self._on_event("error", call.error)
                continue
            label = f"{call.tool}({json.dumps(call.args, ensure_ascii=False)[:120]})"
            self._on_event("call", label)
            try:
                result = get_tool(call.tool).run(self.permissions, call.args)
            except (ToolError, PermissionError_) as exc:
                result = ToolResult(False, str(exc))
            except Exception as exc:  # a tool bug must not kill the loop
                _log.exception("tool crashed: %s", call.tool)
                result = ToolResult(False, f"Tool crashed: {exc}")
            if not result.ok:
                any_failed = True
            self._on_event("result" if result.ok else "error", result.output[:200])
            results.append(f"{call.tool} -> {result.for_model()}")
        return results, any_failed
