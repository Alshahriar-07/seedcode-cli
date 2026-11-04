# Tool Implementation Matrix — SeedCode vs OpenCode

Phase 4 / 12 deliverable. Every entry was written from the actual source:
OpenCode tool plugins live in `OPENCODE/tmdp/packages/core/src/tool/plugin/`
(19 files); the SeedCode inventory below was captured live from
`seedcode.tools.TOOL_REGISTRY` (27 entries) during pass 2. "Verified" means
the SeedCode tool's runtime behaviour was inspected against OpenCode's
(templates, permission flow, output shape), not merely that a same-named
file exists.

Verified states are from passes 1–2; "Excluded" = deliberately not ported,
with the reason. Tests: baseline 939 → **982 passing** after pass 2.

## Filesystem

| OpenCode | SeedCode equivalent | Status | Notes |
| --- | --- | --- | --- |
| `read.ts` (Read) | `read_file` | Verified | Path-traversal guarded; line windows; encoding/line-ending preservation; output truncation at `MAX_OUTPUT_CHARS` before re-entering the model |
| `write.ts` (Write) | `write_file`, `create_file`, `append_file` | Verified | `create_file` refuses to clobber an existing file (v9.2.1 hardening); `write_file` overwrites deliberately with post-write verification |
| `edit.ts` (Edit) | `edit_file` | **Upgraded in pass 2** | OpenCode fuzzy-matching parity ported: quote/dash equivalence classes, unicode-space + whitespace-run collapse, per-line trailing whitespace, exact-once uniqueness in both tiers, `replace_all`, byte-exact replacement (lambda re.sub — no backreference interpretation). See `seedcode/tools/patch.py` |
| (insert helper) | `insert_in_file` | Verified | Exact-once anchor, before/after positioning, verified read-back; OpenCode has no direct equivalent (its Edit covers it) |
| `patch.ts` (Apply Patch) | not ported (by design) | Excluded | OpenCode ships a real patch applier (`packages/patchmark`); SeedCode deliberately keeps exact/fuzzy-match editing because model-generated unified diffs malform (line-number drift) and convert into retry loops. Documented trade-off, not an oversight |
| Delete | `delete_file`, `delete_directory` | Verified | Permission-gated, `confirm_action` surfaced |
| Create directory | `create_directory` | Verified | Parent creation, workspace floor enforced |
| List files | `list_dir` (+ `list_directory` alias) | Verified | Alias shares implementation (tested in `tests/test_v921_agent_tools.py`) |
| `glob.ts` (Glob) | `find_files` | Verified | Pattern search over the workspace tree; read-only |
| File information | `inspect_process` covers process state; no separate `stat` tool | Excluded | `read_file` + `list_dir` cover the model's actual needs; a `stat`-only tool adds schema surface without a use case |

## Search

| OpenCode | SeedCode equivalent | Status | Notes |
| --- | --- | --- | --- |
| `grep.ts` (Grep) | `search_text` (+ `search_content` alias) | Verified | Regex/literal modes, file glob filtering, workspace-rooted |
| `glob.ts` | `find_files` | Verified | See above |
| (code search) | `project_index` | Verified (SeedCode-specific) | Symbol/project outline; no OpenCode counterpart (OpenCode uses an LSP via its plugin system instead) |

## Development

