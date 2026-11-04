"""Agent profiles: selectable personas over ONE unified agent engine.

OpenCode reference: ``packages/core/src/agent.ts`` + ``packages/schema/src/
agent.ts`` — agents are entries in a registry carrying a name, description,
system-prompt text, an optional steps budget and a capability restriction,
with a default primary agent (``build``) selectable in the UI. Reimplemented
here against SeedCode's own registry idioms (the same pattern the
slash-command and tool registries use); no OpenCode code is copied.

A profile is *not* a mode switch. The engine stays one tool-driven agent; a
profile only (a) appends persona/context instructions to the system prompt,
(b) tightens the set of tools that profile's turns may call, and (c) can cap
the turn's step budget. The default profile ``build`` reproduces the
engine's previous behaviour exactly, so existing users see no change.

Sources, in resolution order for the same id (later wins):
* built-in agents defined below;
* user-level ``<app_dir>/agents.json``;
* project-level ``<workspace>/.seedcode/agents.json``.

An invalid user definition is never fatal: it is skipped with a logged
reason, and the built-ins stay usable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..tools.permissions import PermissionLevel
from ..utils.logger import get_logger
from ..utils.text import safe_text

_log = get_logger("agents")


@dataclass(slots=True, frozen=True)
class AgentProfile:
    """One selectable agent persona.

    ``tools`` optionally names the ONLY tools this profile may run (a real
    enforcement filter — the engine refuses other calls, and native tool
    schemas/manifests exclude them, so the model cannot even be offered
    them). ``None`` means every tool in the visible groups (the normal
    engine behaviour). ``groups`` optionally narrows the advertised tool
    groups. ``steps`` optionally caps the per-turn step budget.
    """

    id: str
    name: str
    description: str
    #: Appended to the system prompt (persona, focus, working style).
    system: str = ""
    #: Tool whitelist, by exact registry name (None = no extra restriction).
    tools: frozenset[str] | None = None
    #: Restricted tool-group set for the manifest/specs (None = engine decides).
    groups: tuple[str, ...] | None = None
    #: Optional per-turn step-budget cap (None = engine default).
    steps: int | None = None
    #: Optional per-agent model routing (OpenCode agent schema parity): when
    #: set, a turn with this profile — including any subagent spawned from a
    #: task delegated to it — is served by this provider/model instead of the
    #: session's active one. ``None`` keeps the session routing unchanged.
    provider: str | None = None
    model: str | None = None
    #: Where the definition came from (builtin / user / project).
    source: str = "builtin"


def _freeze(names: Any) -> frozenset[str] | None:
    """A validated tool-name whitelist, or None."""
    if names is None:
        return None
    if isinstance(names, str):
        names = [names]
    if isinstance(names, (list, tuple, set)) and all(
        isinstance(n, str) and n.strip() for n in names
    ):
        return frozenset(n.strip().lower() for n in names)
    return None


# Read-only tools: what the inspection-only profiles (plan / explore) may run.
_READ_TOOLS = frozenset(
    {
        "read_file",
        "list_dir",
        "list_directory",
        "create_directory",
        "find_files",
        "search_files",
        "search_text",
        "search_content",
        "project_index",
        "git",
        "get_command_output",
        "check_exit_code",
        "inspect_process",
        "run_tests",
    }
)

#: The built-in profiles. ``build`` is the default and is behaviour-neutral.
BUILTINS: dict[str, AgentProfile] = {
    "build": AgentProfile(
        id="build",
        name="Build",
        description=(
            "The default unified agent: understands the request, plans, "
            "edits the project with real tools, runs commands and verifies "
            "the result."
        ),
        system=(
            "Fulfil the user's request end-to-end: analyse, plan, make the "
            "minimal real changes, verify them, then report exactly what "
            "happened."
        ),
        tools=None,
        steps=None,
    ),
    "plan": AgentProfile(
        id="plan",
        name="Plan",
        description=(
            "Read-only strategist: investigates the project, then returns a "
            "concrete, ordered implementation plan without changing any file."
        ),
        system=(
            "Produce a concrete implementation plan. You must NOT change any "
            "file or run mutating commands: use only inspection tools "
            "(read_file, list_dir, find_files, search_text, git, "
            "project_index) to ground the plan in the real code. Deliver: "
            "the goal, the files that need changing and the specific change, "
            "the order of operations, risks, and how to verify each step. Do "
            "not implement — the user switches back to the build agent to "
            "execute."
        ),
        tools=_READ_TOOLS,
        steps=10,
    ),
    "explore": AgentProfile(
        id="explore",
        name="Explore",
        description=(
            "Read-only investigator: maps a codebase, finds where a feature "
            "lives or where a bug likely hides, without modifying anything."
        ),
        system=(
            "Investigate the codebase and report findings. You must NOT "
            "change files or run mutating commands: answer with facts you "
            "verified with the inspection tools (paths, line ranges, entry "
            "points, dependencies between files, data flow). Cite exact "
            "paths and line numbers for every claim. Prefer search_text/"
            "find_files over reading whole files."
        ),
        tools=_READ_TOOLS,
        steps=10,
    ),
    "general": AgentProfile(
        id="general",
        name="General",
        description=(
            "General-purpose agent: full tool access for development "
            "requests that go beyond one project's code (scripts, "
            "environments, documents)."
        ),
        system=(
            "Handle general development work with the full tool set. Apply "
            "the build agent's discipline: understand first, targeted edits, "
            "run real commands, verify, and report honestly."
        ),
        tools=None,
        steps=20,
    ),
}

DEFAULT_AGENT_ID = "build"


# ---------------------------------------------------------------------------
# user-defined agents
# ---------------------------------------------------------------------------
def _agents_files(workspace: Path | None) -> list[tuple[Path, str]]:
    """Candidate agent-definition files, lowest precedence first."""
    candidates: list[tuple[Path, str]] = []
    try:
        from ..utils.helpers import app_dir

        candidates.append((Path(app_dir()) / "agents.json", "user"))
    except OSError:
        pass  # an unwritable home must not break agent loading
    if workspace is not None:
        candidates.append((Path(workspace) / ".seedcode" / "agents.json", "project"))
    else:
        try:
            from ..codemode_state import codemode_state

            ws = getattr(codemode_state(), "workspace", None)
            if ws:
                candidates.append((Path(ws) / ".seedcode" / "agents.json", "project"))
        except Exception:
            pass  # no codemode state: project agents are simply absent
    return candidates


def _parse_agents_file(path: Path, source: str) -> dict[str, AgentProfile]:
    """Parse one ``agents.json``; a bad entry is skipped with a reason, not fatal."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        _log.warning("%s: not valid JSON (%s) — agents skipped", path, exc)
        return {}
    if not isinstance(data, dict):
        _log.warning("%s: expected a JSON object of agents — skipped", path)
        return {}
    profiles: dict[str, AgentProfile] = {}
    for raw_id, entry in data.items():
        try:
            aid = str(raw_id).strip().lower()
            if not aid:
                continue
            if not isinstance(entry, dict):
                raise ValueError("agent definition must be a JSON object")
            if entry.get("disabled") is True:
                continue
            name = str(entry.get("name") or aid).strip()
            description = safe_text(str(entry.get("description") or "").strip())
            system = safe_text(
                str(entry.get("system") or entry.get("prompt") or "").strip()
            )
            if not system:
                raise ValueError('an agent needs a "system" persona instruction')
            tools = _freeze(entry.get("tools"))
            if tools is None and entry.get("tools") is not None:
                raise ValueError('"tools" must be a list of tool names')
            groups_raw = entry.get("groups")
            groups: tuple[str, ...] | None = None
            if groups_raw is not None:
                if isinstance(groups_raw, str):
                    groups_raw = [groups_raw]
                if not isinstance(groups_raw, list) or not all(
                    isinstance(g, str) and g.strip() for g in groups_raw
                ):
                    raise ValueError('"groups" must be a list of group names')
                groups = tuple(str(g).strip().lower() for g in groups_raw)
            steps = entry.get("steps")
            if steps is not None:
                if isinstance(steps, bool) or not isinstance(steps, int) or steps < 1:
                    raise ValueError('"steps" must be a positive integer')
            floor_raw = str(entry.get("permission") or "workspace").strip().lower()
            PermissionLevel.parse(floor_raw)  # validate the level early
            provider_raw = str(entry.get("provider") or "").strip()
            model_raw = str(entry.get("model") or "").strip()
            profiles[aid] = AgentProfile(
                id=aid,
                name=name,
                description=description or "(user-defined agent)",
                system=system,
                tools=tools,
                groups=groups,
                steps=steps,
                provider=provider_raw or None,
                model=model_raw or None,
                source=source,
            )
        except (TypeError, ValueError) as exc:
            _log.warning("%s: agent '%s' skipped: %s", path, raw_id, exc)
            continue
    return profiles


