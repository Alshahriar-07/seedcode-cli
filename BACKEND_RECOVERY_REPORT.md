# Seed Code CLI v9.2.0 — Backend Recovery Report

> **Status: historical.** This document records the v9.2.0 backend repair and is
> kept unchanged as evidence of that release. The current release is v9.2.1 —
> see [`docs/reports/SeedCode-CLI-v9.2.1-Technical-Report.md`](docs/reports/SeedCode-CLI-v9.2.1-Technical-Report.md).

Date: 2026-11-01
Scope: backend repair/stabilization of the existing codebase. No new UI features were added.

## 1. Root causes found

The v9.2.0 refactor removed the desktop/computer stack and the old Chat/Agent/Code
mode modules, but several live modules still referenced them:

| Live module | Broken reference | Effect |
|---|---|---|
| `seedcode/core/agent.py` | `self.permissions.desktop`, `..computer` imports, desktop prompt builder | **`AgentEngine` could not be constructed at all** (`AttributeError`) |
| `seedcode/app.py` | `from .core.modes import active_mode, mode_title` | Every agent task raised `ModuleNotFoundError` before running |
| `seedcode/commands/doctor.py` | `_check_ocr()` (removed) | `/doctor` always crashed with `NameError` |
| `seedcode/core/internet.py` | DDG parser split on `result__body`, while the title anchor precedes it | Web search always returned 0 results |
| `seedcode/core/providers/ollama.py` | No check for an empty stream | Empty model output surfaced as a meaningless "no response" |
| `seedcode/core/agent.py` | Native tool loop only parsed fenced `tool` blocks | A model emitting `{"name": ..., "arguments": ...}` as text ended the turn instead of executing the call |

## 2. Repaired modules

- `seedcode/core/agent.py`
  - Removed all references to the deleted Computer Engine, so `AgentEngine` builds and runs.
  - Added structured activity events (event id, task id, timestamp, type, message, path, status, diff, line counts) emitted from real operations.
  - Added parsing for OpenAI-style JSON tool calls emitted as text, including fenced JSON, so the loop executes them rather than returning them.
  - Emits `task_started` / `task_completed` / `task_failed`; tool events carry real output and diffs.
- `seedcode/app.py`
  - `_task_mode_label` now reports the unified `Agent Mode` without importing the removed `core.modes`.
- `seedcode/commands/doctor.py`
  - Removed the dead OCR check; `/doctor` completes and reports real rows.
- `seedcode/core/providers/ollama.py`
  - Tracks whether any text/tool call was emitted; raises an actionable `ProviderError` for a genuinely empty response instead of silently succeeding.
  - Verified normal and streaming text plus native tool calls against a real local model.
- `seedcode/core/internet.py`
  - Rewrote DuckDuckGo parsing to scan result anchors (supports both HTML and Lite layouts).
  - Added the DDG Lite endpoint as a fallback; search now returns real titles/URLs/snippets.
- `seedcode/core/intent.py`
  - Coding tasks no longer trigger internet retrieval for temporal words like “now”; research is limited to explicit/doc signals. This removed an unnecessary web lookup on every “now run the tests” turn.
- `seedcode/tools/base.py`, `filesystem.py`, `patch.py`, `diffs.py` (new)
  - `ToolResult` now carries a real unified diff for creates/edits/appends/deletes/inserts.
  - Diffs are generated with `difflib` from actual before/after content; created/deleted files use `/dev/null`, matching `git diff`.
- `seedcode/core/events.py` (new)
  - `ActivityEvent` / `build_event` / `event_type_for`: one structured event per real operation.

## 3. Removed obsolete code (Phase 10)

- Deleted the already-removed desktop stack entirely (`seedcode/computer/`, `seedcode/apps/`, desktop tools/dependencies usage).
- Deleted the dead `seedcode/core/client.py` shim that unconditionally raised `ImportError`.
- Confirmed no live module references the retired `/chat`, `/agent`, `/codemode`, `/assist`, `/mode`, `/desktop` modules.
- Kept provider, filesystem, terminal, memory, project-context and git infrastructure.

## 4. Verification evidence

### Application
- `python -c "import seedcode"` — OK
- `python -c "import seedcode.app"` — OK
- `python -c "from seedcode.core.agent import AgentEngine"` — OK
- Full module import sweep (`pkgutil.walk_packages`) — **0 bad imports**
- `python -m seedcode --version` → `Seed Code CLI 9.2.0`
- `python -m seedcode --help` → OK
- Piped startup (`/exit`) reaches the menu and exits cleanly with “Goodbye — plant ideas, grow code.”

### Ollama (real local `qwen3:4b`)
- `detect()` → `True`
- Non-streamed text request → `'hello world'` (cold start 146s, warm 13.4s)
- Native tool request → correct `read_file {"path": "hello.txt"}` call
- Empty output now produces a clear, actionable `ProviderError`; no silent provider switch.

