"""Intent analysis: what a request actually needs (v9.1.1).

Agent Mode must understand a request *before* choosing tools. The single most
expensive mistake it can make is treating every prompt as a coding task: a
request to play a song on YouTube would then scan the workspace, read project
files, index the repository and inspect git — none of which the task needs.

This module is the deterministic, model-free classifier that prevents that.
It answers, in order:

1. **What kind of request is this?** — conversation, research (needs current
   information), coding, or a computer/browser action.
2. **Which capabilities does it need?** — internet, workspace files, terminal,
   desktop/browser. Only the ones genuinely required are turned on, so no
   context and no tool is used "because it was available".
3. **Should Chat Mode temporarily escalate to Agent execution?** — only when
   real execution is required, never merely because a prompt looks complex.

The classifier is keyword/pattern based on purpose:

* it is **deterministic** (the same prompt always classifies the same way),
* it costs **no tokens** and no extra model call,
* it is **testable** without a provider, and
* it **fails safe**: anything it cannot confidently classify is a
  conversation, which never touches the user's machine.

It is deliberately conservative about escalation. A coding *question* stays in
Chat Mode; only an actionable coding/computer request escalates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

__all__ = [
    "Intent",
    "IntentKind",
    "classify",
    "needs_internet_query",
    "TOOL_GROUP_FOR_INTENT",
]


class IntentKind(str, Enum):
    """What the user is asking for, at the capability level."""

    CONVERSATION = "conversation"  # answer directly, no machine access
    RESEARCH = "research"          # answer, but needs current information
    CODING = "coding"              # inspect/modify a project
    COMPUTER = "computer"          # drive the browser / desktop


#: The tool group an intent may advertise, beyond the always-on ``core`` group.
#: A coding request never sees the desktop/browser tools, and a browser request
#: never sees the web-search tools, so the advertised tool set stays minimal.
TOOL_GROUP_FOR_INTENT: dict[IntentKind, tuple[str, ...]] = {
    IntentKind.CONVERSATION: (),
    IntentKind.RESEARCH: ("web",),
    IntentKind.CODING: (),
    IntentKind.COMPUTER: ("desktop", "web"),
}


@dataclass(frozen=True, slots=True)
class Intent:
    """The resolved need behind one user request."""

    kind: IntentKind
    #: Capabilities implied by the request. Every one defaults off.
    needs_internet: bool = False
    needs_workspace: bool = False
    needs_terminal: bool = False
    needs_desktop: bool = False
    #: Whether Chat Mode should temporarily execute this as an Agent turn.
    executes: bool = False
    #: Short, user-facing explanation of why (shown only when escalating).
    reason: str = ""
    #: The likely subject/target (a search query, an app/site, a file).
    target: str = ""

    @property
    def is_conversation(self) -> bool:
        return self.kind is IntentKind.CONVERSATION

    @property
    def tool_groups(self) -> tuple[str, ...]:
        """Extra tool groups this intent may use (always excludes ``core``)."""
        return TOOL_GROUP_FOR_INTENT.get(self.kind, ())


# --- lexical signals ---------------------------------------------------------
# Each list is checked with word boundaries so "open" never matches "openssl"
# and "play" never matches "display".

_COMPUTER_ACTIONS = (
    "open", "launch", "play", "pause", "resume", "click", "double click",
    "type", "press", "screenshot", "scroll", "close", "focus", "switch to",
    "navigate", "go to", "browse", "sign in", "log in", "login", "log out",
    "logout", "send", "message", "minimise", "minimize", "maximise", "maximize",
    "drag", "copy", "paste", "select",
)

# Named applications / sites that make a computer action unambiguous. Typing a
# site name alone ("youtube") is a hint, not a command; the action verb is
# still required.
_COMPUTER_TARGETS = (
    "youtube", "spotify", "netflix", "whatsapp", "telegram", "discord", "slack",
    "gmail", "browser", "chrome", "firefox", "edge", "notepad", "calculator",
    "explorer", "desktop", "taskbar", "window", "cursor", "mouse", "keyboard",
    "clipboard", "vscode", "vs code", "steam", "twitter", "instagram", "tiktok",
)

_CODING_ACTIONS = (
    "fix", "implement", "refactor", "add", "create", "write", "edit", "modify",
    "update", "change", "rename", "delete", "remove", "move", "build", "run",
    "test", "debug", "install", "bump", "migrate", "optimise", "optimize",
    "document", "review", "commit", "revert", "patch", "scaffold", "generate",
    "configure", "wire", "hook up", "integrate", "extract", "split", "clean up",
    "reproduce", "resolve the bug",
)

_CODING_SUBJECTS = (
    "code", "bug", "error", "exception", "traceback", "function", "class",
    "module", "package", "repo", "repository", "project", "workspace",
    "provider", "agent", "test", "tests", "pytest", "api", "endpoint",
    "config", "configuration", "file", "files", "script", "dependency",
    "dependencies", "import", "database", "schema", "migration", "endpoint",
)

_TERMINAL_SIGNALS = (
    "run ", "execute", "pytest", "npm ", "pnpm ", "yarn ", "pip ", "python ",
    "git ", "docker ", "make ", "cargo ", "go test", "shell", "terminal",
    "command", "build the project", "run the tests", "start the server",
)

_QUESTION_STARTS = (
    "who", "what", "when", "where", "why", "how", "which", "whose", "whom",
    "is ", "are ", "does ", "do ", "did ", "can ", "could ", "would ", "should ",
    "will ", "explain", "describe", "tell me", "compare", "difference",
    "summarise", "summarize", "help me understand",
)

# Freshness / currency language: the model's internal knowledge cannot answer
# these reliably, so current information is fetched.
_RESEARCH_SIGNALS = (
    "latest", "current", "currently", "today", "now", "recent", "recently",
    "up to date", "up-to-date", "as of", "right now", "this week",
    "this month", "this year", "news", "release notes", "changelog",
    "price", "pricing", "cost", "stock", "weather", "score", "who won",
    "trending", "documentation", "docs for", "api docs", "look up",
    "search the web", "search online", "google", "browse the web", "online",
)

_FILE_RE = re.compile(
    r"(?<![\w/])(?:[\w.-]+/)*[\w.-]+\.(?:py|js|ts|tsx|jsx|json|toml|ya?ml|md|txt|"
    r"css|html|sh|bat|ps1|cfg|ini|env|sql|java|go|rs|c|cpp|h|hpp|rb|php|cs)\b",
    re.IGNORECASE,
)
#: Tokens that look like ``name.js`` but are product/runtime names, not files.
#: Without this, "the latest Node.js release" would be classified as a coding
#: task and the agent would read the workspace it does not need.
_PRODUCT_NAMES = frozenset(
    {
        "node.js", "next.js", "nuxt.js", "vue.js", "react.js", "three.js",
        "express.js", "d3.js", "backbone.js", "ember.js", "angular.js",
        "alpine.js", "solid.js", "svelte.js", "jquery.js", "moment.js",
        "electron.js", "stimulus.js", "preact.js", "chart.js", "tensorflow.js",
    }
)
_CODE_FENCE_RE = re.compile(r"```")
_SLASH_PATH_RE = re.compile(r"(?:\b[\w.-]+/)+[\w.-]+")
_WORD_RE = re.compile(r"[a-z0-9]+")


def _has_word(text: str, words: tuple[str, ...]) -> bool:
    """Word-boundary membership, tolerant of multi-word phrases."""
    for word in words:
        if " " in word:
            if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text):
                return True
        elif re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text):
            return True
    return False


def _first_word(text: str, words: tuple[str, ...]) -> str:
    for word in words:
        if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text):
            return word
    return ""


def _is_question(text: str) -> bool:
    """Whether the request asks for information rather than an action."""
    stripped = text.strip()
    if not stripped:
        return False
    if stripped.endswith("?"):
        return True
    lowered = stripped.lower()
    return any(lowered.startswith(start) for start in _QUESTION_STARTS)


def _references_files(text: str) -> bool:
    if _SLASH_PATH_RE.search(text):
        return True
    for match in _FILE_RE.finditer(text):
        if match.group(0).lower() not in _PRODUCT_NAMES:
            return True
    return False


def classify(text: str) -> Intent:
    """Resolve what ``text`` needs. Never raises; unknown input is a chat.

    The order of the checks encodes the priority of the signals: an explicit
    computer action on a known app is a computer task even if it contains the
    word "search"; a concrete file/code reference makes it a coding task; a
    pure question is a conversation (optionally with internet lookups).
    """
    raw = (text or "").strip()
    if not raw:
        return Intent(IntentKind.CONVERSATION, reason="empty request")

    lowered = raw.lower()
    words = _WORD_RE.findall(lowered)
    question = _is_question(raw)
    references_files = _references_files(raw)

    computer_verb = _first_word(lowered, _COMPUTER_ACTIONS)
    computer_target = _first_word(lowered, _COMPUTER_TARGETS)
    coding_verb = _first_word(lowered, _CODING_ACTIONS)
    coding_subject = _first_word(lowered, _CODING_SUBJECTS)
    terminal = _has_word(lowered, _TERMINAL_SIGNALS)
    research = _has_word(lowered, _RESEARCH_SIGNALS)

    # 1. Computer/browser action. Needs an action verb AND a target (an app,
    #    site, or the desktop/browser itself) — "search" alone is too weak.
    if computer_verb and computer_target:
        # "open src/main.py" is a coding action, not a UI action.
        if not references_files and not _CODE_FENCE_RE.search(raw):
            target = computer_target
            return Intent(
                IntentKind.COMPUTER,
                needs_desktop=True,
                needs_internet=True,
                executes=True,
                reason=f"browser/desktop action: {computer_verb} {target}",
                target=target,
            )

    # 2. Real internet access (search/fetch) explicitly requested.
    explicit_web = any(
        phrase in lowered
        for phrase in (
            "search the web", "search online", "google ", "browse the web",
            "look up online", "on the internet", "web search",
        )
    )

    # 3. Coding. An imperative coding verb, a concrete file reference, a code
    #    fence, or a coding subject paired with an action — but a *question*
    #    that merely mentions code stays conversational unless it names a file.
    actionable_coding = bool(coding_verb) or references_files or bool(
        _CODE_FENCE_RE.search(raw)
    )
    if coding_subject and not actionable_coding and _has_word(
        lowered, ("fix", "implement", "refactor", "add", "write", "run", "test")
    ):
        actionable_coding = True
    if actionable_coding and not (question and not references_files):
        return Intent(
            IntentKind.CODING,
            needs_workspace=True,
            needs_terminal=bool(terminal or coding_verb in ("run", "test", "build")),
            needs_internet=bool(research or explicit_web),
            executes=True,
            reason=(
                f"coding task: {coding_verb or 'file'} "
                f"{coding_subject or (raw.split()[0] if words else '')}".strip()
            ),
            target=coding_subject,
        )

    # 4. Research: a question (or lookup request) that needs current data.
    if research or explicit_web:
        query = _research_query(raw)
        return Intent(
            IntentKind.RESEARCH,
            needs_internet=True,
            # Research is answerable in Chat Mode: it does not execute anything.
            executes=False,
            reason="needs current information",
            target=query,
        )

    # 5. Everything else is a conversation — no machine access at all.
    return Intent(
        IntentKind.CONVERSATION,
        needs_internet=False,
        needs_workspace=False,
        executes=False,
        reason="conversational request",
    )


def _research_query(text: str) -> str:
    """A compact search query derived from the request (no model call)."""
    cleaned = re.sub(r"^(?:please\s+|can you\s+|could you\s+|hey\s+)", "", text.strip(), flags=re.I)
    cleaned = cleaned.strip().rstrip("?.!")
    return cleaned[:300] or text.strip()[:300]


def needs_internet_query(text: str) -> str:
    """The search query when ``text`` needs the internet, else ''."""
    intent = classify(text)
    if intent.kind is IntentKind.RESEARCH and intent.needs_internet:
        return intent.target or text
    return ""
