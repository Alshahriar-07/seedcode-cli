# Agent Engine Report — Deep Comparison Against OpenCode

Phase 3 / 6 / 7 / 10 deliverable (pass 2). Everything below was written
from the real sources: OpenCode `packages/core/src/agent.ts` +
`packages/core/src/tool/plugin/*.ts` + `packages/schema/src/agent.ts`, and
SeedCode `seedcode/core/agent.py`, `seedcode/core/calls.py`,
`seedcode/core/agents.py`, `seedcode/tools/subagent.py`,
`seedcode/tools/terminal.py`.

## The user-visible workflow (mission diagram → reality)

The required loop — USER INPUT → analysis → context → agent selection →
planning → model request → tool calls → results → continuation →
verification → final response — is genuinely implemented and no longer a
single-shot question/answer:

* **Deterministic request classification** (`classify`) runs before any
  model request and decides context scope and tool groups (v9.1.1 intent).
* **Profiles** (`seedcode/core/agents.py`) supply persona/system prompts,
  tool whitelists and step budgets. `build` is the default and is
  behaviour-neutral; `plan`/`explore` are read-only *enforcement*, not
  convention (the engine refuses disallowed calls in `_execute_one`).
* **The engine keeps going**: `_run_turn` loops steps until the model stops
  calling tools or budgets/text-protocol fail-safes trigger
  (`MAX_STEPS=15`, Code Mode `60`, `_MAX_CALLS_PER_STEP=8`). The plan/Edit/
  verify behaviour is covered by real end-to-end tests
  (`tests/test_v910_agent_execution.py`: write files, edit them, run the
  verifier — with the real tools and real filesystem).
* **Verification before completion**: Code Mode sessions verify tasks
  against `TurnEvidence` (files actually inspected, commands actually run)
  — "TASK COMPLETE" claims without observed work are rejected in tests.

## Tool calling (native + text protocol)

* **Native first**: providers supporting function calling get real JSON
  schemas (`group`, `types` fields on each `Tool`; `types` distinguishes
  boolean/integer args). The engine tracks a per-session `_native` state:
  `None` untried → `True` working → `False` fell back to the text protocol.
* **Text protocol fallback** parses `{"tool": ..., "args": {...}}` blocks
  from model text (`parse_tool_calls`), with per-call `error` so a malformed
  block is fed back to the model instead of crashing the loop.
* **Both paths verified by tests**, and the OpenRouter wire parser
  (`_iter_tool_stream`) is covered directly.

## Tool-call state management (new in pass 2)

OpenCode stores each tool call under its provider call id in the session
(`ToolCallRecord` in SeedCode models is the conversation-level twin). Pass 2
adds the missing execution-state layer, `seedcode/core/calls.py`:

* **Unique ids**: provider id when supplied, generated `agent-N` otherwise;
  collisions get a `#seq` suffix.
* **Lifecycle statuses** recorded from what actually happened: `pending →
  running → completed | failed | blocked | deduped`. `blocked` is detected
  from the real gate outcome (permission/profile/hook refusal), not guessed.
* **Real timing**: `started_at`/`finished_at` (monotonic), `duration_ms` per
  call; the `tool_done` observer payload carries `call_id` and
  `duration_ms` for the UI.
* **Duplicate prevention**: a provider call id already *finished* in the
  same turn is never executed again — the recorded result is replayed with a
  `[DUPLICATE … do not call it again]` prefix so the model does not reason
  as if the call were new. Text-protocol calls carry no ids and are
  intentionally exempt (a repeated read may be a legitimate re-check).
  Verified in `tests/test_tool_calls.py` with a counting wrapper around the
  real registry runner.

## Parallel and sequential execution

* All-read-only steps (≥2 calls) run in a `ThreadPoolExecutor`; any mutating
  call forces the whole step sequential. File-conflict safety comes from the
  permission floor and the `subagent` tool being `mutates=True`.
* Ordering, per-call error independence and the `tool_done` payload are
  asserted by tests.

