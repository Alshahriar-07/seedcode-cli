# Changelog

All notable changes to Seed Code CLI are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project adheres to [Semantic Versioning](https://semver.org/).

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
