# Changelog

All notable changes to Seed Code CLI are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project adheres to [Semantic Versioning](https://semver.org/).

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