| OpenCode | SeedCode equivalent | Status | Notes |
| --- | --- | --- | --- |
| `shell.ts` (Bash/Shell) | `run_command` | Verified | Real subprocess, workspace containment, per-command `timeout_s` (clamped, with partial output fed back — same semantics class as OpenCode's shell timeout), background tasks with `get_command_output` / `check_exit_code` / `inspect_process`, structured per-line `terminal_output` events while running |
| Git status/diff/history | `git` | Verified | Read-only subcommands first-class; mutating subcommands go through the permission gate (`mutates=True`) |
| Test execution | `run_tests` | Verified | Detects runner (pytest/npm test/…), reports the REAL suite result; verified for passing and failing suites |
| Project detection | `project_index` | Verified (partial) | Detects/populates project layout; no explicit "dependency inspection" tool — `read_file` of lock files covers it today |
| `artifact.ts` | — | Excluded | OpenCode artifact rendering (custom UI channel). SeedCode delivers diffs/examples as text + `diff` fields on tool results |
| `file-diff.ts` | diff output on every mutating tool | Verified (shape matches intent) | Each mutating `ToolResult` carries a unified `diff`; the UI/conversation show it |

## Agent

| OpenCode | SeedCode equivalent | Status | Notes |
| --- | --- | --- | --- |
| `subagent.ts` (Task/subagent) | `subagent` | **Implemented in pass 2** | Fresh child `AgentEngine` (isolated message history), same `PermissionManager` (delegation never grants duties), depth guard = 1 (children cannot spawn further subagents), failure isolation as `ToolError`, per-profile provider/model routing, child's final report is the tool result. See `seedcode/tools/subagent.py` + `AgentEngine._spawn_subagent` |
| `todo.ts` (Task tracking) | `tests/test_task_flow.py` task graph + taskpanel UI | Verified (UI-adjacent) | SeedCode's Code Mode session owns a real task graph with acceptance criteria and verified completion (state machine, not model claims); the `todo` tool file itself is not ported — the capability exists through the session/task machinery |
| Context retrieval | `read_file`/`search_text`/`project_index` | Verified | No separate tool needed |
| Session operations | app-level session handling (`/sessions`, stores) | Verified | Outside the tool registry |
| `question.ts` | app-level dialogs (PermissionManager `confirm_action`, menu UI) | Partial | OpenCode marks/user-prompts tool; SeedCode uses its own dialog components — functionally equivalent prompting, different surface |
| `schedule.ts` | not ported | Excluded | Deferred/background scheduling server; no user demand and heavy platform surface |
| `skill.ts` | plugins/hook system (`seedcode/tools/hooks.py`) | Partial | SeedCode extensibility is via plugins + lifecycle hooks (pass 1); no OpenCode "skills" naming/branding |
| `opencode.ts` | n/a | Excluded | Branding/product-info tool of OpenCode; not applicable to SeedCode |
| `image-generate.ts` | not ported | Excluded | Requires an image-generation backend; not part of a coding agent's core |
| `mcp-resource.ts` | not ported | Excluded | MCP server integration is a separate subsystem; plugins provide the extensibility path for now |

## Information

| OpenCode | SeedCode equivalent | Status | Notes |
| --- | --- | --- | --- |
| `websearch.ts` | `web_search` | Verified | Real search backend; results truncated for context safety |
| `webfetch.ts` | `web_fetch` | Verified | URL retrieval with size caps; read-only |

## Extensibility (pass 1)

| Capability | Status | Notes |
| --- | --- | --- |
| Custom tools (plugin `.py` files register into `TOOL_REGISTRY`) | Verified | `seedcode/tools/hooks.py` plugin loader: project `.seedcode/plugins/` + user `plugins/` |
| Lifecycle hooks (`before`/`after` per tool call) | Verified | `HookManager.run_before/run_after`; veto contract: only explicit `False` or a raised exception vetoes; results recorded with real ok/output/diff/exit_code |
| Per-profile tool whitelists | Verified | `AgentProfile.tools` frozenset enforced in `AgentEngine._execute_one`; native schemas exclude them so the model cannot even be offered the tool |
| `subagent` delegation tool | Implemented (pass 2) | See Agent category above |

## Duplicate-name policy

OpenCode and SeedCode occasionally solve the same need with different tool
names (`Read`→`read_file`, `Glob`→`find_files`). No duplicate tools were
added where a working equivalent already exists; the contract aliases
(`list_directory`, `search_files`, `search_content`) exist for model ergonomics
and share implementations (asserted by tests, not by name similarity).
