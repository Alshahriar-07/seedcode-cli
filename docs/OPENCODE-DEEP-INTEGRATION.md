# OpenCode Deep Integration — feature-by-feature status

Phase 1 / 12 deliverable, pass 2. Reference repository:
`OPENCODE/tmdp/` (MIT). Product identity: SeedCode CLI — no OpenCode
branding, code, or assets were copied; behaviour was ported and
reimplemented in Python with SeedCode's own idioms.

## Repository structure as actually found

| Mission's guess | Reality in `OPENCODE/tmdp/` |
| --- | --- |
| `packages/opencode/` | not present; the core is `packages/core/` (agent.ts, session, tool plugins) |
| `packages/ui/` | present, but it's console/web UI, NOT the terminal UI; the TUI is `packages/tui/` (TypeScript, Ink-based) |
| `packages/console/` | web console app (content site + admin), not the agent runtime |
| `packages/plugin/` | plugin SDK surface (JS plugin contracts) |
| `packages/sdk/` | generated client SDK for the server API |
| `packages/core/` | the heart: agent execution, session logic, tool plugins |
| Patch engine | `packages/patchmark/` (real unified-diff runtime) |
| Agent schema | `packages/schema/src/agent.ts` (name, description, prompt, model ref, mode primary/subagent, temperature…) |
| Tool registry | `packages/core/src/tool/` with per-tool plugin files under `tool/plugin/` |

## Subsystem-by-subsystem integration matrix

| Subsystem | OpenCode implementation | SeedCode equivalent | Pass-2 status |
| --- | --- | --- | --- |
| Agent registry | `packages/core/src/agent.ts` + `schema/src/agent.ts` | `seedcode/core/agents.py` (pass 1): AgentProfile, built-ins build/plan/explore/general, agents.json overrides, AgentRegistry resolution | Verified |
| Message/tool-call lifecycle | Session messages store tool parts keyed by provider call id; retries/dedupes upstream | `seedcode/core/calls.py` (pass 2): unique ids, status lifecycle, real timing, duplicate call-id prevention | Implemented + tested |
| Tool execution loop | Model loops over tool calls until done; bash streaming with timeouts | `AgentEngine._execute_calls/_execute_one`: native + text protocol, all-read-only parallelism, mutates→sequential, truncation | Verified (pre-existing) + tested |
| Edit engine | `tool/plugin/edit.ts`: normalization (quotes, dashes, whitespace runs, trailing whitespace), exactly-once, replaceAll | `seedcode/tools/patch.py` (pass 2): fuzzy_pattern equivalence classes + whitespace-run coalescing + per-line trailing-whitespace join, exact tier first, uniqueness in both tiers, byte-exact lambda replacement, CRLF/encoding preservation, post-write verification | Implemented + tested |
| Patch engine | `packages/patchmark/` unified-diff applier | Deliberately not ported (documented trade-off: exact-match editing with actionable errors beats model-diff retry loops); `diff` output still produced per change | Excluded by design (see TOOL-IMPLEMENTATION-MATRIX.md) |
| Subagents | `tool/plugin/subagent.ts`: fresh child session, depth guarding, model override, background mode | `seedcode/tools/subagent.py` + `AgentEngine._spawn_subagent` (pass 2): fresh child engine, depth guard=1, shared PermissionManager, failure isolation, report passthrough, per-profile model routing | Implemented + tested (background mode excluded — limitation) |
| Per-agent model config | `schema/src/agent.ts` model ref per agent | `AgentProfile.provider/model` from agents.json (pass 2), applied to delegated children via deep-copied AppConfig | Implemented + tested |
| Permissions | Different mechanism (deny/allow lists, per-action asks per tool) | `seedcode/tools/permissions.py`: hierarchical levels (read_only < workspace < full_system), ActionGate confirmations, per-session memory | Verified (pre-existing, stronger invariants in some areas: level comparison is a single ordered check) |
| Extensibility | JS plugin SDK + MCP | `seedcode/tools/hooks.py` (pass 1): plugin tools + before/after lifecycle hooks, veto contract | Verified |
| Terminal TUI | `packages/tui/` (TS/Ink, own widget runtime) | `seedcode/ui/` (Textual-based; 22 components/modules verified) | Not rebuilt this pass — see TUI-REDESIGN-REPORT.md for the honest gap list |
| Session/context mgmt | Session compaction, summarization, per-message token accounting | Context bounded by intent classification + tool-output truncation; `/compact` at command layer; per-model token budgeting NOT computed | Partial (documented gap) |
| Commands | `packages/core/src/cli` + command registry | `seedcode/commands/` registry incl. `/agents` (pass 1) | Verified |
| Streaming | Server-sent events from server → client UIs | Provider stream events (`TextDelta`, `ThinkingDelta`, `ToolCallEvent`) rendered live by `TuiStreamRenderer` | Verified |
| Error recovery | Provider failover chains, retries | `seedcode/core/chat.py` failover chain, ToolError isolation per call, `TurnEvidence` verification against fabricated success claims | Verified |

## What pass 2 actually added (code, not prose)

1. `seedcode/core/calls.py` — tool-call state management (OpenCode
   session-tool parity): unique ids, timing, statuses, duplicate prevention.
2. `seedcode/tools/subagent.py` + engine wiring — real subagent delegation
   with OpenCode's depth guard and permission-inheritance semantics.
3. `AgentProfile.provider/model` — per-agent model routing.
4. `edit_file` fuzzy tier — OpenCode edit.ts matching parity behind the
   exact tier.
5. 43 new tests; suite 982 passing (baseline 939 preserved; nothing deleted
   or weakened).

## Honest remaining differences

* TUI is not a visual clone of OpenCode's current release (see
  TUI-REDESIGN-REPORT.md for the enumerated gaps and the screenshot plan).
* No unified-diff patch applier (design trade-off, documented).
* No background subagent execution; nested delegation limited to 1 hop.
* Per-model context-window budgeting is estimated, not provider-catalogue
  accurate.
* MCP server support and OpenCode's artifact/question/schedule/image tools
  are not ported (see the matrix's Excluded rows and reasons).

## License compliance

`OPENCODE/tmdp/` is MIT. Passes 1–2 did not copy OpenCode source files;
they reimplemented *behaviour* in Python (OpenCode's own docs describe the
mechanisms). No branding, logos, or non-code assets were reused. The
SeedCode PolyForm Noncommercial license is untouched.
