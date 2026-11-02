# Seed Code CLI v9.2.0 — Technical Report

**Version:** 9.2.0 · **Tag:** `v9.2.0` · **Built:** 2026-11-02 · **Platform:** Windows 11 x64
**Repository:** https://github.com/Alshahriar-07/seedcode-cli

---

## 1. Project Overview

Seed Code CLI is an autonomous AI coding agent for the terminal. The user types a
task; the agent analyses it, inspects the workspace, builds a plan, executes it
through real tool calls (terminal, files, search, research), verifies each result
and reports the outcome honestly. Version 9.2.0 completes the pivot to a single
unified coding agent and adds the two UI surfaces that make the agent's work
visible: a model-thinking panel and a right-side task plan panel.

## 2. Architecture Overview

The runtime is layered between the user, the model and the tools:

```
You > request
  → task understanding (deterministic intent classifier)
  → workspace inspection (project scanner)
  → task planning → TODO state (seedcode/core/tasks.py)
  → orchestrator loop (seedcode/core/agent.py: AgentEngine)
      → model provider (seedcode/core/providers/*)
      → tool registry + execution engine
      → result validation → next task
      → completion verification → final report
  → event stream (seedcode/core/events.py) → terminal UI (seedcode/ui/*)
```

Key properties, all verified by tests:

- Multiple sequential tool calls per turn; results are returned to the model and
  execution continues after success.
- Bounded iteration limits prevent infinite loops; Ctrl+C cancels cleanly.
- A tool failure is returned to the model as a failure; it never silently becomes
  a success.
- UI panels are driven exclusively by real orchestrator events — no fake
  animations, no fabricated logs.

## 3. Major Bugs Fixed

| Bug | Fix |
|-----|-----|
| `NameError: name 'active_workspace' is not defined` | The permission manager is constructed with the selected workspace. |
| `ImportError: AgentEngine` / entry-point drift | Import surface consolidated on `seedcode.core.agent.AgentEngine`. |
| Ollama connected but produced no visible thinking | The `thinking` field was dropped in the stream loop and never requested. Fixed: capability detection via `GET /api/show`, `think` flag sent only for capable models, `ThinkingDelta` events emitted, relayed and rendered. |
| Installer rejected `Seed Code CLI 9.2.0` | Robust semantic-version extraction from the version banner; accepts the exact displayed format, rejects wrong or malformed versions (regression-tested in `tests/test_installer_version.py`). |
| False completion reports | Completion verification is evidence-based: created files are re-read, modifications are confirmed, exit codes and test results are parsed; final status is `COMPLETED / PARTIALLY COMPLETED / FAILED / CANCELLED`. |

## 4. Agent Orchestration Design

