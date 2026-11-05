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

import ast
import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable

from . import events as event_stream
from .agents import AgentProfile, AgentRegistry, DEFAULT_AGENT_ID
from .calls import CallTracker, ToolCallState
from ..tools.subagent import on_delegate as _child_delegate
from ..tools.subagent import register_delegate
from . import instructions as instruction_engine
from .chat import ChatEngine, ChatError
from .identity import build_system_prompt
from .intent import Intent, IntentKind, classify
from .models import AppConfig, Message, ToolCallRecord, clean_values
from .model_profiles import profile_for
from .project import detect_project
from .providers import provider_label
from .skills import skill_catalog
from .task_state import TaskStateStore
from .providers.base import (
    FinishEvent,
    ProviderError,
    TextDelta,
    ThinkingDelta,
    ToolCallEvent,
    ToolSpec,
)
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
from ..tools.hooks import MANAGER as tool_hooks, ToolHookResult
from ..utils.logger import get_logger
from ..utils.text import safe_text

_log = get_logger("agent")

# Hard bounds so a confused model can never loop forever. These bound ONE
# model/tool turn; they are not a task budget. A Code Mode task is driven by
# the persistent session loop (seedcode.core.session), which keeps issuing
# turns until the task verifies — so the Code Mode turn budget is generous by
# design and its exhaustion continues the task instead of ending it.
MAX_STEPS = 25
CODEMODE_MAX_STEPS = 60
_MAX_CONSECUTIVE_FAILURES = 3
_MAX_CALLS_PER_STEP = 8

# v10.2.0: a response that stopped at the output-token ceiling is continued,
# never presented as a completed answer. The prompt is explicit that the
# prefix is preserved so the continuation cannot restart or repeat it.
_CONTINUATION_PROMPT = (
    "[SYSTEM] Your previous response was cut off by the output-token limit. "
    "Continue exactly where it stopped. Do not repeat anything already written "
    "and do not restart the task. If a tool call was incomplete, emit the whole "
    "call again as complete JSON. Keep working until the task is verified."
)
# v10.2.0: a completion claim is not proof. If a tool failed during this turn,
# the backend asks for one verification step instead of accepting the claim.
_VERIFY_PROMPT = (
    "[SYSTEM] A tool call failed during this turn. Do not report the task as "
    "complete yet. Inspect the last failure, fix the cause or verify the "
    "current state with a tool, then continue. If the task genuinely cannot be "
    "completed, report the exact blocker instead of claiming success."
)
_COMPLETION_CLAIM = re.compile(
    r"\b(complete|completed|completion|done|finished|successful|succeeded)\b",
    re.IGNORECASE,
)
# A small model can describe the change it *would* make without calling a
# file tool. One bounded correction asks it to apply the work; the task is
# never silently accepted as complete on the strength of prose.
_USE_TOOLS_PROMPT = (
    "[SYSTEM] This request changes the workspace, but no file tool has run: "
    "code or a claim in the reply is not a file modification. Inspect the "
    "relevant files, apply the change with the file tools "
    "(create_file/write_file/edit_file), then verify by reading the result or "
    "running the relevant tests. Only then answer. Never narrate a tool action "
    "('I will call read_file') — emit the call in the same reply; a described "
    "call is not an executed one. Use the text protocol exactly, one call per "
    "fenced block:\n"
    "```tool\n"
    '{"tool": "write_file", "args": {"path": "...", "content": "..."}}\n'
    "```\n"
    "If the user explicitly asked to see code without editing, say so and "
    "provide it."
)
_NO_CHANGES_NOTE = (
    "[No workspace changes were applied: no file tool ran successfully. "
    "The text below describes intended work, not completed work.]\n\n"
)
_MODIFY_REQUEST = re.compile(
    r"\b(build|create|implement|add|update|modify|change|fix|refactor|rename|"
    r"delete|remove|write|patch|migrate|install)\b",
    re.IGNORECASE,
)
_SHOW_ONLY = re.compile(
    r"\b(show|example|snippet|explain|walk me through|don't (?:edit|change|"
    r"modify|write)|without (?:editing|changing|modifying))\b",
    re.IGNORECASE,
)
_CODE_FENCE = re.compile(r"```([\w.+-]*)[ \t]*\r?\n(.*?)```", re.S)
# Fragments that are not source code: a plan is not an unapplied edit, and
# plain-text/diff/log blocks are descriptions rather than files.
_NON_CODE_LANGS = frozenset({"plan", "text", "txt", "diff", "log"})
# Three identical call/result cycles changes nothing: stop with an honest
# report instead of spending the rest of the step budget in a loop.
_MAX_REPEATED_STEPS = 3

# When the conversation grows past this, older turns are folded into one
# summary message so context stays bounded without losing the working state.
_HISTORY_COMPACT_AT = 60
_HISTORY_KEEP_RECENT = 30
# Token-aware guard: even a short transcript can overflow when tool results
# are large, so compaction also triggers on estimated context pressure.
_DEFAULT_CONTEXT_TOKENS = 128_000
_CONTEXT_COMPACT_RATIO = 0.75
_TOKEN_PRESSURE_KEEP = 8
_TOOL_RESULT_TRIM_CHARS = 4_000
_CONTINUE_NOTE = (
    "[SYSTEM] This turn reached its step budget. The task is NOT finished. "
    "The session will continue it in the next model call — summarise nothing "
    "and keep working when prompted."
)

# User plugins (custom tools + tool hooks) load once per process, at the
# first engine construction, with the workspace they serve. Contained: a
# broken plugin is logged and skipped, never fatal (see tools.hooks).
_PLUGINS_LOADED = False


def _load_plugins_once(workspace) -> None:
    global _PLUGINS_LOADED
    if _PLUGINS_LOADED:
        return
    from ..tools.hooks import load_plugins

    try:
        loaded = load_plugins(workspace)
    except Exception:
        _log.exception("plugin discovery failed")
        loaded = []
    if loaded:
        _log.info("loaded %d user plugin(s): %s", len(loaded), loaded)
    _PLUGINS_LOADED = True


_TOOL_BLOCK = re.compile(r"```tool\s*\n(.*?)```", re.DOTALL)
#: A JSON block/object that uses the OpenAI-style ``name`` + ``arguments``
#: shape. Some local models emit such a call as *text* even when native tools
#: are offered; executing it is the difference between a real tool loop and a
#: reply that merely describes the operation.
_INLINE_JSON_BLOCK = re.compile(r"```(?:json)?\s*([\[{].*?[\]}])\s*```", re.DOTALL)
#: v10.2.0's explicit text tool protocol. A local model that cannot use the
#: native channel emits ``TOOL_CALL: {json}``; unlike arbitrary JSON in prose,
#: this marker is an unambiguous request to execute a real tool.
_TOOL_CALL_MARKER = re.compile(r"(?m)^[ \t]*TOOL_CALL[ \t]*:[ \t]*")
#: LFM / Liquid tool-call tags. The body is normally a pythonic call
#: (``[read_file(path="README.md")]``) but some models put JSON inside.
_TOOL_TAG = re.compile(r"<\|tool_call_start\|>(.*?)<\|tool_call_end\|>", re.S | re.I)
#: Pythonic calls: ``[read_file(path="README.md")]``. Bare ``tool(...)`` is
#: accepted only inside a tool tag or when it is the whole reply, so a code
#: example in prose is never executed.
_PYTHON_CALL = re.compile(r"\[\s*([A-Za-z_]\w*)\s*\(([^()]*)\)\s*\]", re.S)
_PYTHON_BARE_CALL = re.compile(
    r"^\s*([A-Za-z_]\w*)\s*\(([^()]*)\)\s*$", re.S
)

#: A local model that narrates the call instead of emitting it ("I will call
#: list_dir") gets exactly one bounded correction per turn. Without it the turn
#: ends as a prose answer and the tool never runs — the reported failure.
_TOOL_MENTION_PROMPT = (
    "[SYSTEM] You described a tool action but no executable tool call was "
    "received. Do not narrate the action — perform it now in the SAME reply. "
    "Emit one call in this exact form and wait for the result:\n"
    "```tool\n"
    '{"tool": "<name>", "args": {...}}\n'
    "```\n"
    "If you are only answering a question, reply without naming a tool."
)
_TOOL_MENTION_VERBS = re.compile(
    r"\b(?:call|calling|use|using|run|running|invoke|invoking|execute|"
    r"executing|read|reading|list|listing|search|searching|inspect|"
    r"inspecting|check|checking|open|opening|create|creating|write|writing|"
    r"edit|editing|apply|applying|make|making)\b",
    re.IGNORECASE,
)
_TOOL_MENTION_INTENT = re.compile(
    r"\b(?:i(?:'ll| will| am going to|'m going to)|let me|we(?:'ll| will)|"
    r"i (?:need|want) to|next,? i(?:'ll| will)?)\b",
    re.IGNORECASE,
)