## Cancellation and timeouts (verified state, not fluff)

* `run_command` keeps its own timeouts: per-command `timeout_s` clamped,
  partial output fed back on expiry, real tracked background processes —
  the semantic parity OpenCode's `shell` tool provides.
* Blanket per-tool-call wall-clock timeouts were NOT added in pass 2: the
  only tool that can legitimately run long is `run_command`, which already
  owns a correct timeout, and a generic kill-timer would add thread-leak
  complexity without claiming a user-visible failure it prevents.
  Recorded as a deliberate non-change.
* Turn interruption: the TUI's interrupt path exists (`input.py` interrupt
  handling) and seats above the engine's step loop.

## Subagent orchestration (new in pass 2)

Ported semantics from OpenCode's `subagent.ts`:

* **Real delegation**: `subagent` tool → `AgentEngine._spawn_subagent` → a
  fresh child `AgentEngine` with an empty message history (system prompt
  only), running the delegated `task` prompt. The child's whole transcript
  is a side conversation; only its final report enters the parent loop.
* **Depth guard** = 1 hop (OpenCode-style refusal with actionable text).
  Only the main engine owns the session delegate; it re-registers itself
  after a delegation returns, so a child can never hijack it.
* **Permissions are inherited, never granted**: the child shares the SAME
  `PermissionManager` instance. A read-only floor stays read-only.
* **Failure isolation**: provider crashes, budget exhaustion and tool
  breakage inside the child surface as plain `ToolError`s; the parent's turn
  survives and can reason over the report.
* **Model routing per agent** (below) applies to children: declaration in
  `agents.json` routes the delegated worker through a different backend.
* Multi-hop/nested delegation: refused (default limit, same as scoped
  decision in pass 2 scoping). Background delegation (OpenCode's
  `runInBackground`) is NOT ported — recorded as a limitation.

## Per-agent model configuration (new in pass 2)

`AgentProfile.provider` / `AgentProfile.model` parsed from `agents.json`
(user-level and project-level), validated leniently (blank → None). When
set, `_spawn_subagent` deep-copies the session `AppConfig` and applies the
overrides — the session's own selection is untouched (the deep copy matters
because the model setter writes the active provider's entry). `describe()`
lists routing inline. Built-ins ship without routing; users opt in.

## Context management

* Intent classification bounds context: the engine does not scan the whole
  workspace for out-of-scope asks.
* Tool outputs are truncated (`MAX_OUTPUT_CHARS = 12 000`) before re-entering
  the conversation, so a single cat-calls-a-monster-file cannot blow the
  context window; omitted char counts are shown.
* Repeat-read of the same file is NOT suppressed at engine level (a repeat
  may be a legitimate re-check after edits); Code Mode deduplicates
  inspected files in `session.state.inspected`.
* Compaction (`/compact`) exists at the command layer. Provider-specific
  per-model context budgets are NOT computed in pass 2 — token estimation
  is derived from truncation limits only; recorded as the honest gap.

## Commands and DX (pass 1 + pre-existing)

`/agents` selection (pass 1), `/help`, `/new`, `/sessions`, `/model`,
`/theme`, `/tools`, `/config`, `/compact`, `/exit` in the command registry
(raw lists verified in `seedcode/commands/`). Autocomplete exists in the
input layer. The pass-1 pinned test for a retired `/agent` alias stays
green.

## Test accounting (Phase 10)

Baseline after pass 1: **939 passing**. After pass 2: **982 passing**
(+43: 11 patch-fuzzy, 12 tool-call state, 20 subagent/profile-routing).
Every mission-listed new test category that is in scope of the pass-2 code
changes is covered (duplicate-call prevention, parallel reads, sequential
mutating steps, subagent failure, per-profile model routing, ambiguous fuzzy
edits, CRLF preservation, byte-exact replacement). TUI-specific rendering
tests and provider-wire cancellation tests remain at their pre-existing
coverage.
