# Changelog

All notable changes to Seed Code CLI are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project adheres to [Semantic Versioning](https://semver.org/).

## [9.1.1] — 2026-09-28

An intent, dependency and diagnostics release. v9.1.0 gave Agent Mode the power
to act; v9.1.1 makes it act *deliberately* — a turn first decides what it is
(conversation, research, coding, computer), and only the capability that intent
actually requires is allowed to run or even be advertised. Alongside it: Chat
Mode can now hand a single task to Agent Mode and return, internet access is a
capability of both modes rather than something bolted onto one, "Allow All"
really covers the session, and a build that is missing its built-in credential
says exactly which variable it looked for instead of failing anonymously.

### Added

- **Intent classification** (`seedcode.core.intent`): a deterministic,
  model-free classifier runs before every turn and resolves it to
  `conversation`, `research`, `coding` or `computer`, with the flags the rest of
  the application needs (`needs_internet`, `needs_workspace`, `needs_terminal`,
  `needs_desktop`, `executes`, `reason`, `target`). It is pure Python with no
  provider call, so classification can never fail, cost a request, or change
  behaviour when the network is down. Product names are recognised as products,
  not paths, so "the latest Node.js release" is research — not a file read.
- **Internet access as a modular capability** (`seedcode.core.internet`):
  keyless DuckDuckGo HTML search, page fetching with boilerplate stripped, a
  `research()` helper that reads the top sources, and a char-budgeted,
  URL-attributed `context_block()` so retrieved text is bounded and attributable
  in the prompt. Every failure path returns an empty result instead of raising,
  and `SEEDCODE_DISABLE_INTERNET=1` disables the capability for a session.
- **Web tools** (`seedcode.tools.web`): `web_search` and `web_fetch`,
  registered in a new `web` tool group and advertised **only** to an intent that
  needs the internet. Both are read-only (`mutates=False`).
- **Built-in credential diagnostics** (`seedcode.default_api`):
  `builtin_status()` and `describe_builtin_status()` report whether the built-in
  Default connection is available, missing, invalid or unavailable for this
  build, with a value-shape check (`_MIN_KEY_LEN`, whitespace/ASCII) — and never
  include the key itself. `/doctor` reports the built-in connection state.
- **"Allow All" permission scope** (`seedcode.tools.permissions`,
  `seedcode.ui.dialog`): `ActionGrant.ALL` and `ActionGate.allow_all` grant every
  action type for the rest of the session,  selectable from the permission dialog as a third option next to Allow Once and
  Always Allow.
- **New test module** (`tests/test_v911_stage1.py`): 52 tests covering intent
  classification, intent-gated tool groups, the "no workspace context without a
  workspace intent" guarantee, internet retrieval (fully network-patched), the
  web tools, Chat → Agent escalation, permission persistence, the `ALL`
  permission scope, provider failover reasons, and the build-time credential
  diagnostics.

### Improved

- **Minimum-tool principle.** `AgentEngine` now builds its tool manifest from
  the intent: `core` always, plus `web` only for research/computer, plus
  `desktop` only for a computer intent at a permission level that allows it.
  A coding task never sees the browser tools.
- **Zero unnecessary file access.** Project context, the workspace coding
  preamble and the terminal hint are all gated by intent, so a question that
  never needed the project no longer causes the project to be read.
- **Chat → Agent escalation, and back.** When a Chat Mode request is one that
  needs to *execute* something, the turn runs with the agent engine and then
  returns to Chat Mode, announced on both sides
  (`[Agent assist - <reason>]` … `[Temporary agent task finished - back to Chat Mode]`).
  Explicit Agent Mode (`/agent on`) is unaffected and stays on until you turn it
  off.
- **Modular internet access for both modes.** Chat Mode retrieves sources for a
  research request and shows its progress (`[Accessing internet...]`,
  `[Reading relevant sources...]`) before answering.
- **Actionable failover messages.** A provider switch now records why it
  happened and reports it (`rate limited`, `authentication rejected`,
  `network unreachable`, `provider unavailable`, `repeated transient errors`)
  instead of switching silently.
- **`test_tools.py`** asserts the manifest over every registered group
  (`core`, `desktop`, `web`), so a new group cannot be added without a manifest
  entry.

### Fixed

- **"Allow All" did not persist.** The action gate was rebuilt for every
  engine, so an "allow all" decision was forgotten the moment a new engine was
  created and the same action type prompted again. There is now one
  process-wide gate (`_ACTION_GATE`, with `reset_action_gate()` to clear it),
  and `allow_all` short-circuits `check()` for every subsequent action.
- **A build could silently ship without its built-in credential.** The `.env`
  variable name was misspelt (`DEFULT_API_KEY`), which no consumer read, so the
  packaging step embedded nothing and the installed artifact reported the
  built-in connection as unavailable. `embed_default_key.py` now recognises both
  documented names (`OPENROUTER_API_KEY`, `SEEDCODE_DEFAULT_API_KEY`), rejects a
  typo'd name explicitly instead of ignoring it, validates the value's shape,
  and exits non-zero with the exact fix — never printing the value.
- The repository `.env` key was renamed to the documented
  `SEEDCODE_DEFAULT_API_KEY` (file is git-ignored; the value was preserved and
  never printed).

### UI/UX

- The permission panel offers three grants — **Allow Once**, **Always Allow**,
  **Allow All** — plus Deny, and the confirmation hint names the keys
  (`Enter allow · a allow session · A allow all · d deny · Esc cancel`).
- Chat Mode turns that escalate show why they escalated and that control has
  returned to Chat Mode.
- Internet retrieval is visible while it happens rather than appearing as a
  pause.

### Agent

- Agent Mode keeps working exactly as before when it is explicitly enabled;
  what changed is that it is no longer the *only* thing that can act, and it is
  no longer handed tools the task does not need.

### Provider

- A failover now explains itself (see *Improved*), and the reason travels with
  the provider-switch event so the interface and logs agree.

### Configuration

- `DEFAULT_API_KEY`-style typos are a hard, named error at build time rather
  than a silent no-credential artifact.
- `SEEDCODE_DEFAULT_API_KEY` and `OPENROUTER_API_KEY` are the two recognised
  built-in-credential variable names, matching what the runtime reads.
- `SEEDCODE_DISABLE_INTERNET=1` turns the internet capability off for a session.

### Testing

- Full suite: **1136 passed, 1 skipped** (up from 1084 passed, 1 skipped).
- `python -m compileall` over the package is clean.
- Version **9.1.1** across source, packaging metadata, installers and release
  artifacts; `seedcode.__version__` remains the single source of truth.

## [9.1.0] — 2026-09-28

The workspace release. Seed Code now asks where it may work before it starts, and
Agent Mode performs the work — creating, editing and running real commands in the
project — instead of describing it.

### Added

- **Explicit workspace selection at startup** (`seedcode/workspace.py`): every
  interactive start presents `Select Workspace` with `Current Folder` and
  `Choose a Folder` (the operating system's native folder picker). The selected
  directory becomes the **workspace** — the single root for Agent Mode, file
  reads and writes, project indexing, the `.seedcode` context and `plan.md`,
  terminal commands and verification. Automation is never blocked:
  `SEEDCODE_WORKSPACE=<dir>` selects the workspace directly and
  `SEEDCODE_NO_WORKSPACE_PROMPT=1` keeps the launch directory.
- **Verified agent execution**: a task is only `COMPLETED` when its acceptance
  criteria are backed by evidence on disk — files that exist, a command that ran
  and exited 0, tests that passed. A model that claims "done" while the checks
  fail is sent back to fix them; completion is never a text assertion.
- **Built-in `Default` provider** in the packaged builds (wheel, sdist,
  standalone EXE and installer), so a fresh install can run without the user
  configuring a key first. `openrouter` and `ollama` remain available.

### Changed

- **Version 9.1.0** across source, packaging metadata, installers and release
  artifacts; `seedcode.__version__` remains the single source of truth.
- **One deterministic checksum source**: the release's `SHA256SUMS.txt`,
  generated from the final artifacts after the last one is built, is the only
  expected value. The remote installers no longer carry a pinned digest.

### Fixed

- **Windows installer checksum mismatch** — the hand-maintained pinned digest
  that drifted from a rebuilt binary (and aborted with "SHA256SUMS.txt and this
  installer's pinned checksum disagree") is gone.
- **Linux CI test failure** — the lifecycle self-guard test no longer assumes a
  Windows-only window handle, so `ubuntu-latest` passes without disabling it.
- **Flaky mode-switch test** that sampled the worker count on the line directly
  after starting the thread; it now waits for the worker to register.

## [8.2.5] — 2026-09-25

A terminal-workspace release. Seed Code CLI runs as a **persistent terminal
application** — a fixed header carrying the Seed Code ASCII logo, a scrolling
conversation region and a fixed message composer — instead of a sequence of
printed banners. All existing functionality (providers, models, modes, tools,
permissions, commands, shortcuts, Code Mode, project memory) is preserved; the
interface around it is the product.

### Added

- **Persistent three-region TUI** (`seedcode/ui/tui.py`): a full-screen
  prompt_toolkit application with a fixed header, a scrolling middle region and
  a fixed composer. Running `seedcode` on an interactive console opens it
  immediately; `SEEDCODE_NO_TUI=1`, `SEEDCODE_PLAIN`, and non-TTY hosts keep the
  sequential console.
- **Centralized reactive state** (`seedcode/ui/state.py`): one `AppState` object
  holds provider, model, mode, status, workspace, masked key, context budget,
  messages, activities and connection state. Components subscribe and only the
  changed regions repaint — no clear-and-redraw, no flicker.
- **Live header that keeps the Seed Code ASCII logo** (`seedcode/ui/header.py`):
  the fixed top region is a bordered dashboard carrying the exact Seed Code
  block logo and tagline over Provider/Model, Mode/Status, Workspace/Context and
  the masked API key. The branding is never replaced by a plain-text wordmark on
  a console that can draw it, and every value is read from live state and
  truncated so no line can overflow. Below 76 columns — where the 70-column art
  genuinely cannot be drawn un-clipped — the *metadata* re-flows into a compact
  panel and then plain lines: the logo is omitted whole rather than truncated,
  and legacy consoles get the wordmark form.
- **Professional message composer** (`You >`): the fixed bottom region is a
  bordered, multiline editor that can never scroll away with the history,
  carrying the `You >` prompt with continuation lines aligned under it. Cursor
  movement, Home/End, Backspace/Delete, arrow keys, history, paste, long
  prompts, Unicode, terminal resize and clear-editing-state all work, and input
  stays responsive however long the prompt gets. The prompt is display only:
  the submitted message never contains it.
- **Animated thinking indicator**: sending a message immediately shows
  `AI > ◌ Thinking…` in the conversation region and animates it
  (`.` → `..` → `...`) while the provider is being called. It is a UI state
  only — it runs on its own daemon thread, is replaced the instant real output
  streams, and never delays the model call or the agent.
- **`AI >` attribution**: streamed answers and every live activity line are
  attributed to the assistant (`AI > ⚙ Reading package.json`,
  `AI > ✓ Tests passed`), so the interface reads as a dialogue instead of raw
  terminal logs.
- **Unseen-output hint**: scrolling up while a turn produces output shows
  `↓ n new` in the composer instead of yanking the viewport back to the bottom.
- **Streaming that keeps its place**: assistant tokens update a live block at a
  throttled refresh rate while the header and composer stay fixed — the whole
  screen is never redrawn for a token, so there is no flicker.
- **Live agent activity stream**: tool/command events appear as they actually
  happen (`AI > ⚙ Reading package.json`, `AI > ✓ Tests passed`), and the header
  status moves through the real phases — `● Ready`, `◌ Thinking`, `◌ Working`,
  `● Running`, `✓ Completed`, `⚠ Error`, `■ Cancelled` — from real events. A
  finished Code Mode or Agent Mode task ends on `✓ Completed`.
- **Inline permission prompt**: Allow Once / Always Allow / Deny is answered in
  the composer while the running turn waits, so the permission model is
  unchanged and no turn is blocked by a nested dialog.
- **Cooperative Ctrl+C**: cancelling during a turn stops the turn (the engine's
  callbacks raise), returns the header to a ready state, and leaves the session
  usable — Ctrl+D exits.

### Changed

- The composer is a real editor rather than a `You >` prompt line: `Enter`
  sends, `Shift+Enter` (or `Ctrl+J` / `Alt+Enter`) inserts a newline, `↑`/`↓`
  walk the input history, `Esc` clears the line (and denies a pending
  permission prompt), and `Ctrl+C` cancels. `Ctrl+K` palette, `Ctrl+P` files,
  `Ctrl+R` history, `Ctrl+/​` shortcuts, `Ctrl+L` clear, `PageUp`/`PageDown`
  (and `Ctrl+Home`/`Ctrl+End`) scroll the conversation, and the mouse wheel
  scrolls without stealing the follow-the-bottom behaviour.
- The header is never reprinted: it is one permanent component whose values
  update in place, so switching provider, model or mode is visible immediately.
- `/clear` empties the conversation region instead of emitting a terminal clear.

### Removed

- The visible Agent/Chat **to-do checklist** (`☐ Inspect project` style step
  rows) is gone from the TUI. Agent Mode decides its own execution sequence and
  shows a live activity stream; the retired checklist is no longer rendered.
- **Code Mode is no longer a separate user-facing mode.** Its workspace coding
  capabilities (`.seedcode` project memory + index, the persistent
  plan → execute → verify session loop) are now native capabilities of Agent
  Mode, available automatically when Agent Mode is on. The mode system exposes
  exactly two modes, **Chat Mode** and **Agent Mode**; `code`, `codemode`,
  `assist` and `desktop` remain accepted input aliases that resolve to Agent
  Mode, and `/codemode` still toggles the workspace capability. No capability
  was deleted — it was moved, not duplicated.

### Fixed

- **Mode switching no longer tears down and rebuilds the terminal
  application.** The prompt_toolkit ``Application``, ``Layout`` and every
  region are built once and re-entered; mode switches (`/agent on`, `/agent
  off`, `/mode`, `/chat`, `/codemode`) run *inside* the live application as a
  state transition instead of exiting the event loop and reconstructing the
  screen — the root cause of the old freeze/flicker on switch. A single
  background worker runs the turn, and cancellation/exit stop the thinking
  animation and release the application cleanly.
- **The permission prompt is a bounded TUI component.** Allow/Deny runs as a
  bordered panel inside the same application (`Enter` allows, `A` allows for
  the session, `D` denies, `Esc` cancels). Only the agent turn waits — the
  event loop, the header and the composer stay live.

### Packaging

- Rebuilt the release artifacts from the final source: the standalone Windows
  executable (`dist\seedcode.exe`, PyInstaller, Seed Code icon + 8.2.5 version
  resource), the Inno Setup installer (`Release\SeedCode-CLI-Setup-8.2.5.exe`,
  published to the repo root as `seedcode-cli-setup.exe`), the Python wheel and
  sdist, and the npm launcher tarball.
- Re-staged `dist/release/8.2.5/` with a fresh `SHA256SUMS.txt` computed from
  the final files (4 artifacts + npm tarball), and removed the obsolete 8.1.0
  artifacts from `dist/`, `dist/release/` and `Release/`.
- Pinned the freshly built Windows x64 digest in `IRM_INSTALL/install.ps1`'s
  `$PinnedChecksums` (a cross-check/fallback for `SHA256SUMS.txt`; verification
  is never bypassed).

### Version

- **Version synchronized to 8.2.5** across the package
  (`seedcode.__version__`), the CLI `--version`, the Windows installer metadata,
  the npm wrapper, the remote installers, `RELEASE_INFO.txt` and the docs.
  `seedcode --version` reports `Seed Code CLI 8.2.5`.

---

## [8.1.0] — 2026-09-24

A modes, task-lifecycle and release-packaging release. Seed Code now exposes
exactly **three modes** — Chat, Code and Agent — with one shared resolver so no
surface can disagree or invent a fourth, and the whole release surface
(package, installers, checksums) is synchronised on **8.1.0**. Everything
below is implemented in this repository and covered by the test suite.

### Modes

- **Exactly three user-facing modes**: Chat Mode, Code Mode and Agent Mode.
  A new single source of truth, `seedcode.core.modes`, owns the enum, the
  labels, the descriptions and the parsing; `/mode`, `/chat`, `/codemode`,
  `/agent`, `/assist`, `/desktop`, the dashboard, the status bar, the menu and
  the task header all resolve through it.
- **Assist Mode is retired as a mode.** Its capabilities now belong to Agent
  Mode. `assist`, `desktop`, `codemode` and `code` remain accepted *input*
  aliases (`assist`/`desktop` → Agent Mode, `codemode` → Code Mode) so existing
  habits and stored configurations keep working, but no fourth mode can be
  selected or displayed.
- `AppConfig.mode` (`"chat" | "code" | "agent"`) replaces the legacy
  `agent_mode` boolean, with a migration that reads old configs (including a
  stored `"assist"`) and a read/write `agent_mode` compatibility property, so
  old configuration files load unchanged and round-trip to the new field.
- The interactive main menu no longer offers a separate "Assist Mode" entry
  (it was a fourth mode); the mode actions are Code Mode and Agent Mode.
- The `/tools` panel is titled "Agent Tools" and the desktop tools' guidance
  now points at `/agent on` as the primary route.

### Task lifecycle

- **Completing a task never closes the application.** Every task path (success,
  failure, provider error, `Ctrl+C`) finishes the task view, prints a status and
  returns to a ready prompt that accepts the next request. Only `/exit` leaves
  the chat loop, and even that returns to the menu rather than the process.
- Code Mode keeps its evidence-based task graph: a task is `COMPLETED` only
  when its acceptance criteria are satisfied by observed evidence (files that
  exist, a command that exited 0, tests that passed, no unresolved error), and
  a step is never reported done unless the work behind it really happened.
- The live progress view is driven by the model's *plan* (the task graph
  checklist) and by real tool events, so what is on screen is what actually
  ran.

### UI

- The dashboard, status bar, `/status`, `/mode` and the task header all name the
  active mode from live state through `seedcode.core.modes`, so no surface can
  show a stale or contradictory mode name.
- Fixed the Code Mode menu item reporting `ON` whenever Agent Mode was on: it
  now reflects the Code Mode workspace session alone.
- The command hint advertises `/agent` as the primary Agent Mode route.

### Distribution

- **Version synchronised to 8.1.0** across the package (`seedcode.__version__`,
  which the wheel, sdist, CLI, executable and release workflow all read), the
  remote installers, `IRM_INSTALL/RELEASE_INFO.txt`, the npm launcher metadata
  and the Windows installer definition.
- **Linux/macOS installer rewritten to the release checksum contract.**
  `IRM_INSTALL/install.sh` now prefers the platform standalone binary
  (`SeedCode-CLI-<ver>-<os>-<arch>`) and falls back to the official wheel; in
  both paths it fetches the artifact, verifies it against the release's
  `SHA256SUMS.txt` (`sums_lookup` / `verify_sha256` / `sha256_of`, tolerant of
  spaces, tabs, CRLF and the GNU binary-mode marker while still requiring 64
  hex digits and an exact file name), then installs — and it verifies the file
  it installed by absolute path, reporting any older `seedcode` that is earlier
  on `PATH`. A missing entry, a mismatch, or an unreachable checksum file all
  abort: verification is never bypassed.
- The Windows installer's pinned-checksum map is reset for 8.1.0. Digests are
  computed from the real published artifacts at release time (never invented);
  with no pinned entry the release `SHA256SUMS.txt` is the sole authority and
  an unreachable checksum file refuses the install instead of proceeding.
- Restored the installer/release checksum tests to green: the Linux installer
  once again implements the documented `sums_lookup`/`verify_sha256` contract,
  and the installers, README and `RELEASE_INFO.txt` all carry the 8.1.0
  artifact names.
- Added a Code of Conduct and a Security Policy (see `.github/`).

## [7.2.5] — 2026-09-23

A reliability and provider-system release. 7.1.0 left two reliability gaps
open — the task graph could neither record a consciously-not-needed task, nor
distinguish a lost connection from a failed task — and the provider layer had
no answer for a failing provider. Both are closed here: Code Mode now skips
honestly and pauses (never fails) on a lost connection, and the provider
system became **OpenRouter / Ollama / Custom** with automatic, state-preserving
failover, provider health management, and Ollama auto-start. Everything below
is implemented and covered by the test suite in this repository.

### Code Mode

- New `SKIPPED` task state (`seedcode.core.tasks.TaskState`). A task the model
declares not needed (`TASK <id> SKIPPED: <reason>`) and a pending task whose
prerequisite can never finish are both marked `SKIPPED` — recorded with the
reason, transitively, and never counted as verified work. A project cannot be
`complete` from skips alone: at least one task must still be verified with real
evidence. The checklist shows `–` (`-` on legacy consoles).
- Dependents of a failed/blocked/skipped task are now skipped explicitly with
`skipped: prerequisite task N is failed`, instead of being left as "waiting on
unfinished dependencies".
- Network-aware recovery: a transient provider failure (dropped connection,
timeout, 429/5xx) now reconnects with longer backoff before the call is given
up, and it never restarts a task — the same model call is retried with its
state intact. If reconnection still fails the session **pauses** with the plan,
files and checkpoint preserved (`SessionConnectivityError`), rather than
reporting the task as failed. Permanent errors (bad key, unknown model) still
fail with their own message.

### UI

- Reconnection progress (`connection lost — reconnecting (attempt n/m)`,
`connection restored — resuming`) is shown live, and a network pause reports
its real reason while keeping `/resume` as the action.
- The completion summary reports `• Skipped: n task(s) not needed` so the
`n/N tasks verified` figure is never inflated by skips.
- `SKIPPED` is styled in the Code Mode header, the task checklist and the
`/session` task rows.

### Providers

- The user-facing provider list is now **OpenRouter, Ollama and Custom**
  (`seedcode.core.providers.visible_provider_ids`). Default, FreeModel
  Claude/Codex and AeroLink are retained as legacy built-ins for backward
  compatibility — an existing configuration still loads, selects and uses
  them — but they are no longer advertised for a new setup.
- New **Custom providers** (`seedcode.core.providers.custom.CustomProvider`):
  any number of user-defined, OpenAI-compatible endpoints, each with its own
  name, base URL, API key and model. Add, edit, test the connection,
  enable/disable, reorder by priority, select and delete them from `/provider`
  or `/custom`; configurations persist in `config.json` and there is no
  artificial count limit. A rejected key, an unreachable host and a non-http(s)
  base URL each produce a specific, actionable message, and a key is only ever
  shown masked (`config.masked_key`).
- New **provider health** (`seedcode.core.providers.health`): session-only
  states `unknown · healthy · retrying · temporarily_unavailable ·
  rate_limited · authentication_error · offline`, with per-state cooldowns so
  a configuration that just failed is not hammered and a rejected key is never
  retried in the same session. There is no path to an infinite retry loop.
- New **automatic failover** (`seedcode.core.providers.failover.FailoverChain`,
  wired through `seedcode.core.chat.ChatEngine`): a request that fails on the
  active provider is retried there first, then switches to the next healthy
  configuration and retries **the same request**. Conversation history, task
  state and TODO state are preserved, so the work resumes at the current
  operation instead of restarting; the failure only surfaces when no viable
  provider remains.
- **Ollama auto-start** (`seedcode.core.providers.ollama_start`): selecting
  Ollama checks the server, re-checks immediately before launching (never a
  duplicate), starts `ollama serve` detached and cross-platform, polls for
  readiness with a bounded timeout, and continues the original request. A
  missing executable or a startup timeout is reported actionably; the user no
  longer needs to run `ollama serve` by hand.

### UI

- Provider failover is visible (`Switching provider: A → B`), `/status` and
  the provider list keep showing each provider's own model and masked key, and
  `/custom` opens the custom-provider manager.

### Distribution

- License change: Seed Code CLI is now distributed under the **PolyForm
  Noncommercial License 1.0.0** (`PolyForm-Noncommercial-1.0.0`), replacing the
  previous MIT label in `LICENSE`, the package metadata (`pyproject.toml`), the
  npm launcher metadata, the Windows executable version resource, and the
  documentation. Third-party dependency licenses are unchanged.
- The startup screen now leads with the Seed Code ANSI logo and a compact
  block of live state (version, provider, model, mode, status), with a text
  wordmark and ASCII borders on consoles that cannot draw the block glyphs.
- Fixed the pip first-run false-offline state: a provider that merely has no
  API key is reported as `No Key` (a setup state), never `Offline`, so a fresh
  `pip install seedcode-cli` no longer looks like a broken application.
- `seedcode` is the primary command after every installation method; the wheel
  exposes the `seedcode` console script and `python -m seedcode` still works.
- Python 3.10 is supported again (`requires-python = ">=3.10"`); the project
  is audited against the 3.10 grammar and standard library.
- Rebuilt every release artifact for 7.2.5 (wheel, sdist, portable Windows
  executable, Inno Setup installer) with a fresh `SHA256SUMS.txt` verified
  against the staged files; the generated secret module is never packaged.

## [7.1.0] — 2026-09-22

A Code Mode architecture release: Code Mode became a *persistent software-
engineering agent* that drives a planned task graph through many model/tool
cycles instead of stopping after one model response, the Code Mode view was
redesigned into a compact professional header, and the desktop-control
cleanup paths can no longer close applications the agent opened.

### Code Mode

- New task execution engine (`seedcode.core.tasks`): explicit task states
  (`PENDING`, `RUNNING`, `VERIFYING`, `BLOCKED`, `RECOVERING`, `FAILED`,
  `COMPLETED`, `CANCELLED`), dependencies between tasks, acceptance criteria,
  and machine-checked verification (`file:`, `file: … | contains:`, `run:`,
  `tests`, `no-errors`).
- New persistent session engine (`seedcode.core.session`): plans the request
  into a task graph, then runs task after task — each task may need many
  model calls, tool calls, edits, command runs, failed attempts and repairs
  before it is verified and completed. A model response is never treated as
  proof of completion.
- Continuous context: a compact working state (request, current task,
  completed/remaining tasks, dependencies, changed files, commands, tests,
  errors, blockers, acceptance criteria) is rebuilt for every model call, and
  history compaction keeps context growth bounded without losing state.
- Session checkpoints are persisted to `.seedcode/checkpoints/`, so an API
  failure, an output/context limit, or a pause resumes from the current task
  instead of starting over.
- Bounded recovery: provider errors retry with backoff, failing commands are
  inspected and fixed, and repeated identical failures stop with an explicit
  blocker instead of looping forever.
- Each task keeps its own execution record — current action, files inspected
  and affected, commands with their outcome, tool calls, test results, errors,
  retry count, timestamps and the verification result — and the record is part
  of the persisted plan (`.seedcode/plan.json`), so a resumed session knows per
  task what was already done instead of restarting from the session list.
- `/session`, `/pause`, `/resume` and `/stop` control and inspect a Code Mode
  session without losing task state or project files.
- Cancellation stops future model calls and tools, preserves state, and never
  closes applications.
- Inspected files are recorded as part of the session context, so a resumed
  session knows what was already looked at instead of re-reading it.

### UI

- New compact Code Mode header: `SEEDCODE 7.1.0 • CODE MODE` with state,
  `Task n/N`, the active task, a progress bar, elapsed time and call count —
  two rows while working, one row while idle, no decorative art.
- Task view shows the plan as a short checklist (`✓ ● ○ ✗`) and a single live
  activity line instead of a wall of output. The action line follows the real
  phase — the tool actually running, then `Verifying acceptance criteria` /
  `Verifying the project (tests / build)` — never a decorative message.
- New `/session` inspection view (`seedcode.ui.session_view`): state, progress,
  elapsed time, model/tool calls, recoveries, tests, commands, files inspected
  and changed, blockers, and the checkpoint/resumability of a stopped session —
  plus one row per task with its state, verification result and own execution
  record. `/status` gained the same state in one line
  (`RUNNING (1/3 verified) — Task 2`, or `PAUSED (2/3 verified) — resumable with
  /resume`).
- Evidence-gated completion is now visible: a finished task announces its own
  record, and a finished project closes with `✓ Verification`, `✓ Tests`,
  `✓ Files`, `✓ Commands` and inspected items — observed facts only, with
  nothing drawn for a value that was never observed.
- Stopping a session (`/stop` or `Ctrl+C`) reports that the plan and completed
  work were kept and points at `/resume` instead of reading as a dead end.
- Responsive at every width with the existing ASCII fallback, and the Code Mode
  panel now re-fits on every refresh so resizing the terminal mid-session
  cannot leave a panel wider than the screen.

### Desktop control

- Fixed the auto-close behaviour: applications the agent opens stay open.
  A session ledger plus an explicit-intent guard now refuses implicit closes
  of user-facing apps (windows, browsers, tabs) from any cleanup path;
  internal cleanup (mouse/keyboard release, driver/socket teardown) is
  unchanged.
- Opening a new tab/window never collapses the last one, and `Ctrl+W` is no
  longer used as a blind fallback when it could close the app itself.

## [6.2.5] — 2026-09-21

A UI, provider and distribution release: the startup screen lost its ASCII
logo (and gained a compact, structured header), the built-in **Default**
connection became a first-class provider separate from OpenRouter, tasks
became step-by-step, and installation moved to the official IRM installer
system.

### UI

- Removed the large ASCII logo from the CLI interface; `seedcode.ui.logo` no
  longer exists and nothing renders pixel or block art anywhere. This is the
  one permanent change to the startup screen. The orphaned artwork it used
  (`seedcode/assets/logo.txt`) was deleted too — the 16x16 mark in
  `seedcode/branding.py` stays, because it is packaging only (`.ico`, the
  installer wizard, Explorer/search surfaces) and the terminal UI never
  renders it.
- Restored the previous, richer startup dashboard around that change: a
  bordered reference panel (96-column design width) with the `Seed Code`
  wordmark and the `AI CODING AGENT` descriptor on the left, a divider, and
  identity (`Seed Code | Eagox Studio`), the tagline and the live session
  state on the right — Provider, Model, and Mode with its status on one row,
  plus `API Key` only when the provider actually needs one.
- Final proportions of that panel: wider and shorter. The design width is 96
  columns (the whole `cohere/north-mini-code:free` model name fits without
  clipping), and the two padding rows were dropped — one blank row under the
  title is kept for breathing room, the live rows follow, then the border. The
  panel is eight lines at full width instead of ten.
- The dashboard is responsive: the info section slides left below the design
  width so the whole model value still fits (80 columns shows
  `cohere/north-mini-code:free` in full), then the value clips, then a compact
  one-row panel renders under 64 columns, then plain lines under 40 — never
  overflowing or breaking its border.
- The panel and the state marks fall back to ASCII automatically on consoles
  that cannot draw or encode them (raster-font `cmd.exe`, redirected streams
  on a cp1252 host), so the header never breaks mid-render.
- Mode switches reprint the one-line session summary (`provider · model ·
  mode · status`) rather than the whole dashboard.
- The `API Key` row is rendered **only** for providers that actually require
  a key, so Default and Ollama never show one.
- `mode_label()` remains the single source of truth for the active mode, and
  the header, `/mode`, `/chat` and `/status` all name it identically.

### Task flow (Code / Assist / Agent Mode)

- Added `seedcode.ui.tasks`: a reusable task-progress abstraction with the
  states `pending`, `running`, `completed`, `failed` and `skipped`, and a
  compact live view (`analyze → inspect → plan → implement → test → verify`).
- Step state is driven by real engine activity. `AgentEngine` now reports
  structured activity (`tool_start` / `tool_done` / `say`) through an optional
  `on_step` callback, and the flow uses it: a read tool completes *Inspect
  files*, a mutating tool completes *Implement changes*, and *Run tests* only
  completes when a recognised test command actually ran and exited 0 — a
  failing run is shown as failed with what failed, never as a passing count.
- Steps the task never needed are reported as **skipped**, so progress is
  never fabricated; failed steps stay visible after the task ends.
- Tasks now end with a persistent summary (files changed, test result) and a
  status line — `✓ Task completed` / `✗ Task failed` / `■ Task cancelled` —
  followed by `Ready for next task.` The CLI is only exited by the user.
- The task block uses the dashboard's visual language (primary-toned rule,
  `Task  ·  Code Mode` heading, indented step details) so progress reads as
  part of the UI rather than a separate debug pane.
- Live command output is echoed compactly (first lines, then one elision
  note). The model and `~/.seedcode/logs/seedcode.log` still receive every
  line, and errors are never hidden.
- Plain Chat Mode is unchanged: same fast spinner, no task view.

### Default model

- `defaults.DEFAULT_MODEL` is now `cohere/north-mini-code:free`, and a fresh
  install seeds it onto the Default provider's own slot — so a release build
  needs neither a key nor a model choice to start working.
- The default is never forced onto another provider: OpenRouter, FreeModel
  Claude/Codex, AeroLink and Ollama keep their own models, and switching
  providers still cannot leak a model or a key between them.

### Providers

- Added **Default** — Seed Code's built-in API connection — as a first-class
  provider that needs no API key, and made it the provider a fresh install
  ships with (`defaults.DEFAULT_PROVIDER`).
- Default and OpenRouter are separate choices everywhere: separate `/provider`
  entry, config slot, model, connection status and credential.
- Provider-specific API-key rules are enforced by one shared helper: Default
  and Ollama require no key; OpenRouter, FreeModel Claude, FreeModel Codex and
  AeroLink each require their own. The provider picker groups choices by what
  they need and shows each provider's backend, model and key state.
- Provider configurations are now strictly isolated: the embedded release
  credential is no longer copied into OpenRouter's stored slot, and the
  previously hardcoded `freemodel_claude` migration fallbacks now resolve to
  the built-in Default provider.
- `/status` gained an `API Key` row and reads its backend label from the
  provider, so Ollama is "Local server" and Default is "Seed Code API".

### Connectivity

- Audited provider connections: streaming errors now name the provider the
  user selected rather than the internal backend, and a rejected built-in
  credential points at `/provider` instead of an API-key prompt that does not
  apply.
- Audited terminal control: execution streams output live, captures `stderr`
  with `stdout`, reports exit codes, bounds long-running commands, and kills
  the whole process tree on timeout or `Ctrl+C`. A command whose pipe closes
  while it keeps running can no longer block the UI loop.
- Code Mode, Agent Mode and Assist Mode are covered against every provider by
  a provider × mode matrix test; an unreachable catalogue or unknown provider
  produces a clear message instead of a crash.
- **Chat Mode commands**: `/chat` and `/chat on` return the session to plain
  conversation (no tools) from anywhere; `/chat` alone shows the active mode.
- **`/mode` command**: one generic switcher — `/mode chat|assist|code|agent` —
  alongside the existing direct commands (`/chat`, `/assist`, `/codemode`).
- **`/permissions` alias** for `/permission`.
- **`Retry-After` handling**: a genuine HTTP 429 reports the provider's
  requested wait, honored (capped) during the bounded retry. The retry count
  is configurable via `SEEDCODE_MAX_RETRIES` (clamped to 0–5; default 2).
- HTTP 429 is reported only for actual rate limiting: timeouts, DNS/connect
  failures, invalid keys, and unknown models keep their specific messages.
- **Environment selection overrides**: `SEEDCODE_PROVIDER` and
  `SEEDCODE_MODEL` (also readable from a project `.env`) preseed the
  first-run provider/model. A stored user selection is never overwritten.

### Distribution

- Fixed the Windows installer's `SHA256SUMS.txt` parser, which reported
  "No SHA256 checksum for …" for artifacts the release actually listed. It now
  tolerates any whitespace padding (one space, two, tabs), CRLF line endings
  and the `*` binary-mode marker; it matches the file name exactly and
  case-insensitively, still requires a 64-hex-digit digest, and **never**
  skips verification. `Invoke-WebRequest`'s byte[] response (GitHub serves
  release assets as `application/octet-stream`) is decoded instead of being
  treated as text.