_AGENT_PREAMBLE = (
    "\n\nYou are in AGENT MODE with access to the user's project at {workspace} "
    "(permission mode: {mode})."
)

# Injected for a conversational request so an Agent turn does not reach for
# tools (or the workspace) simply because they are available.
_CONVERSATION_PREAMBLE = (
    "\n\nCONVERSATIONAL REQUEST: the user is asking a question, not posing a "
    "task. Answer directly and concisely from your own knowledge. Do NOT read "
    "the workspace, do NOT run commands, and do NOT call a tool unless the "
    "request explicitly names something that must be read or changed. "
    "Using a tool you do not need is a failure, not thoroughness."
)

# Injected for a browser/desktop request so the agent never conflates it with
# a coding task (and never scans the project to fulfil it).
_COMPUTER_PREAMBLE = (
    "\n\nCOMPUTER TASK: this request is about acting on the user's computer or "
    "browser, not about their project. Do NOT read, search or modify workspace "
    "files, and do NOT run project commands for it. Use only the computer / "
    "browser capability required, then report what actually happened."
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
    "5. REAL FILES OR IT DID NOT HAPPEN: code belongs in files. Create or "
    "change any source code by calling write_file/create_file/edit_file/"
    "insert_in_file — printing a code block in your reply is NOT the "
    "operation, and a described or proposed tool call is not an executed "
    "one: the operation counts only when a tool result says it succeeded. "
    "Before editing an existing file, locate and read it, then make the "
    "minimal targeted change. After a file operation, verify (file exists, "
    "content matches, command passes) BEFORE reporting success, and report "
    "failures honestly if recovery fails. The chat is for explanations, "
    "questions, updates and short summaries — never the destination for complete "
    "source files (show code in chat only when the user asked for a snippet).\n"
    "6. When you learn something durable about this project "
    "(architecture, conventions, decisions), note it in .seedcode memory."
)

_TEXT_PROTOCOL_INSTRUCTIONS = (
    " To use a tool, emit a fenced block exactly like:\n"
    '```tool\n{{"tool": "<name>", "args": {{...}}}}\n```\n'
    "Rules: any number of tool blocks per reply; results arrive in the next "
    "message as [TOOL RESULTS]; when the task is complete, reply WITHOUT tool "
    "blocks — that is your final answer. Never narrate a tool action ('I will "
    "call list_dir', 'let me read the file'): emit the call in the SAME reply. "
    "A described call is not an executed one. Never invent tool results. If a "
    "call fails, read the error and correct your next call.\n\n"
    "Available tools:\n{manifest}"
)

_COMPACT_NATIVE_INSTRUCTIONS = (
    " Use the provided tools. Read the result of every call before the next "
    "action. Never narrate a call — call the tool now. Never invent a result. "
    "When the task is verified, answer without calling a tool."
)

_COMPACT_TEXT_INSTRUCTIONS = (
    "\n\nTo call a tool, write one line per call (complete JSON):\n"
    'TOOL_CALL: {{"name": "<tool>", "arguments": {{...}}}}\n'
    "Results arrive in the next message. When the task is verified, reply "
    "without a TOOL_CALL line. Never narrate a call — emit it now. Never "
    "invent a tool result.\n\n"
    "Tools:\n{manifest}"
)