### Filesystem + diff accuracy
| Operation | Result | Diff stats |
|---|---|---|
| `write_file` new 3-line file | OK, content verified on disk | added 3, removed 0 |
| `edit_file` one line | OK, content verified on disk | added 1, removed 1 |
| `delete_file` | OK, file gone | added 0, removed 3 |

### Terminal
- Agent called `run_command` → real output `5`, exit 0; event `terminal_completed`.
- Command execution covers stdout/stderr merge, exit code, timeouts and process-tree kill (existing tested implementation).

### Internet
- `internet.search("python 3.12 release notes")` → 3 real results (python.org, docs.python.org)
- `internet.research(...)` → 1 fetched excerpt, context block built
- `web_search` tool → real result list
- Real agent research turn (240s): called `web_search`, then `web_fetch`, and answered with two Python 3.12 features citing the source URL.

### Agent workflow (real Ollama, temp workspace)
- Turn 1 (337s): created `test-project/calculator.py` with `add(a, b)` and a `main` printing `add(2, 3)`; file content was verified on disk.
- Turn 2 (77s): ran `python test-project/calculator.py` and reported the actual output `5`.
- Turn 3 (240s): web research with tools, as above.

### Activity events
Observed for a real turn:
`task_started → tool_started → file_read → task_completed`
Each payload contained `event_id`, `task_id`, `timestamp`, `type`, `message`, `path`, `status`, `diff`, `lines_added`, `lines_removed`, metadata. Tool events are generated from the actual tool execution, not the UI.

### Full test suite
Command (5 desktop-era test modules excluded because their imports were removed in v9.2.0):

```
python -m pytest -q --ignore=tests/test_dispatcher.py --ignore=tests/test_engine_hardening.py \
  --ignore=tests/test_lifecycle.py --ignore=tests/test_skills.py --ignore=tests/test_v910_workspace.py
```

Result: **649 passed, 66 failed, 43 errors** (plus 5 collection errors in the excluded desktop modules).

All remaining failures are stale tests for the intentionally removed v9.1 architecture or release metadata, not live-backend regressions:

- 43 errors + 11 + 4 + 3 + 2 + 2 + 2: `test_v825_mode_switch_resize`, `test_v625_ux`, `test_v625_core`, `test_v825_tui`, `test_v710_codemode`, `test_menu_routing`, `test_v710_ui` — import/call removed `/chat`, `/agent`, `/codemode`, `/assist`, mode switching.
- 13 `test_provider_matrix` — imports `mode_label` from `commands.status` (moved to `ui.statusbar`) and tests Chat/Agent mode matrix.
- 10 `test_permission_level` — expects the removed `PermissionLevel.DESKTOP`.
- 6 `test_dashboard`, 3 `test_ui_reference`, 2 `test_v710_session_view` — expect “Chat Mode”/“Code Mode” labels; v9.2.0 uses one unified Agent/Coding Agent.
- 3 `test_v911_stage1` — expects removed `_handle_chat` / old Chat escalation.
- 2 `test_installer_checksums`, 1 `test_v725_distribution` — installers/README still say 9.1.1 while `__version__` is 9.2.0 (release metadata, not runtime).
- 1 `test_v725_providers` — expects automatic failover, which v9.2.0 intentionally disables unless `auto_failover` is enabled.
- 1 `test_config` — expects a 4096-token chat clamp; agent mode intentionally allows 8192.
- 1 `test_v710_e2e_todo_app` — expects header version `9.1.1`.

No tests were deleted or weakened.

## 5. Remaining limitations

1. **Stale test suite.** Tests covering removed desktop/mode features still fail/error. They were left untouched per the “do not delete tests” rule; they need to be rewritten or retired deliberately in a follow-up.
2. **Release metadata drift.** `IRM_INSTALL/install.sh`, `install.ps1`, `RELEASE_INFO.txt` and README asset names still reference 9.1.1. This was left alone because updating them without staging 9.2.0 release artifacts would point installers at files that may not exist.
3. **Cloud providers not live-tested.** No valid cloud keys were available; their request/response paths are covered by the existing mocked provider tests, while Ollama was tested for real.
4. **Model latency.** Local `qwen3:4b` takes ~13–300s per turn on this machine; the agent loop is bounded and streams, but total task time is model-bound.
5. **Autonomous full-app build not exhaustively tested.** File creation, read-back, command execution, error handling, research and diff/event reporting were verified end-to-end; a long “build and fix a complete app” scenario was not run to completion.
6. **Desktop control is intentionally absent.** Browser/desktop automation tools were removed in v9.2.0, so computer-intent requests are answered without desktop tools.

## 6. Suggested next step

Retire/rewrite the obsolete v9.1-era tests (mode switching, desktop permissions, old labels) against the unified Agent architecture, then align installer/README versions with the v9.2.0 release artifacts.