- Hardened the Linux installer the same way: `sums_lookup` parses the checksum
  file with an awk program that trims whitespace, validates the digest, strips
  the binary marker and matches the exact name. A missing hashing tool now
  **refuses** the install instead of skipping verification, and the
  coreutils escape marker on MSYS/Git Bash no longer looks like a mismatch.
- Added the official **IRM installer system** in `IRM_INSTALL/`:
  `install.ps1` (Windows, PowerShell 5.1+), `install.sh` (Linux, bash) and
  `RELEASE_INFO.txt`.
- Both installers download the official release artifact, verify its SHA256
  against the release's `SHA256SUMS.txt` before installing, install per-user,
  configure `PATH`, support an existing installation, and verify the result
  with `seedcode --version`. Neither depends on a cloned repository.
- Aligned the GitHub release workflow with the artifact names the installers
  download (`SeedCode-CLI-<version>-windows-x64.exe`,
  `SeedCode-CLI-Setup-<version>.exe`, the wheel/sdist and a `SHA256SUMS.txt`
  covering every attached asset), and made it fail loudly on a version
  mismatch.
- Removed stale v6.2.0 release artifacts, the old v6.2.0 WinGet manifests and
  the associated untracked build output from the repository.
- Removed the WinGet manifest builder (`scripts/winget/build_manifest.py`),
  which nothing referenced after the WinGet channel was dropped, and the
  unused duplicate GitHub PyPI workflow (`.github/workflows/python-publish.yml`
  — the stock, unmodified template). `publish.yml` remains the single PyPI
  publish path; the wheel and sdist it builds are release assets that the
  Linux installer verifies, not a documented install channel.

