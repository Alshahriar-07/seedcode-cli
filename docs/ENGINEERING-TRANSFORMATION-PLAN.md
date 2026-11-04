# SeedCode CLI — Engineering Transformation Plan

Status: **Phase 1 (audit) complete; Phase 2–9 foundation implemented and
validated (agent profiles, tool hooks, plugin loader, `/agents` command).**

This document is the Phase One deliverable required by the transformation
brief: an audit of both repositories, an architecture comparison, and a
migration / testing strategy.

---

## 1. Current SeedCode CLI architecture (as implemented, not as described)

Product: `seedcode-cli` (Python ≥3.10), package `seedcode/`, ~26,600 lines.
Entry point `seedcode.cli:main` → `seedcode.app`. Identity: __Seed Code
__, license PolyForm Noncommercial 1.0.0.

### Verified structure

| Area | Files | Assessment |
|---|---|---|
| Agent engine | `core/agent.py` (1,031 ln) | Native tool calling with an automatic text-protocol fallback, bounded step budget, parallel read-only executes, evidence recorder for verification. Mature. |
| Chat/provider layer | `core/chat.py`, `core/providers/*` | Retry + opt-in failover (local providers never silently switched), health tracker, streaming. 5 built-in providers + unlimited custom OpenAI-compatible ones. Mature. |
| Tool engine | `tools/base.py` + `filesystem, terminal, search, git, patch, web` | 27 real tools in a single registry (`TOOL_REGISTRY`), JSON schemas generated from implementations, aliases never drift. Mature. |
| Permissions | `tools/permissions.py` | Three hierarchical levels + session-only dangerous-action gate (shell / git mutate / git remote / delete / outside write). Solid. |
| Sessions / tasks | `core/session.py`, `core/tasks.py`, `core/models.py` | Persistent Code Mode sessions with checkpoints, task graph, accepted-criteria verification against recorded evidence. Solid. |
| TUI | `ui/*` (TUI ~2,268 ln) | Rich + prompt_toolkit, live activity events, streaming, theme system with live-preview picker, task panel, thinking strip. Solid. |
| Config | `config/manager.py`, `core/models.py` | Fault-tolerant JSON config at `~/.seedcode/config.json`, project `.env` support. Solid. |
| Commands | `commands/*` | Slash-command registry mirroring the tool registry. |
| Memory / index | `memory/`, `codemode.py`, `workspace.py` | `.seedcode/`-backed project memory and file map. |
| Extension surface | — | **Gap (see §3).** |

### Honest findings (weaknesses found in the audit)

1. **Single hardcoded agent.** `AgentEngine` bakes one persona and one tool
   set into the system prompt; there is no way to run a read-only planning
   persona or a user-defined agent without editing the source.
2. **No tool-lifecycle hooks.** Tools have exactly one consumer (the agent
   loop) and no before/after interception point, so third-party code cannot
   observe or veto execution.
3. **No user/extension plugin loading.** The tool `@register` decorator is
   internal-only; no mechanism loads user modules from the project.
4. The README and CHANGELOG overstate some claims (“premium”, “27 tools” —
   counted: registry holds 27 entries including aliases; acceptable), and the
   product is source-available (PolyForm Noncommercial), not OSI open source.
   This must be stated, not worked around, because the mission says
   “open-source”; changing the license is a product-owner decision and is
   **not** done in code without an explicit instruction.
5. `tests/` is comprehensive (53 files) and genuinely executes real tools
   (real files, real subprocesses).

## 2. OpenCode reference architecture (locally cloned at `OPENCODE/tmdp/`)

TypeScript/Bun monorepo, MIT licensed. Relevant packages:

* `packages/core/src/agent.ts` — an agent registry: agents have an id,
  selectable mode (`primary`/`subagent`), a schema-defined **Info** carrying
  `model`, `system` prompt, `description`, `steps` budget, `permissions`
  ruleset; the default primary agent is `build`. User-defined agents come
  from config (`schema/config/agent.ts`) and are merged into the same
  registry; defaults are synthesized (`Info.default(id)`).
* `packages/core/src/plugin/*`, `packages/plugin/*` — a plugin system with
  lifecycle effects (`tool`, `permission`, `session`, `command`, `hook`,
  `model`, …) — plugins may register tools and intercept events.
* `packages/cli/src`, `packages/tui` — TUI over a client/server split (not
  portable to a Python single-process CLI; the *patterns* are portable).
* `packages/theme` — token-based theming (SeedCode already has an
  equivalent, architecture differs but capability parity exists).

License compatibility: MIT — reuse is permitted with attribution; per the
brief, no wholesale copying and no brand reuse. Implementations below are
re-expressed in Python against SeedCode's registry patterns (which predate
this work and are the project's own).