`AgentEngine` owns the turn lifecycle. The text-protocol tool loop accepts
multiple ```tool blocks per model reply, dispatches exactly one tool call, feeds
the real result back and continues. A deterministic (model-free) classifier
chooses which context the turn needs; the orchestrator keeps task state in the
TODO manager and updates it only from observed outcomes — never from the model's
claim that it is finished.

## 5. Tool Calling Improvements

Central registry tools, each with schema, error format and real verification:

| Tool | Verified behaviour |
|------|--------------------|
| `run_command` | stdout/stderr/exit code/duration/cwd; PowerShell and POSIX; timeout handling |
| `read_file` | encoding-aware reads with line numbers; large-file safety |
| `write_file` / `edit_file` | create/update with diffs re-read after write |
| `list_dir` / `make_dir` / `move` / `copy` / `delete` / `exists` | workspace-boundary validated |
| `search` | code + filename search, venv/cache/build excluded |
| `research` / `web_search` | keyless internet search; query and sources surfaced as real events |
| `inspect_project` | structure, languages, dependency manifests, entry points |

Parameter validation happens before dispatch; a dispatch error returns the
actual error to the model. Workspace permissions (`PermissionLevel.WORKSPACE`)
prevent writes outside the selected workspace; dangerous actions ask first.

## 6. Ollama Improvements

- Reachability check (`GET /api/tags`) and model listing from the live server.
- Model availability validated before generation; auto-started server is never
  duplicated (`ensure_running` re-checks before launching).
- Streaming: partial chunks, malformed chunks, empty responses, connection
  interruptions and timeouts are distinguished and surface real errors.
- Capability detection from `GET /api/show`; `think` is requested only for
  models that report `thinking`, degrading silently otherwise.
- `ThinkingDelta` events stream the model's own reasoning to the UI.
- The user's selected provider is never silently switched; failover is opt-in.

## 7. TODO UI (right-side task plan panel)

New `seedcode/ui/taskpanel.py`, rendered in the eight-region TUI layout:

- States: `○` pending, `●` in progress, `✓` completed, `✗` failed, `↻` retrying.
- Driven only by orchestrator task events (`task_start`, `task_done`,
  `task_retry`, `task_failed`, plan updates); the current task is highlighted.
- Live `N / M completed` progress; `All tasks completed` only when all verified.
- Narrow-width graceful degradation; resizing re-fits without breaking the main
  conversation layout.

## 8. Thinking UI

New bounded, collapsible thinking panel:

- Shows only content the model itself exposes (`ThinkingDelta`); never invented,
  never mixed with the response, never exposed chain-of-thought that the
  provider does not publish.
- Bounded height (~25% of terminal, min/max clamped); responsive to resizing.
- Auto-scrolls while streaming, stops forcing bottom when the user scrolls up,
  resumes at bottom; collapses after the turn; older content preserved.

## 9. Completion Verification

- Created/modified files are verified by re-reading after the write.
- Terminal commands report the parsed real exit code; failures are shown `✗`.
- Test runs report real pass/fail counts.
- Multi-step tasks: all TODO items must be verified; unresolved failures are
  reported explicitly as `PARTIALLY COMPLETED` or `FAILED`.

## 10. Installer Improvements

- `setup.iss` verifies the installed exe by extracting the semantic version from
  the banner and comparing against the installer's own version.
- Both remote installers (`install.ps1`, `install.sh`) download the release's
  `SHA256SUMS.txt` and abort on a missing entry, unreachable checksum file or
  digest mismatch; verification is never bypassed.
- The staged `SHA256SUMS.txt` uses explicit LF newlines so Linux/macOS
  `sha256sum -c` and awk lookups never see stray CR bytes.

## 11. Test Results

Full suite (pytest, Windows 11, Python 3.12):

- **813 passed, 0 failed, 0 skipped** — 773 before this release plus 40 new
  regression tests:
  - `tests/test_v920_thinking_stream.py` — capability detection, `think` flag
    scoping, ThinkingDelta emission/relay/render, no-thinking models unaffected.
  - `tests/test_v920_task_panel.py` — panel rendering, all five task states,
    progress counts, event-driven updates, narrow-width behaviour.
  - mode-switch/resize structural test updated to pin the new layout.
- Shell/tools/codemode agent integration tests run the real tools in a temp
  workspace (create → run → verify, edit → verify).
- Ollama paths are covered by mocked HTTP tests; cloud providers are mocked.

## 12. Build Results (dist/release/9.2.0)

| Artifact | Size (bytes) | SHA256 verified |
|----------|-------------:|-----------|
| `SeedCode-CLI-9.2.0-windows-x64.exe` | 43,980,669 | ✅ OK |
| `SeedCode-CLI-Setup-9.2.0.exe` | 45,427,583 | ✅ OK |
| `seedcode_cli-9.2.0-py3-none-any.whl` | 333,932 | ✅ OK |
| `seedcode_cli-9.2.0.tar.gz` | 286,636 | ✅ OK |
| `seedcode-cli-9.2.0.tgz` | 6,399 | ✅ OK |
| `SHA256SUMS.txt` | 479 | 5/5 entries `OK` via `sha256sum -c` |

The standalone exe reports `Seed Code CLI 9.2.0` with the Seed Code icon
embedded; the Setup installer was compiled by Inno Setup 6 and verified the same
way. DIGESTS are real — generated by `stage_release.py` from the actual files
and re-verified with `sha256sum -c`.

## 13. Known Limitations

- Cloud providers (OpenRouter, FreeModel, AeroLink) were not live-tested in this
  environment; their request paths are covered by mocked tests.
- Ollama thinking appears only for models reporting the `thinking` capability.
- Model latency is model-bound: on local `qwen3:4b` a turn can take 30–300 s,
  which is inherent to the local model, not the CLI.
- DOCX generation requires `python-docx` (a local tool dependency only, not a
  runtime dependency of the CLI itself).