### Documentation

- Rewrote `README.md`: the installation section now shows only the official
  IRM commands, and the first-run example, provider table, persistence layout
  and security model describe the v6.2.5 behaviour.
- Removed npm / pip / winget from the user-facing installation instructions;
  the release-notes body generated by the release workflow no longer
  advertises them either.
- Documented the Default provider's credential resolution and the
  provider-isolated `config.json` layout.

### Changed

- `seedcode --version` prints `Seed Code CLI 6.2.5` (machine-parseable for
  installers and scripts).
- The provider registry has six providers; `default` leads the `/provider`
  list as the zero-setup option.
- Provider display label for local Ollama is now `Ollama` (previously
  `Ollama (local)`), with the connection type shown separately.

## [6.2.0] — 2026-08-19

- Code Mode: workspace-aware agent sessions with `.seedcode` project memory,
  an incremental project index, and `/codemode on|off|status`.
- Unified permission system (read_only / workspace / desktop / full_system)
  with per-action confirmation for dangerous operations.
- Default API configuration layer: embedded release key resolution with
  user-config and environment precedence (key material never committed).
- Live terminal output streaming for run_command; process-tree cancellation.
- Dashboard redesigned around a compact 88-column grid with the pixel-art
  brand mark; removed the giant banner.

## [6.1.5] — 2026-08-17

- Universal Desktop Operator Stack: Computer Engine (mouse, keyboard,
  windows, browser, vision/OCR detection ladder) behind permission gates.

## [6.1.4] — 2026-08-17

- Packaging and installer maintenance release (Windows build pipeline,
  Inno Setup installer, WinGet manifests).