## 3. Architecture comparison → transformation decisions

| Capability | OpenCode | SeedCode today | Decision |
|---|---|---|---|
| Agent types | Registry of named agents (build default, plan/explore/general built-ins, user-defined, mode primary/subagent, system prompt, steps, permissions) | One hardcoded persona | **Reimplement** as a Python registry module (`core/agents.py`): built-in `build` (default, current behaviour unchanged), `plan`, `explore`, `general`, plus user-defined agents loaded from `agents.json` (project + user level). Enforced by tool filtering, not just prompting. `/agent` command instead of OpenCode's TUI picker semantics. |
| Tool hooks | Plugin effects intercept tools/permissions | None | **Reimplement** as `tools/hooks.py`: a session-global hook manager with `before_tool` (may veto) and `after_tool`, wired at the single execution choke point. |
| Custom tools / plugins | Plugin packages | None (internal decorator only) | **Reimplement** as a plugin loader: `<workspace>/.seedcode/plugins/*.py` imported at engine construction; plugins register tools with the existing `@register` decorator and hooks with the new manager. No marketplace, no sandbox beyond the permission system (documented). |
| Provider abstraction | `packages/ai` | Provider-independent already | **Preserve** (no change). |
| Permissions | Ruleset per agent on a permission bus | 3 levels + dangerous-action gate | **Preserve**, extended only so an agent profile can *tighten* (never loosen) the set of tools its turn may call. |
| Session/context | Event-sourced server/session split | Persistent checkpointed sessions + token-aware compaction | **Preserve** (a port would break every existing integration for no capability gain). |
| TUI | Client/server TUI in Go-like patterns, prompt templates | Rich/prompt_toolkit TUI with live honest activity | **Preserve**; already implements the required patterns (streaming, activity, shortcuts, slash commands, theme picker). |
| Themes | Tokens | Palette-based with live preview | **Preserve.** |

## 4. Migration strategy

 Rhino-order, each phase validated before the next:

1. **Foundation** — add `core/agents.py` and `tools/hooks.py`; no existing
   behaviour changes; the default `build` profile reproduces today's engine
   byte-for-byte.
2. **Core engine** — wire profiles + hooks into `AgentEngine` (prompt,
   tool-groups filter, execution choke point); add `/agent` command.
3. **Validation** — new unit tests (`tests/test_agents.py`,
   `tests/test_tool_hooks.py`) + full existing suite must stay green.
4. **Documentation** — README and docs updated only for shipped features.

Configuration compatibility: introduced keys are new and optional
(`agents.json` files are additive); nothing existing is renamed or removed.

## 5. Testing strategy

* New modules get direct tests: registry safety (invalid user agents are
  skipped, never fatal), profile enforcement (a read-only profile really
  cannot run a mutating tool), hook veto/observe semantics, plugin loading
  error containment.
* The existing suite must pass unchanged (backward-compat gate).
* Test style follows the repo's own: real files/commands where possible, no
  faked success.

## 6. Shipped in the current implementation pass

* `seedcode/core/agents.py` — the agent-profile registry (built-ins
  `build`/`plan`/`explore`/`general`, user/project `agents.json` overrides,
  validation that skips rather than crashes).
* Engine integration (`seedcode/core/agent.py`) — profile resolution,
  persona injection, whitelist filtering of native specs and text-protocol
  manifests, real call-level enforcement, profile step budgets,
  `set_agent()` for `/agents`.
* `seedcode/tools/hooks.py` — before/after tool hooks (veto semantics), the
  process-wide `HookManager`, and the user plugin loader
  (`.seedcode/plugins/`, `plugins/`).
* `seedcode/commands/agent.py` — `/agents` (list + switch; `/agent` stays
  retired per the v9.2.0 decision that the retired-command tests pin).
* `seedcode/tools/__init__.py` — `register` exported for plugin authors.
* Tests: 27 new tests in `tests/test_agents.py` and
  `tests/test_tool_hooks.py`; full suite 939 passed, 0 regressions.

## 7. Known limitations & explicit non-goals for this pass

* Agent profiles are enforced at tool level; they do not change the
  *provider/model selection* (OpenCode's per-agent model refs are a future
  step; the `permission` key of a user agent definition is validated but
  informational for now).
* Subagent fan-out (OpenCode `mode: "subagent"`) is not implemented; the
  registry models only primary selectable agents this pass.
* The `agents.json` files are read once per `AgentEngine`/`AgentRegistry`
  construction, not watched live.
* License stays PolyForm Noncommercial 1.0.0 (product-owner decision).