class AgentRegistry:
    """Built-in + user-defined agent profiles for one session/workspace.

    A user or project definition of the same id overrides the built-in (a
    team can specialise ``build`` for their repository). The built-ins are
    never mutated.
    """

    def __init__(self, workspace: Path | None = None) -> None:
        self._overrides: dict[str, AgentProfile] = {}
        for path, source in _agents_files(workspace):
            self._overrides.update(_parse_agents_file(path, source))

    def list(self) -> list[AgentProfile]:
        """All selectable profiles, the default first."""
        merged: dict[str, AgentProfile] = dict(BUILTINS)
        merged.update(self._overrides)
        default = merged.pop(DEFAULT_AGENT_ID, None)
        ordered = sorted(merged.values(), key=lambda a: (a.source != "builtin", a.id))
        return ([default] if default else []) + ordered

    def get(self, agent_id: str | None) -> AgentProfile | None:
        if not agent_id:
            return None
        aid = agent_id.strip().lower()
        if aid in self._overrides:
            return self._overrides[aid]
        return BUILTINS.get(aid)

    def resolve(self, agent_id: str | None) -> AgentProfile:
        """The effective profile for a request (default when unknown)."""
        profile = self.get(agent_id)
        if profile is None:
            if agent_id and agent_id.strip():
                _log.info("unknown agent '%s' -> default build profile", agent_id)
            return BUILTINS[DEFAULT_AGENT_ID]
        return profile


def describe(profile: AgentProfile) -> str:
    """One line of honest metadata for selection lists (/agent)."""
    bits: list[str] = []
    if profile.tools is not None:
        bits.append(f"{len(profile.tools)} whitelisted tools")
    if profile.steps is not None:
        bits.append(f"≤{profile.steps} steps/turn")
    routing = " / ".join(v for v in (profile.provider, profile.model) if v)
    if routing:
        bits.append(f"model {routing}")
    if profile.source != "builtin":
        bits.append(profile.source)
    tail = f" ({', '.join(bits)})" if bits else ""
    return f"{profile.name} ({profile.id}) — {profile.description}{tail}"