_NATIVE_INSTRUCTIONS = (
    " Use the provided tools to inspect and change the project. Rules: tool "
    "results arrive as tool messages; when the task is complete, answer "
    "WITHOUT calling tools — that is your final answer. Never narrate a tool "
    "action ('I will call list_dir'); call the tool in the SAME reply. A "
    "described call is not an executed one. Never invent tool results. If a "
    "call fails, read the error and correct your next call. Prefer editing "
    "existing files over rewriting them. Code belongs in files: create or "
    "change source code with the file tools — printing a code block in your "
    "reply is not a file operation and never counts as implemented."
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


@dataclass(slots=True)
class ToolCall:
    """One parsed tool invocation from the model's reply."""

    tool: str
    args: dict
    error: str = ""  # parse/validation error, fed back to the model
    call_id: str = ""  # provider call id ("" for text-protocol calls)


def _payload_calls(data: Any) -> list[ToolCall]:
    """Tool calls from one decoded JSON payload (object or list).

    Accepts the shapes local models actually emit: the text protocol
    (``tool``/``args``), OpenAI tool calls (``name``/``arguments``) and a flat
    action object (``{"action": "write_file", "path": ...}``).
    """
    entries = data if isinstance(data, list) else [data]
    calls: list[ToolCall] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name") or entry.get("tool") or entry.get("action")
        args = entry.get("arguments")
        if args is None:
            args = entry.get("args", entry.get("parameters", {}))
        if not isinstance(args, dict):
            args = {}
        if (
            not args
            and entry.get("action")
            and not entry.get("name")
            and not entry.get("tool")
        ):
            args = {
                key: value
                for key, value in entry.items()
                if key not in ("action", "name", "tool", "arguments", "args", "parameters")
            }
        if isinstance(name, str) and name.strip() and isinstance(args, dict):
            calls.append(ToolCall(name.strip().lower(), clean_values(args)))
    return calls


def _inside_fence(text: str, index: int) -> bool:
    """Whether ``index`` falls inside a triple-backtick fence.

    A ``TOOL_CALL`` written inside a documentation/code fence is an example,
    not an instruction to run the tool; only an unfenced marker is executable.
    """
    return (text or "").count("```", 0, index) % 2 == 1


def _marker_calls(text: str) -> list[ToolCall]:
    """Parse ``TOOL_CALL: {...}`` payloads (v10.2.0 text protocol).

    The JSON is extracted with :meth:`json.JSONDecoder.raw_decode`, so a
    multi-line object is read whole and a truncated payload is reported as a
    malformed call — it is never executed.
    """
    calls: list[ToolCall] = []
    decoder = json.JSONDecoder()
    for match in _TOOL_CALL_MARKER.finditer(text or ""):
        if _inside_fence(text or "", match.start()):
            continue
        rest = (text or "")[match.end():].lstrip()
        if rest.startswith("```"):
            rest = rest[3:]
            newline = rest.find("\n")
            rest = rest[newline + 1:] if newline != -1 else rest
        if not rest:
            calls.append(ToolCall("", {}, error="TOOL_CALL marker has no JSON payload."))
            continue
        try:
            data, _end = decoder.raw_decode(rest)
        except ValueError as exc:
            calls.append(
                ToolCall("", {}, error=f"Invalid JSON after TOOL_CALL: {exc}")
            )
            continue
        parsed = _payload_calls(data)
        if not parsed:
            calls.append(
                ToolCall(
                    "",
                    {},
                    error='TOOL_CALL needs {"name": "<tool>", "arguments": {...}}.',
                )
            )
        calls.extend(parsed)
    return calls


def _parse_python_call(name: str, body: str) -> ToolCall | None:
    """One pythonic ``name(key=value, ...)`` call, when the tool is real.

    Only literal keyword/positional arguments are accepted; anything that
    would need evaluation (a variable, a call, an f-string) is rejected so a
    model can never smuggle code into a tool argument through this parser.
    """
    name = (name or "").strip().lower()
    if name not in TOOL_REGISTRY:
        return None
    try:
        node = ast.parse(f"{name}({body})", mode="eval").body
    except (SyntaxError, ValueError):
        return None
    if not isinstance(node, ast.Call):
        return None
    args: dict[str, Any] = {}
    tool = TOOL_REGISTRY.get(name)
    required = [
        arg
        for arg, description in (tool.args.items() if tool is not None else ())
        if "(optional)" not in description
    ]
    for index, value in enumerate(node.args):
        if index >= len(required):
            return None
        try:
            args[required[index]] = ast.literal_eval(value)
        except (ValueError, TypeError, SyntaxError):
            return None
    for keyword in node.keywords:
        if not keyword.arg:
            return None
        try:
            args[keyword.arg] = ast.literal_eval(keyword.value)
        except (ValueError, TypeError, SyntaxError):
            return None
    return ToolCall(name, clean_values(args))


def _python_calls(text: str) -> list[ToolCall]:
    """Parse ``[tool(...)]`` calls; a code fence is an example, never a call."""
    source = text or ""
    calls: list[ToolCall] = []
    for match in _PYTHON_CALL.finditer(source):
        if _inside_fence(source, match.start()):
            continue
        call = _parse_python_call(match.group(1), match.group(2))
        if call is not None:
            calls.append(call)
    return calls


def _tag_calls(text: str) -> list[ToolCall]:
    """Parse ``<|tool_call_start|>…<|tool_call_end|>`` payloads.

    The payload is JSON (OpenAI style or the text protocol) or a single
    pythonic call, with or without square brackets.
    """
    calls: list[ToolCall] = []
    for match in _TOOL_TAG.finditer(text or ""):
        body = (match.group(1) or "").strip()
        if not body:
            continue
        try:
            calls.extend(_payload_calls(json.loads(body)))
            continue
        except (TypeError, ValueError):
            pass
        pythonic = _PYTHON_CALL.fullmatch(body) or _PYTHON_BARE_CALL.fullmatch(body)
        if pythonic is not None:
            call = _parse_python_call(pythonic.group(1), pythonic.group(2))
            if call is not None:
                calls.append(call)
    return calls


def _dedupe_calls(calls: list[ToolCall]) -> list[ToolCall]:
    """Drop identical calls repeated inside one reply (never across replies)."""
    seen: set[str] = set()
    unique: list[ToolCall] = []
    for call in calls:
        key = json.dumps(
            {"tool": call.tool, "args": call.args, "error": call.error},
            sort_keys=True,
            default=str,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(call)
    return unique


def parse_tool_calls(text: str) -> list[ToolCall]:
    """Extract tool calls from a model reply (malformed ones carry .error).

    Protocols understood, in order: the fenced ```` ```tool ```` block, the
    v10.2.0 ``TOOL_CALL: {json}`` marker, LFM/Liquid
    ``<|tool_call_start|>…<|tool_call_end|>`` tags, bracketed pythonic calls
    ``[read_file(path="README.md")]``, and a reply that is only one bare
    ``read_file(path="…")`` call. Only an explicit marker/tag/fence (or that
    whole-reply pythonic form) is executable — arbitrary JSON or a code example
    inside prose is not.
    """
    calls = _marker_calls(text)
    calls.extend(_tag_calls(text))
    calls.extend(_python_calls(text))
    bare = _PYTHON_BARE_CALL.fullmatch((text or "").strip())
    if bare is not None and not _inside_fence(text or "", 0):
        call = _parse_python_call(bare.group(1), bare.group(2))
        if call is not None:
            calls.append(call)
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
    return _dedupe_calls(calls)


def parse_inline_tool_calls(text: str) -> list[ToolCall]:
    """Parse OpenAI-style ``{"name": ..., "arguments": {...}}`` text calls.

    Only an ENTIRE reply that is JSON, or a fenced JSON block inside it, is
    considered — a JSON example in the middle of prose is never executed. Both
    a single object and a list of calls are accepted, as are the ``tool`` /
    ``args`` aliases, so a model that cannot use the native channel still gets
    a real tool loop instead of printing the call as its answer.
    """
    candidates: list[str] = []
    stripped = (text or "").strip()
    if stripped.startswith(("{", "[")):
        candidates.append(stripped)
    candidates.extend(_INLINE_JSON_BLOCK.findall(stripped))
    calls: list[ToolCall] = []
    for raw in candidates:
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            continue
        calls.extend(_payload_calls(data))
    return _dedupe_calls(calls)


def _strip_python_calls(text: str) -> str:
    """Remove executable ``[tool(...)]`` calls, preserving code examples."""
    pieces: list[str] = []
    last = 0
    for match in _PYTHON_CALL.finditer(text or ""):
        if _inside_fence(text or "", match.start()):
            continue
        if _parse_python_call(match.group(1), match.group(2)) is None:
            continue
        pieces.append(text[last:match.start()])
        last = match.end()
    pieces.append((text or "")[last:])
    return "".join(pieces)


def strip_tool_blocks(text: str) -> str:
    """Reply text with every executable tool call removed.

    This is what the user sees. It covers the fenced block, the v10.2.0
    ``TOOL_CALL:`` marker (JSON payload included), LFM tool tags and executable
    pythonic calls, and nothing else: prose, documentation and code examples are
    preserved.
    """
    source = _TOOL_TAG.sub("", text or "")
    decoder = json.JSONDecoder()
    pieces: list[str] = []
    last = 0
    for match in _TOOL_CALL_MARKER.finditer(source):
        if _inside_fence(source, match.start()):
            continue
        pieces.append(source[last:match.start()])
        rest = source[match.end():].lstrip()
        start = match.end() + (len(source[match.end():]) - len(rest))
        if rest.startswith("```"):
            rest = rest[3:]
            newline = rest.find("\n")
            if newline != -1:
                rest = rest[newline + 1:]
                start = source.find(rest, start) if rest else start
        try:
            _data, end = decoder.raw_decode(rest)
        except ValueError:
            # Malformed payload: remove the marker line only. The malformed
            # JSON is never executed, and the agent reports it to the model.
            newline = source.find("\n", match.end())
            last = len(source) if newline == -1 else newline + 1
            continue
        last = start + end
        while last < len(source) and source[last] in " \t\r":
            last += 1
        if last < len(source) and source[last] == "\n":
            last += 1
    pieces.append(source[last:])
    cleaned = _TOOL_BLOCK.sub("", "".join(pieces)).strip()
    return _strip_python_calls(cleaned).strip()


def visible_reply(text: str) -> str:
    """Assistant text safe to display: recognized call JSON never leaks.

    When the whole reply (or a fenced JSON block) is a recognized tool call,
    it is internal protocol and is removed. Ordinary prose that merely
    contains JSON is left untouched.
    """
    cleaned = strip_tool_blocks(text)
    if not parse_inline_tool_calls(cleaned):
        return cleaned
    cleaned = _INLINE_JSON_BLOCK.sub("", cleaned).strip()
    if cleaned.startswith(("{", "[")):
        try:
            json.loads(cleaned)
            return ""
        except ValueError:
            pass
    return cleaned


def validate_tool_call(call: ToolCall) -> str:
    """Strict pre-execution validation (v10.2.0).

    A call is executed only when the tool really exists and every required
    argument is present with a plausible type. The error text is fed back to
    the model so it can correct itself; nothing unvalidated reaches a
    destructive operation.
    """
    if call.error:
        return call.error
    name = (call.tool or "").strip().lower()
    if not name:
        return "Tool call is missing a tool name."
    tool = TOOL_REGISTRY.get(name)
    if tool is None:
        return (
            f"Unknown tool '{name}'. Available tools: "
            + ", ".join(sorted(TOOL_REGISTRY))
            + "."
        )
    missing = [
        arg
        for arg, description in tool.args.items()
        if "(optional)" not in description and arg not in call.args
    ]
    if missing:
        return (
            f"Tool '{name}' is missing required argument(s): "
            + ", ".join(missing)
            + "."
        )
    for arg, kind in tool.types.items():
        if arg not in call.args or kind != "integer":
            continue
        value = call.args[arg]
        if isinstance(value, bool):
            return f"Tool '{name}' argument '{arg}' must be an integer."
        try:
            int(value)
        except (TypeError, ValueError):
            return f"Tool '{name}' argument '{arg}' must be an integer."
    return ""


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
      ``{"phase": "tool_start"|"tool_done"|"narration", ...}`` — so a step-by-step
      task view can be driven by what actually happened instead of guesswork.
    """

    def __init__(
        self,
        config: AppConfig,
        permissions: PermissionManager,
        on_event: Callable[[str, str], None] | None = None,
        on_step: Callable[[dict[str, Any]], None] | None = None,
        on_delta: Callable[[str], None] | None = None,
        on_discard: Callable[[], None] | None = None,
        max_steps: int | None = None,
        agent_id: str | None = None,
        subagent_depth: int = 0,
    ) -> None:
        super().__init__(config)
        self.permissions = permissions
        # v10.2.0: the execution profile for the selected model (compact for
        # small local models, standard/advanced otherwise). It drives prompt
        # style, step/call budgets, compaction and the tool protocol.
        self.model_profile = profile_for(config)
        # Response-continuation state (one turn). ``_prefix_text`` preserves
        # the already-generated text so a continued answer is returned whole
        # and never duplicated.
        self._continuation_used = 0
        self._prefix_text = ""
        self._completion_nudged = False
        # v10.4.0: bounded correction when the model describes an edit instead
        # of applying it, and no-progress detection across tool steps.
        self._tool_nudged = False
        # v10.5.0: bounded correction when the model narrates a tool call
        # ("I will call list_dir") but emits no executable call. One per turn.
        self._tool_call_prompted = False
        self._user_request = ""
        self._step_signature = ""
        self._repeat_count = 0
        # Narrated details come straight from model/tool text; normalize them at
        # the observer boundary so no embedder (UI, tests, log) can receive text
        # that fails to encode (v7.1.0).
        self._on_event = _SafeObserver(on_event) if on_event else (lambda k, d: None)
        self._on_step = on_step or (lambda payload: None)
        # v10.5.0: live token relay. ``on_delta`` paints the answer as it is
        # generated; ``on_discard`` clears that preview when the text turns out
        # to be intermediate prose for a tool step (or a nudge being replaced).
        self._on_delta = on_delta or (lambda text: None)
        self._on_discard = on_discard or (lambda: None)
        # Native tool calling: None = untried, False = fell back to the text
        # protocol for this session, True = at least one native step worked.
        self._native: bool | None = None
        # Evidence observed during the turn in flight (v7.1.0): the persistent
        # Code Mode session verifies tasks against this, never against the
        # model's own claim of success.
        self.last_evidence = TurnEvidence()
        # Tool-call state (OpenCode session-tool parity): unique ids, timing,
        # duplicate-call prevention. One tracker instance = one turn; the
        # engine resets it in :meth:`run_turn`.
        self.calls = CallTracker()
        # The resolved need behind the turn in flight (v9.1.1). ``None`` until
        # a turn (or an explicit :meth:`set_intent`) sets it, in which case the
        # engine keeps the pre-9.1.1 behaviour of carrying full context.
        self._intent: Intent | None = None
        # User plugins: custom tools (registered into TOOL_REGISTRY) and
        # before/after tool hooks. Loaded once, for this engine's workspace.
        _load_plugins_once(self.permissions.workspace)
        # Agent profiles (OpenCode-style selectable personas over this ONE
        # engine). ``build`` — the default — is behaviour-neutral.
        self._registry = AgentRegistry(self.permissions.workspace)
        self._agent_id = (agent_id or DEFAULT_AGENT_ID).strip().lower()
        # Subagent delegation (OpenCode parity): this engine registers itself
        # as the session's spawn callback, so the ``subagent`` tool can create
        # a fresh child engine. ``subagent_depth`` counts how many delegation
        # hops separate this engine from the user's request (0 = main agent);
        # children deeper than the guard are refused with a plain tool error.
        self.subagent_depth = max(0, int(subagent_depth))
        # Only the MAIN engine is the session's spawn callback: a child must
        # never overwrite it with its own (depth-limited) handler, and the
        # main engine re-registers itself after a delegation returns.
        if self.subagent_depth == 0:
            register_delegate(self._spawn_subagent)
        # Code Mode gets a generous per-turn budget (the session continues
        # the task afterwards); callers may override it explicitly, and so may
        # the active profile (a caller's explicit value always wins).
        if max_steps is not None:
            self._explicit_steps = max(1, int(max_steps))
        else:
            self._explicit_steps = 0
        self._apply_profile_steps()
        self.messages[0] = Message(role="system", content=self._system_prompt())

    def _system_prompt(self) -> str:
        intent = self._intent

        # Small local models get a deliberately short, sequential context: a
        # long prompt is the first thing a limited model loses. Standard and
        # advanced models keep the full engine context below.
        if self.model_profile.prompt_style == "compact":
            return self._compact_system_prompt()

        # Build Seed Code identity + agent instructions
        base_identity = build_system_prompt(
            provider_label(self.config.provider),
            self.config.model or "unspecified"
        )
        preamble = _AGENT_PREAMBLE.format(
            workspace=self.permissions.workspace,
            mode=self.permissions.mode.label,
        )
        if self._workspace_allowed() and self._codemode_active():
            # The behavioural instruction that makes the model a coding agent
            # (not just a chat model with tools) — previously defined but
            # never injected, so Code Mode lost its workflow guidance.
            preamble += _CODEMODE_PREAMBLE.format(
                workspace=self.permissions.workspace
            )
            # v10.2.0: the modular instruction engine adds the stage objective,
            # the situation→tool guide (filtered to the tools this turn may
            # actually call), the TODO contract, verification and continuation.
            preamble += "\n\n" + instruction_engine.build_instructions(
                workspace=self.permissions.workspace,
                stage="execute",
                allowed=self.profile.tools,
                groups=self._groups(),
                context=self._project_memory_summary(),
                # Sized so the full contract (identity, protocol, security,
                # workspace, tool guide, TODO, verification, continuation)
                # survives for standard models; detail is what gets dropped.
                budget_chars=5600,
                protocol="native" if self._native_active() else "text",
            )
            preamble += self._codemode_context()
        else:
            # Agent mode outside Code Mode still needs the workspace boundary,
            # the injection defence and the tool lifecycle: a model must never
            # obey instructions found in a file it was asked to read.
            preamble += "\n\n" + instruction_engine.workspace_section(
                self.permissions.workspace
            )
            preamble += "\n\n" + instruction_engine.security_section()
            preamble += "\n\n" + instruction_engine.tool_protocol_section(
                "native" if self._native_active() else "text"
            )
        # Agent profile persona: appended AFTER the base preambles so it is
        # the last, most specific word on how this turn is performed.
        profile = self.profile
        preamble += (
            f"\n\nACTIVE AGENT PROFILE: {profile.name} ({profile.id}).\n"
            f"{profile.system}"
        )
        if profile.tools is not None:
            preamble += (
                "\nThis profile may call ONLY these tools: "
                + ", ".join(sorted(profile.tools))
                + ". Any other tool call is rejected before it runs; "
                "work strictly within this restriction."
            )
        # v9.1.1: an intent-aware preamble keeps a conversational question from
        # turning into a workspace sweep, and a browser task from touching the
        # project at all. The terminal hint is only useful when the task can
        # actually run something.
        if intent is not None:
            if intent.is_conversation:
                preamble += _CONVERSATION_PREAMBLE
            elif intent.kind is IntentKind.COMPUTER:
                preamble += _COMPUTER_PREAMBLE
        if intent is None or intent.needs_terminal or intent.needs_workspace:
            preamble += self._terminal_context()
        if self._native_active():
            # The API carries the tool schemas; no manifest needed.
            instructions = _NATIVE_INSTRUCTIONS
        else:
            instructions = _TEXT_PROTOCOL_INSTRUCTIONS.format(
                manifest=tool_manifest(self._groups(), allowed=profile.tools)
            )
        project = self._project_context() if self._workspace_allowed() else ""
        # v10.4.0: progressive-disclosure skills and the durable task record.
        # Both are omitted when empty, so an unconfigured project sees no
        # extra prompt at all.
        sections = [self._skills_section(), self._task_state_section()]
        extra = "\n\n".join(section for section in sections if section)
        return base_identity + preamble + instructions + project + (("\n\n" + extra) if extra else "")

    def _compact_system_prompt(self) -> str:
        """The small-model context: short, sequential and example-driven."""
        profile = self.profile
        lines = [
            f"You are SeedCode, an autonomous coding agent in {self.permissions.workspace}.",
            f"Agent: {profile.name}. {profile.system}",
            "",
            "Work in small steps:",
            "1. Inspect with tools before changing anything.",
            "2. Call one or two tools, then read their real results.",
            "3. Change code only with the file tools; chat text is not a file.",
            "4. If a tool fails, read the error and fix it; do not repeat the call.",
            "5. Verify the result, then answer briefly without tools.",
            "6. Never claim work that a tool result does not prove.",
            "7. Record the plan with update_task; load a matching skill with use_skill.",
        ]
        if profile.tools is not None:
            lines.append(
                "Only these tools are allowed: " + ", ".join(sorted(profile.tools)) + "."
            )
        lines.append("Use run_command for builds and tests. Stay inside the workspace.")
        # v10.2.0: the compact instruction engine is the small-model contract —
        # one objective, explicit steps, an explicit tool grammar.
        compact = instruction_engine.compact_instructions(
            workspace=self.permissions.workspace,
            allowed=profile.tools,
            groups=self._groups(),
            context=self._project_memory_summary(max_chars=600),
            budget_chars=1600,
        )
        if compact:
            lines.append(compact)
        if self._native_active():
            lines.append(_COMPACT_NATIVE_INSTRUCTIONS)
        else:
            lines.append(
                _COMPACT_TEXT_INSTRUCTIONS.format(
                    manifest=tool_manifest(self._groups(), allowed=profile.tools)
                )
            )
        for section in (self._skills_section(), self._task_state_section()):
            if section:
                lines.append(section)
        return "\n".join(lines)

    def _task_state_section(self) -> str:
        """The durable ``.seedcode/todo.json`` plan, when one exists."""
        try:
            return TaskStateStore(self.permissions.workspace).render()
        except Exception:
            return ""

    def _skills_section(self) -> str:
        """Skill names and descriptions only (bodies load via ``use_skill``)."""
        try:
            return skill_catalog(self.permissions.workspace)
        except Exception:
            return ""

    # --- agent profile -------------------------------------------------------
    @property
    def profile(self) -> AgentProfile:
        """The active agent profile (resolved; the default when unknown)."""
        return self._registry.resolve(self._agent_id)

    def set_agent(self, agent_id: str | None) -> AgentProfile:
        """Select the agent profile for subsequent turns and re-render.

        Returns the profile that is actually active after the change, so a
        caller (the /agents command) can report it honestly. The step budget
        is recalculated unless a caller explicitly fixed it.
        """
        profile = self._registry.resolve(agent_id)
        self._agent_id = profile.id
        self._apply_profile_steps()
        self.refresh_system_prompt()
        return profile

    def _apply_profile_steps(self) -> None:
        """Recalculate ``max_steps``: caller, config, agent profile, model.

        Priority: an explicit caller budget, then the user's
        ``max_agent_steps`` setting, then the agent profile's own cap, then the
        model-aware execution profile. The model profile can only raise the
        engine floor (never silently reduce a stronger model), while Code Mode
        keeps its generous session budget.
        """
        # The model may have been switched since the last turn (provider/model
        # are live configuration), so the profile is re-resolved here too.
        self.model_profile = profile_for(self.config)
        if self._explicit_steps:
            self.max_steps = self._explicit_steps
            return
        configured = int(getattr(self.config, "max_agent_steps", 0) or 0)
        if configured > 0:
            self.max_steps = configured
            return
        profile_steps = self.profile.steps
        base = CODEMODE_MAX_STEPS if self._codemode_active() else MAX_STEPS
        floor = max(base, int(self.model_profile.max_steps))
        self.max_steps = profile_steps if profile_steps else floor

    def _max_calls_per_step(self) -> int:
        """Calls executed from one model step (bounded by config/profile)."""
        configured = int(getattr(self.config, "max_tool_calls_per_step", 0) or 0)
        if configured > 0:
            return max(1, min(configured, _MAX_CALLS_PER_STEP))
        return max(1, min(int(self.model_profile.max_calls_per_step), _MAX_CALLS_PER_STEP))

    # --- subagent delegation (OpenCode parity) --------------------------------
    def _spawn_subagent(self, task: str, agent_id: str | None = None) -> str:
        """Run a delegated task on a FRESH child engine, return its report.

        OpenCode reference: ``packages/core/src/tool/plugin/subagent.ts``. The
        child gets an isolated message history (only the delegated task), the
        SAME PermissionManager (delegation never grants duties), an explicit
        profile id, and cannot spawn further subagents — OpenCode's depth
        guard, ported: hovering one hop costs context trust, two already
        nests sessions.

        When the chosen profile carries its own provider/model routing, the
        child is served through that backend (a copy of the session config —
        the session's own selection is untouched); otherwise it inherits the
        session's routing.

        The whole child transcript is a side conversation: only its final
        report becomes the ``subagent`` tool result. Failures surface as a
        plain tool error so this (parent) turn survives and can reason over
        what happened.
        """
        if self.subagent_depth >= 1:
            raise ToolError(
                "Subagent delegation is one level deep: the delegated agent "
                "may not spawn further subagents. Decompose the task on this "
                "level instead."
            )
        if not str(task or "").strip():
            raise ToolError("the delegated task is empty.")
        child_profile = self._registry.resolve(agent_id or "general")
        if child_profile.provider or child_profile.model:
            # Deep copy: the model setter writes the active provider's entry,
            # and this child must never move the SESSION's selected model.
            config = self.config.model_copy(deep=True)
            if child_profile.provider:
                config.provider = child_profile.provider
            if child_profile.model:
                config.model = child_profile.model
        else:
            config = self.config
        child_event = (
            (lambda kind, detail: _child_delegate(
                f"[subagent:{child_profile.id}] {kind}: {detail}"
            ))
            if _child_delegate is not None
            else None
        )
        child = AgentEngine(
            config,
            self.permissions,
            on_event=child_event,
            max_steps=child_profile.steps or 20,
            agent_id=child_profile.id,
            subagent_depth=self.subagent_depth + 1,
        )
        if _child_delegate is not None:
            _child_delegate(f"Starting {child_profile.name} agent …")
        try:
            report = child.run_turn(task)
            # A child construction during the child's own run could have
            # replaced the session delegate; the main engine stays the owner.
            if self.subagent_depth == 0:
                register_delegate(self._spawn_subagent)
        except Exception as exc:
            # Any child failure — provider outage, budget exhaustion, a broken
            # tool — is isolated to a plain tool error; the parent's turn
            # continues and shows the model what actually went wrong.
            if self.subagent_depth == 0:
                register_delegate(self._spawn_subagent)
            raise ToolError(
                f"Subagent ({child_profile.id}) failed before completing the "
                f"task: {exc}"
            ) from exc
        if not str(report or "").strip():
            raise ToolError(
                f"Subagent ({child_profile.id}) returned no report — it may "
                "have hit its step budget. Try a narrower task."
            )
        return report

    # --- intent (v9.1.1) ------------------------------------------------------
    def set_intent(self, intent: Intent | None) -> None:
        """Set the resolved need for the next turn and re-render the prompt.

        A caller that already classified the request (the app layer, so Chat
        escalation and Agent execution agree) passes the result here; otherwise
        :meth:`run_turn` classifies the user text itself.
        """
        self._intent = intent
        self.refresh_system_prompt()

    @property
    def intent(self) -> Intent | None:
        """The intent of the turn in flight (``None`` before the first turn)."""
        return self._intent

    def _workspace_allowed(self) -> bool:
        """Whether this turn may read the project (the minimum-tool rule).

        Unknown intent (no turn yet) preserves the previous behaviour, so a
        caller that never classifies loses nothing. Once an intent is set, a
        conversational or computer task is not allowed workspace context — the
        request must not cause file access it does not need.
        """
        if self._intent is None:
            return True
        return self._intent.needs_workspace

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

    def _project_memory_summary(self, max_chars: int = 1000) -> str:
        """The summarized persistent project context, when it has been written.

        Reads ``.seedcode/context.json`` only — it never scans the workspace
        here. The session writes that document at start and refreshes it after
        verified work, so this stays a cheap read on the hot path.
        """
        try:
            from pathlib import Path

            from ..utils.atomic import read_json
            from .context import ProjectContext, context_path

            raw = read_json(context_path(Path(self.permissions.workspace)))
            if not isinstance(raw, dict):
                return ""
            return ProjectContext.from_dict(raw).to_prompt(max_chars=max_chars)
        except Exception:
            _log.exception("could not read the project context")
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

    def event(self, kind: str, detail: str = "") -> None:
        """Forward safe provider lifecycle milestones to the agent observer."""
        super().event(kind, detail)
        self._on_event(kind, detail)

    def _groups(self) -> tuple[str, ...]:
        """The tool groups this turn may use (minimum necessary, v9.1.1).

        ``core`` is always available; ``desktop`` only when the Computer Engine
        is usable *and* the task needs it; ``web`` only when the task needs
        current external information. A coding turn therefore never advertises
        browser/desktop/web tools, and a browser turn never advertises web
        search. An agent profile that names its own groups is authoritative
        instead (``None`` keeps the engine's judgement).
        """
        profile_groups = self.profile.groups
        if profile_groups:
            return tuple(group.strip().lower() for group in profile_groups)
        groups: list[str] = ["core"]
        extra = self._intent.tool_groups if self._intent is not None else ()
        for group in extra:
            if group not in groups and group != "desktop":
                groups.append(group)
        return tuple(groups)

    def _native_active(self) -> bool:
        """Whether this session should use native tool calling right now.

        A small-model profile prefers the explicit text protocol even when the
        provider claims native support: it is easier for a limited model to
        follow and gives SeedCode strict JSON to validate. ``auto`` keeps the
        provider-capability check (and the runtime fallback).
        """
        protocol = (self.model_profile.tool_protocol or "auto").lower()
        if protocol == "text" or self._native is False:
            return False
        try:
            from .providers import get_provider

            supported = get_provider(self.config.provider).supports_tools(self.config)
        except Exception:
            return False
        if protocol == "native":
            return True
        return supported

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
        self.calls.reset()
        # v10.2.0: model/provider may have changed since the last turn, so the
        # execution profile and its budgets are re-resolved per turn.
        self.model_profile = profile_for(self.config)
        self._apply_profile_steps()
        self._continuation_used = 0
        self._prefix_text = ""
        self._completion_nudged = False
        self._tool_nudged = False
        self._tool_call_prompted = False
        self._user_request = user_text or ""
        self._step_signature = ""
        self._repeat_count = 0
        # v9.1.1: understand the request before choosing anything. The intent
        # decides which context and which tools this turn may use, so the agent
        # never scans the workspace for a song or a browser task. Classification
        # is deterministic, so re-classifying here agrees with a caller (the app
        # layer) that already classified for its own escalation decision.
        resolved = classify(user_text)
        if resolved != self._intent:
            self.set_intent(resolved)
        # The turn may resume a plan persisted by an earlier turn, so the
        # system prompt is re-rendered with the current task file.
        self.refresh_system_prompt()
        self.add_user(user_text)
        self._notify("task_started", text=user_text)
        self._on_event("agent_started", f"max_steps={self.max_steps}")
        failures = 0

        _log.info(
            "agent execution started: provider=%s model=%s profile=%s max_steps=%d",
            self.config.provider,
            self.config.model,
            self.model_profile.id,
            self.max_steps,
        )

        step = 0
        while step < self.max_steps:
            step += 1
            self._on_event("iteration", f"step {step}/{self.max_steps}")
            self._on_event("context", str(self._context_tokens()))
            native = self._native_active()
            _log.debug(
                "model iteration started: step=%d/%d native=%s consecutive_failures=%d",
                step,
                self.max_steps,
                native,
                failures,
            )
            if native:
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
                final_text = safe_text(payload) or "Done."
                self._notify("task_completed", text=final_text)
                self._on_event("agent_completed", final_text[:200])
                _log.info("agent execution completed: steps=%d", step)
                return final_text
            failures = failures + 1 if outcome == "failed" else 0
            if outcome == "ok" and self._no_progress():
                # Repeating identical tool work is not progress. Stop honestly
                # and show what the durable record says remains.
                self._on_event("agent_failed", "no progress: repeated identical tool work")
                self._notify("task_failed", text="no progress: repeated identical tool work")
                self.last_evidence.incomplete = True
                state = self._task_state_section()
                report = (
                    "No progress: the same tool work repeated without changing "
                    "the workspace, so I stopped instead of looping. "
                )
                if state:
                    report += "\n\n" + state
                report += "\n\nTell me a different approach (or the missing detail) to continue."
                return report

        self._on_event("limit", f"step budget ({self.max_steps}) reached")
        self._on_event("agent_failed", f"step budget ({self.max_steps}) reached")
        self._notify("task_failed", text=f"step budget ({self.max_steps}) reached")
        _log.warning("agent execution stopped at step budget: max_steps=%d", self.max_steps)
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
    def _context_tokens(self) -> int:
        """Estimate the tokens currently held in the model transcript.

        A cheap chars/4 estimate over exactly the messages that will be sent
        (content plus structured tool-call arguments). It is used for the
        status bar's real usage, never for billing or truncation decisions.
        """
        chars = 0
        for message in self.messages:
            chars += len(message.content or "")
            for call in message.tool_calls or ():
                chars += len(
                    json.dumps(call.arguments or {}, ensure_ascii=False, default=str)
                )
        return max(0, chars // 4)

    def _context_budget(self) -> int:
        """Best-known context window for the active provider.

        Ollama is the one provider whose window we actually request, so use it
        when configured. Everywhere else the window is provider/model metadata
        we do not persist; a generous default still catches runaway tool output.
        """
        if getattr(self.config, "provider", "") == "ollama":
            num_ctx = int(getattr(self.config, "ollama_num_ctx", 0) or 0)
            if num_ctx > 0:
                return num_ctx
        return _DEFAULT_CONTEXT_TOKENS

    def _trim_old_tool_results(self, limit: int) -> bool:
        """Clamp large tool results outside the recent window, oldest first."""
        changed = False
        for message in self.messages[1:max(0, limit)]:
            if message.role != "tool" or not message.content:
                continue
            if len(message.content) <= _TOOL_RESULT_TRIM_CHARS:
                continue
            message.content = (
                message.content[:_TOOL_RESULT_TRIM_CHARS]
                + "\n…[older tool output truncated to keep the context bounded]"
            )
            changed = True
        return changed

    def compact_history(
        self,
        *,
        compact_at: int | None = None,
        keep_recent: int = _HISTORY_KEEP_RECENT,
        max_tokens: int | None = None,
    ) -> bool:
        """Fold older history into one summary message; True when it compacted.

        Code Mode sessions run for many cycles, and unlimited raw history would
        grow the context window without limit. Compaction triggers on message
        count *or* estimated token pressure, so a few huge tool results cannot
        overflow the window before the count threshold is reached. The cut is
        always made at a safe boundary (never between an assistant's tool call
        and its tool results), so strict providers still receive paired
        messages.
        """
        threshold = compact_at or int(self.model_profile.history_compact_at)
        if keep_recent == _HISTORY_KEEP_RECENT:
            keep_recent = int(self.model_profile.keep_recent)
        budget = max(0, int(max_tokens or self._context_budget()))
        pressure = bool(budget) and self._context_tokens() >= int(
            budget * _CONTEXT_COMPACT_RATIO
        )
        if len(self.messages) <= threshold and not pressure:
            return False

        system = self.messages[0] if self.messages and self.messages[0].role == "system" else None
        body = self.messages[1:] if system is not None else list(self.messages)
        if pressure:
            # Large results are the cheapest thing to reclaim: trim them before
            # sacrificing whole turns from the recent window.
            keep_hint = max(4, min(keep_recent, len(body)))
            self._trim_old_tool_results(len(self.messages) - keep_hint)
            if len(self.messages) <= threshold and self._context_tokens() < int(
                budget * _CONTEXT_COMPACT_RATIO
            ):
                return False
            keep_recent = min(keep_recent, _TOKEN_PRESSURE_KEEP)

        keep = max(4, min(keep_recent, len(body)))
        cut = len(body) - keep
        # Never split a tool result from the assistant call that produced it.
        while cut < len(body) and body[cut].role == "tool":
            cut += 1
        if cut <= 0:
            return False
        # Dropping only the previous summary would not shrink anything.
        if body and str(body[0].content or "").startswith("[SESSION SUMMARY]") and cut <= 1:
            return False

        dropped = body[:cut]
        kept = body[cut:]
        summary = self._history_summary(dropped)
        self.messages = ([system] if system is not None else []) + [
            Message(role="user", content=summary)
        ] + kept
        if pressure and self._context_tokens() >= int(budget * _CONTEXT_COMPACT_RATIO):
            # Still tight: clamp old results inside the kept window too, but
            # never touch the newest message (the model is about to answer it).
            self._trim_old_tool_results(len(self.messages) - 1)
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
        request = next(
            (
                str(message.content).strip()
                for message in dropped
                if message.role == "user" and message.content
            ),
            "",
        )
        if request:
            lines.append("Original request: " + request[:300])
        if files:
            lines.append("Files touched: " + ", ".join(files[-20:]))
        if commands:
            lines.append("Commands already run: " + "; ".join(commands[-10:]))
        lines.append(f"Turns compacted: {len(dropped)}")
        return "\n".join(lines)

    # --- response termination (v10.2.0) --------------------------------------
    def _looks_truncated(self, reply: str) -> bool:
        """Heuristic for a text reply cut off mid-structure.

        Used only for the text protocol, where there is no provider finish
        metadata. An odd number of code fences or a ``TOOL_CALL`` marker that
        produced no complete call means the reply ended mid-structure.
        """
        stripped = (reply or "").rstrip()
        if not stripped:
            return False
        if stripped.count("```") % 2 == 1:
            return True
        if (
            stripped.lower().count("<|tool_call_start|>")
            > stripped.lower().count("<|tool_call_end|>")
        ):
            return True
        return bool(_TOOL_CALL_MARKER.search(stripped))

    def _should_continue(self, reply: str, finish_reason: str) -> bool:
        """Whether this reply is a truncated response that must continue."""
        if not bool(getattr(self.config, "continue_truncated", True)):
            return False
        if self._continuation_used >= int(self.model_profile.continuation_limit):
            return False
        if (finish_reason or "").strip().lower() in {
            "length",
            "max_tokens",
            "max_output_tokens",
        }:
            return True
        return self._looks_truncated(reply)

    def _needs_completion_nudge(self, reply: str) -> bool:
        """A completion claim after a failed tool call is not proof.

        The backend asks for one verification step. The nudge is bounded to a
        single step per turn, so a model that cannot recover still ends with an
        honest final reply instead of looping.
        """
        if not self.last_evidence.errors or self.last_evidence.tools <= 0:
            return False
        return bool(_COMPLETION_CLAIM.search(reply or ""))

    def _finish(self, reply: str, finish_reason: str = "") -> tuple[str, str | None]:
        """Finish or continue a no-tool reply.

        Order matters: a truncated response is continued first (preserving the
        already-generated prefix), then a completion claim after a failure is
        sent back for one verification pass, and only then is the text final.
        """
        reply = safe_text(reply or "")
        if self._should_continue(reply, finish_reason):
            self._continuation_used += 1
            _log.info(
                "model continuation started: reason=%s continuation=%d/%d",
                finish_reason or "truncated structure",
                self._continuation_used,
                self.model_profile.continuation_limit,
            )
            self._prefix_text += reply
            self.add_assistant(reply)
            self.messages.append(Message(role="user", content=_CONTINUATION_PROMPT))
            self._on_event("continue", "output limit reached; continuing")
            self._notify("continuing", text="continuing the truncated response")
            return ("ok", None)

        combined = (self._prefix_text + reply).strip()
        self._prefix_text = ""

        if not self._tool_nudged and self._needs_tool_nudge(reply):
            # Describing an edit is not applying it: ask for the real tool work
            # once, preserving the turn (bounded, so a model that cannot comply
            # still ends with its answer instead of looping).
            self._tool_nudged = True
            self._on_discard()
            self.add_assistant(reply)
            self.messages.append(Message(role="user", content=_USE_TOOLS_PROMPT))
            self._on_event("limit", "workspace change described but not applied")
            self._notify("verifying", text="asking for the file changes to be applied")
            return ("ok", None)

        if self._tool_nudged and self._needs_tool_nudge(reply):
            # The one correction was ignored: never let unapplied work read as
            # a completed change. The real workspace stays untouched.
            # ``combined`` was captured above, so prefix it too: it is what the
            # final return actually uses.
            combined = _NO_CHANGES_NOTE + combined
            # The streamed prose alone would read as applied work: replace the
            # live preview with the same honest note the final text carries.
            self._on_discard()
            self._on_delta(combined)

        if (
            not self._tool_nudged
            and not self._tool_call_prompted
            and self._mentions_tool_use(reply)
        ):
            # The reported failure: the model narrates the call ("I will call
            # list_dir") and ends the turn. Ask once for the executable call,
            # preserving the turn; bounded so it can never loop.
            self._tool_call_prompted = True
            self._prefix_text = ""
            self._on_discard()
            self.add_assistant(reply)
            self.messages.append(Message(role="user", content=_TOOL_MENTION_PROMPT))
            self._on_event(
                "continue", "tool action described but no call emitted; requesting it"
            )
            self._notify("verifying", text="requesting the real tool call")
            return ("ok", None)

        if not self._completion_nudged and self._needs_completion_nudge(reply):
            self._completion_nudged = True
            self._on_discard()
            self.add_assistant(reply)
            self.messages.append(Message(role="user", content=_VERIFY_PROMPT))
            self._on_event("limit", "completion claim requires verification")
            self._notify("verifying", text="verifying the claimed completion")
            return ("ok", None)

        # A text-protocol session stays downgraded: only an untried session
        # becomes "native worked" here.
        if self._native is None:
            self._native = True
        self.add_assistant(reply)
        return ("final", combined or "Done.")

    # --- native path ---------------------------------------------------------
    def _native_step(self, failures: int) -> tuple[str, str | None]:
        """One step over the native tool-calling API.

        Returns ("final", text) | ("ok", None) | ("failed", None) |
        ("fallback", None).
        """
        specs = [
            ToolSpec(**s)
            for s in tool_specs(self._groups(), allowed=self.profile.tools)
        ]
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        finish_reason = ""
        try:
            for event in self.stream_reply_events(specs):
                if isinstance(event, TextDelta):
                    text_parts.append(event.text)
                    self._on_delta(event.text)
                elif isinstance(event, FinishEvent):
                    # The provider's own terminal reason: a "length" stop is a
                    # truncated answer that must be continued, not finalised.
                    finish_reason = event.reason
                elif isinstance(event, ThinkingDelta):
                    # v10.3.1: the model's own reasoning IS user-visible — it is
                    # the live reasoning strip above the composer. Relay it as
                    # its own event kind; it is never mixed into the answer
                    # text, counted as output, or stored as a tool activity.
                    if event.text:
                        self._on_event("thinking", event.text)
                    continue
                elif isinstance(event, ToolCallEvent):
                    call = ToolCall(
                        tool=(event.name or "").strip().lower(),
                        args=clean_values(event.arguments),
                        error=event.error,
                        call_id=event.id,
                    )
                    calls.append(call)
                    _log.info("tool call detected: name=%s", call.tool)
        except ChatError as exc:
            if exc.fallback_to_text:
                _log.info("native tool protocol unsupported; falling back to text")
                return ("fallback", None)
            self.drop_last_user()
            raise

        reply = "".join(text_parts)

        if not calls:
            # Belt-and-braces: some models emit the TEXT protocol (or an
            # OpenAI-style JSON call) even when given native tools — honour it
            # rather than ending the turn.
            text_calls = parse_tool_calls(reply) or parse_inline_tool_calls(reply)
            if not text_calls:
                return self._finish(reply, finish_reason)
            self._native = True
            return self._execute_text_style(reply, text_calls, failures)

        self._native = True
        # Tool work starts a fresh output segment: a partially continued text
        # prefix must not be concatenated with a later post-tool answer.
        self._prefix_text = ""
        # Any prose streamed before the call is not the answer: clear the live
        # preview, then show it as one muted narration line.
        self._on_discard()
        call_limit = self._max_calls_per_step()
        executed = calls[:call_limit]
        deferred = calls[call_limit:]

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
        # Recognized native calls are internal protocol: their JSON is never
        # echoed as ordinary assistant text.
        shown = visible_reply(reply)
        if shown:
            self._on_event("narration", shown)

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
                        f"(not executed: only the first {call_limit} tool "
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
            parts: list[str] = []
            for piece in self.stream_reply():
                parts.append(piece)
                self._on_delta(piece)
            reply = "".join(parts)
        except ChatError:
            self.messages = original
            self.drop_last_user()
            raise
        self.messages = original

        calls = parse_tool_calls(reply) or parse_inline_tool_calls(reply)
        if not calls:
            # Some models complete tool work without emitting a summary line.
            # _finish returns a minimal acknowledgement so the UI never shows
            # "(no response)", and it applies continuation/completion guards.
            return self._finish(reply, "")

        return self._execute_text_style(reply, calls, failures)

    def _execute_text_style(
        self, reply: str, calls: list[ToolCall], failures: int
    ) -> tuple[str, str | None]:
        """Execute calls and append text-protocol style feedback messages."""
        for call in calls:
            _log.info("tool call detected: name=%s", call.tool)
        self.add_assistant(reply)
        # See _native_step: executing tools ends the pure-text continuation.
        self._prefix_text = ""
        self._on_discard()
        shown = visible_reply(reply)
        if shown:
            self._on_event("narration", shown)

        call_limit = self._max_calls_per_step()
        results, step_failed = self._execute_calls(calls[:call_limit])
        if len(calls) > call_limit:
            results.append(
                f"(only the first {call_limit} tool calls were run; "
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

    def _mentions_tool_use(self, reply: str) -> bool:
        """Whether the reply narrates a real tool action instead of emitting it.

        The v10.5.0 failure: a local model answers "I will call list_dir" and
        ends the turn, so the tool never runs. Only an actionable phrase
        (``I'll call``, ``let me read``, ``using``) directly before a real tool
        name counts; a clarifying question, a conversational answer, or a code
        example is left alone.
        """
        if self._intent is not None and self._intent.is_conversation:
            return False
        if _SHOW_ONLY.search(self._user_request or ""):
            return False
        text = reply or ""
        # A fenced example may legitimately name/use a tool: never execute it.
        prose = re.sub(r"```.*?```", " ", text, flags=re.S)
        stripped = prose.strip()
        if not stripped or stripped.endswith("?"):
            return False
        if re.match(
            r"^\s*(which|what|please|could you|can you|i need|i cannot|i can't|"
            r"i am unable|unable)\b",
            stripped,
            re.IGNORECASE,
        ):
            return False
        for name in TOOL_REGISTRY:
            for match in re.finditer(rf"\b{re.escape(name)}\b", prose, re.IGNORECASE):
                window = prose[max(0, match.start() - 100):match.start()]
                if _TOOL_MENTION_INTENT.search(window) and _TOOL_MENTION_VERBS.search(
                    window
                ):
                    return True
                if re.search(r"\b(?:using|with|via|through)\s+$", window, re.I) and (
                    _TOOL_MENTION_VERBS.search(window)
                ):
                    return True
        return False

    def _needs_tool_nudge(self, reply: str) -> bool:
        """A workspace-changing request answered only with code in prose."""
        if not self._workspace_allowed():
            return False
        if self._intent is not None and self._intent.is_conversation:
            return False
        if self.last_evidence.changed_files:
            return False
        request = self._user_request or ""
        if not _MODIFY_REQUEST.search(request) or _SHOW_ONLY.search(request):
            return False
        # The session's planning instruction explicitly forbids code: it must
        # return a plan, not be pushed into applying one.
        if re.search(
            r"\bimplementation plan\b|\bdo not write (?:any )?code\b",
            request,
            re.IGNORECASE,
        ):
            return False
        # No tool ran at all: unless the model asked a real clarifying question
        # or reported a blocker, this is a prose answer to a change request
        # (the exact "I created the file" failure small models produce).
        if self.last_evidence.tools <= 0:
            text = (reply or "").strip()
            if re.search(r"\?\s*$", text):
                return False
            if re.search(
                r"^\s*(which|what|please|could you|can you|i need|i cannot|i can't|"
                r"i am unable|unable)\b",
                text,
                re.IGNORECASE,
            ):
                return False
            return True
        blocks = [
            body
            for language, body in _CODE_FENCE.findall(reply or "")
            if language.strip().lower() not in _NON_CODE_LANGS
        ]
        return bool(blocks) and sum(len(block.splitlines()) for block in blocks) >= 2

    def _note_step_signature(self, calls: list[ToolCall], results: list[str]) -> None:
        """Track whether a tool step produced a different observable result."""
        # Duplicate-call results carry the tracker's per-attempt suffix
        # (``same-call#15``); normalize it so replaying the same call really
        # looks identical, while genuinely different output still differs.
        stable_results = [re.sub(r"#[0-9]+", "#dup", result or "") for result in results]
        payload = json.dumps(
            {
                "calls": [[call.tool, call.args] for call in calls],
                "results": stable_results,
            },
            sort_keys=True,
            default=str,
        )
        if payload == self._step_signature:
            self._repeat_count += 1
        else:
            self._step_signature = payload
            self._repeat_count = 1

    def _no_progress(self) -> bool:
        """True after too many identical call/result cycles."""
        return self._repeat_count >= _MAX_REPEATED_STEPS

    def _sync_task_state(self, call: ToolCall, result: ToolResult) -> None:
        """Keep an existing todo.json result field synchronized with reality."""
        try:
            store = TaskStateStore(self.permissions.workspace)
            state = store.load()
            if state is None:
                return
            if call.tool == "run_command":
                command = str((call.args or {}).get("command") or "")[:120]
                state.last_result = f"{command}: {'ok' if result.ok else 'failed'}"[:400]
            else:
                tool = TOOL_REGISTRY.get(call.tool)
                if tool is None or not tool.mutates:
                    return
                path = next(
                    (
                        str((call.args or {}).get(key))
                        for key in ("path", "source", "destination", "file")
                        if (call.args or {}).get(key)
                    ),
                    "",
                )
                state.last_result = (
                    f"{call.tool} {path}: {'ok' if result.ok else 'failed'}"[:400]
                )
            store.save(state)
        except Exception:
            return  # task bookkeeping must never end a turn

    def _limit_reached(self, step_failed: bool, failures: int) -> bool:
        """True when this failure crosses the consecutive-failure bound."""
        if not step_failed:
            return False
        if failures + 1 >= _MAX_CONSECUTIVE_FAILURES:
            self._on_event("limit", "too many consecutive tool failures")
            return True
        return False

    def _drain_images(self) -> list[str]:
        """No image attachments: desktop control was removed in v9.2.0."""
        return []

    def _current_task_id(self) -> int:
        """The persistent session's active task id (0 outside a session)."""
        try:
            from . import session as session_mod

            session = session_mod.current_session()
            graph = getattr(session, "graph", None) if session is not None else None
            active = graph.active() if graph is not None else None
            return int(getattr(active, "id", 0) or 0)
        except Exception:
            return 0

    def _notify(self, phase: str, **fields: Any) -> None:
        """Report structured progress to the UI (a UI bug must never break a turn).

        Besides the legacy ``phase``/``name``/``args`` keys, every report now
        carries a complete :class:`~seedcode.core.events.ActivityEvent`
        (event id, task id, timestamp, type, message, path, status, diff and
        line counts) built from this real operation.
        """
        try:
            event = event_stream.build_event(
                phase=phase,
                task_id=self._current_task_id(),
                **fields,
            )
            payload: dict[str, Any] = {"phase": phase, **fields}
            payload.update(event.to_dict())
            self._on_step(payload)
        except Exception:
            _log.exception("step observer failed")

    def _execute_one(
        self, call: ToolCall, state: ToolCallState | None = None
    ) -> ToolResult:
        """Run one tool call through the registry, converting failures.

        Terminal commands get a structured per-line observer for the duration
        of the call, so ``terminal_output`` events reach the UI while the
        command is still running instead of only when it exits.

        When ``state`` is given, the call's real start/finish time and
        terminal status (completed/failed/blocked) are recorded in the turn's
        tracker (see :mod:`seedcode.core.calls`).

        Two gates run BEFORE the tool, both feeding a plain error back to the
        model (never a crash, never a silent success):

        * the active agent profile's whitelist (a read-only profile genuinely
          cannot mutate — the call is refused here, not merely discouraged);
        * the tool-lifecycle hooks (:meth:`~seedcode.tools.hooks.HookManager
          .run_before`), which may veto an execution (a plugin, a policy).
        """
        if state is not None:
            self.calls.begin(state)
        profile = self.profile
        if profile.tools is not None and call.tool not in profile.tools:
            return ToolResult(
                False,
                f"Blocked: the '{profile.name}' agent profile may not call "
                f"'{call.tool}'. Allowed tools: "
                + ", ".join(sorted(profile.tools))
                + ". Switch agents with /agents to change this."
            )
        veto = tool_hooks.run_before(call.tool, call.args)
        if veto:
            return ToolResult(False, f"Blocked: {veto}.")

        previous = self.permissions.on_activity
        if call.tool == "run_command":
            self.permissions.on_activity = lambda line: self._notify(
                "terminal_output", name="run_command", output=line
            )
        result: ToolResult | None = None
        try:
            result = get_tool(call.tool).run(self.permissions, call.args)
        except (ToolError, PermissionError_) as exc:
            result = ToolResult(False, str(exc))
        except Exception as exc:  # a tool bug must not kill the loop
            _log.exception("tool crashed: %s", call.tool)
            result = ToolResult(False, f"Tool crashed: {exc}")
        finally:
            self.permissions.on_activity = previous
            if result is not None:  # observation only; never fails the loop
                tool_hooks.run_after(
                    call.tool,
                    call.args,
                    ToolHookResult(result.ok, result.output, result.diff, result.exit_code),
                )
        assert result is not None
        if state is not None:
            self.calls.finish(
                state,
                result.ok,
                result.output,
                blocked=result.output.startswith("Blocked:"),
            )
        return result

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
        # the same whether execution was parallel or not. A provider call id
        # that was ALREADY answered this turn (a provider resend) is recorded
        # as deduped and its earlier result replayed — never executed again.
        runnable: list[tuple[int, ToolCall, ToolCallState]] = []
        states: dict[int, ToolCallState] = {}
        deduped: dict[int, ToolCallState] = {}
        for index, call in enumerate(calls):
            if not call.error:
                # v10.2.0: validate the name and required arguments BEFORE any
                # execution, dedupe or permission check. A malformed call is
                # reported to the model, never run.
                call.error = validate_tool_call(call)
            if call.error:
                continue
            prior = self.calls.duplicate_of(call.call_id)
            if prior is not None:
                dup_state = self.calls.start(call.tool, call.args, call.call_id)
                self.calls.finish(
                    dup_state, prior.ok or False, prior.output, prior.error,
                    deduped=True,
                )
                deduped[index] = dup_state
                self._on_event(
                    "call", f"{call.tool}(duplicate call id {call.call_id} — replayed)"
                )
                continue
            state = self.calls.start(call.tool, call.args, call.call_id)
            label = f"{call.tool}({json.dumps(call.args, ensure_ascii=False)[:120]})"
            self._on_event("call", label)
            _log.info("tool execution started: name=%s", call.tool)
            self._notify(
                "tool_start", name=call.tool, args=call.args, call_id=state.call_id
            )
            runnable.append((index, call, state))
            states[index] = state

        outcomes: dict[int, ToolResult] = {}
        parallel = (
            len(runnable) >= 2
            and self.model_profile.parallel_reads
            and all(
                (tool := TOOL_REGISTRY.get(call.tool)) is not None
                and not tool.mutates
                for _index, _call, call in runnable
            )
        )
        if parallel:
            with ThreadPoolExecutor(max_workers=min(4, len(runnable))) as pool:
                futures = {
                    index: pool.submit(self._execute_one, call, state)
                    for index, call, state in runnable
                }
                for index, future in futures.items():
                    outcomes[index] = future.result()
        else:
            for index, call, state in runnable:
                outcomes[index] = self._execute_one(call, state)

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
            dup_state = deduped.get(index)
            if dup_state is not None:
                # Never re-executed: the model is told its earlier result
                # still stands so it does not reason as if the call were new.
                results.append(
                    f"{call.tool} -> [DUPLICATE call id {dup_state.call_id} was "
                    f"already executed this turn ({dup_state.status}); recorded "
                    f"result replayed — do not call it again:\n{dup_state.output[:400]}]"
                )
                self._notify(
                    "tool_done", name=call.tool, args=call.args, ok=dup_state.ok,
                    output=dup_state.output, call_id=dup_state.call_id,
                    duration_ms=dup_state.duration_ms,
                )
                continue
            result = outcomes[index]
            _log.info(
                "tool execution completed: name=%s ok=%s duration_ms=%s",
                call.tool,
                result.ok,
                states[index].duration_ms,
            )
            if not result.ok:
                any_failed = True
            state = states[index]
            self._record_evidence(call, result)
            self._sync_task_state(call, result)
            self._on_event("result" if result.ok else "error", result.output[:200])
            path = ""
            for key in ("path", "source", "destination", "file"):
                value = call.args.get(key)
                if value:
                    path = str(value)
                    break
            self._notify(
                "tool_done", name=call.tool, args=call.args, ok=result.ok,
                output=result.output, diff=result.diff, path=path,
                call_id=state.call_id, duration_ms=state.duration_ms,
            )
            results.append(f"{call.tool} -> {result.for_model()}")
        self._note_step_signature(calls, results)
        return results, any_failed
