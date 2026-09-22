# Seed Code CLI v7.1.0 — Release Notes

**Version:** 7.1.0 · **Tag:** `v7.1.0`

v7.1.0 makes **Code Mode a real long-running software-engineering agent**. One
model response is no longer a task result: a request becomes a plan — a task
graph with dependencies and acceptance criteria — and the session works each
task through as many model/tool cycles as it needs, verifies it against
observed evidence, repairs its own failures, and only then starts the next one.
The Code Mode UI was redesigned into a compact professional header with a live
action line and a `/session` inspection view, and the desktop-control cleanup
paths can no longer close an application the agent opened.

Everything below is behavior that is implemented and covered by the test suite
in this repository.

## Official installation

Windows:

```powershell
irm https://seedcode-cli.vercel.app/install.ps1 | iex
```

Linux:

```bash
curl -fsSL https://seedcode-cli.vercel.app/install.sh | bash
```

Both installers download the official release artifact, verify its SHA256
against the release's `SHA256SUMS.txt` **before** installing, install per-user,
configure `PATH`, and finish by running `seedcode --version`. Verification is
mandatory: a missing checksum entry, a mismatching digest, or a host with no
SHA256 tool makes the installer stop instead of installing unverified code.

## Release assets

Version **7.1.0**, tag **`v7.1.0`** —
<https://github.com/Alshahriar-07/seedcode-cli/releases/tag/v7.1.0>

| Asset | Kind |
| --- | --- |
| `SeedCode-CLI-7.1.0-windows-x64.exe` | Standalone Windows executable (portable; downloaded by the Windows IRM installer) |
| `SeedCode-CLI-Setup-7.1.0.exe` | Windows setup installer (Inno Setup wizard, uninstaller, Start Menu entry) |
| `seedcode_cli-7.1.0-py3-none-any.whl` | Python wheel (downloaded by the Linux IRM installer when no prebuilt binary is published for the platform) |
| `seedcode_cli-7.1.0.tar.gz` | Python source distribution |
| `SHA256SUMS.txt` | SHA256 digest for every attached asset; verified by both installers |

Artifact names are part of the release contract — the installers look each one
up by exact name in `SHA256SUMS.txt`, so they must not be renamed. Every
release must attach the full set above, including the wheel: `install.sh` has
no verifiable artifact to install on Linux/macOS without it.

## Session engine

Code Mode now drives a **task graph** instead of a single agent turn:

```
request → plan → task graph → for each task (dependency order):
            work → model/tool cycles → verify acceptance criteria
            → repair and re-verify on failure → COMPLETED
          → final verification → project completed
```

- **Explicit task lifecycle.** `PENDING · RUNNING · VERIFYING · BLOCKED ·
  RECOVERING · FAILED · COMPLETED · CANCELLED`, with the state shown in the UI
  and persisted with the plan.
- **Dependencies.** A task is not started until its prerequisites are
  completed; the scheduler activates the next runnable task automatically, so
  Task 1 → Task 2 → Task 3 proceeds without a second user message.
- **Acceptance criteria are machine-checked.** `file: <path>`,
  `file: <path> | contains: <text>`, `run: <command>`, `tests`, `no-errors` are
  verified against what actually happened. A model that replies "done" while
  the tests fail is sent back to fix them.
- **Per-task execution records.** Each task maintains its current action, files
  inspected and affected, commands with their outcome, tool calls, test
  results, errors, retry count, timestamps and its verification result. The
  record is written to `.seedcode/plan.json`, so a resumed session knows per
  task what was already done.
- **Evidence, not claims.** `TurnEvidence` is filled by the tool loop itself
  (the files a mutating tool really wrote, the exit status of the commands that
  really ran, the tests among them, unresolved errors). Task completion is
  decided from that evidence.
- **A task is a unit of work, not a model call.** A task may take many model
  calls, many tool calls, several file reads and writes, several shell
  commands, failing tests, fixes, retries and a final verification — the loop
  continues until the task is complete, blocked, cancelled or genuinely failed.
- **Inspected files are tracked** as part of the persistent session context, so
  a resumed task does not re-read what it already looked at.

## Reliability

- **Provider retry with backoff.** A failed model request is retried (bounded,
  with growing delay) and only then surfaces as a clean failure — the
  checkpoint keeps the position so the work is resumable.
- **Command failure → diagnose → fix → re-run.** A failing command becomes a
  repair cycle with the real error text; the task is not completed until the
  re-run passes.
- **Repeated-failure blocker.** If the same action produces the same failure
  without progress, the task stops as `BLOCKED` with an explicit reason instead
  of looping forever. There is no arbitrary "maximum 3 model calls" cap — a
  large project may legitimately need many iterations.
- **Context compaction.** Raw history beyond a recent window is folded into a
  structural summary at safe message boundaries, so long sessions keep a
  bounded context without losing the working state.
- **Checkpoint / resume.** Every meaningful transition is persisted to
  `.seedcode/checkpoints/latest.json` (plan, current task, progress, changed
  files, discoveries, commands, errors, tests, fixes, blockers, next action,
  counters). A pause, `Ctrl+C`, provider failure, output/context limit or
  interruption resumes from the current task rather than from zero.
- **Unicode regression fixed.** A lone/unpaired surrogate (from model output, a
  tool result, file content, terminal bytes or a serialized structure) used to
  abort a session with `'utf-8' codec can't encode … surrogates not allowed`.
  All text now passes one normalization at every boundary: a split high+low
  pair is repaired back into its real character, an unpaired surrogate becomes
  U+FFFD, and valid Unicode (Bangla, Arabic, Chinese, Japanese, Cyrillic,
  emoji, combining marks) is untouched.

## UI

- **Compact Code Mode header** — two rows while working, one while idle, no
  decorative art:

  ```text
  ╭─ SEEDCODE 7.1.0 • CODE MODE ────────────────╮
  │ ● RUNNING   Task 3/8   Build authentication │
  │   ████████████░░░░  72% • 4m 32s • 18 calls │
  │ → Running: pytest tests/auth                │
  ╰─────────────────────────────────────────────╯
  ```

- **Live action line.** One line describing the real work — `→ Reading
  src/auth/login.py`, `→ Editing package.json`, `→ Running pytest tests/auth` —
  which switches to `Verifying acceptance criteria` and `Verifying the project
  (tests / build)` when the session enters those phases.
- **Task checklist** with `✓ completed · ● current · ○ pending · ✗ failed ·
  ■ blocked/cancelled`, so the current task is always obvious.
- **`/session` inspection view.** The detailed state the live view keeps off
  screen: status, progress, current task and action, elapsed time, model/tool
  calls, recoveries, tests, commands, files inspected and changed, blockers,
  and the checkpoint/resumability of a stopped session — plus one row per task
  with its state, verification result and own execution record.
- **`/status` session row.** `RUNNING (1/3 verified) — Task 2`, or
  `PAUSED (2/3 verified) — resumable with /resume`. No row is drawn when there
  is no session.
- **Evidence-gated completion is visible.** A finished project closes with the
  evidence that verified it:

  ```text
  ✓ Project completed — 3/3 tasks verified
    ✓ Verification: accepted
    ✓ Tests: passed
    ✓ Files: 4 affected
    ✓ Commands: 7/7 ok
    • Inspected: 12 item(s)
  Ready for next task.
  ```

- **Stop/resume messaging.** `/stop` and `Ctrl+C` report that the plan,
  completed work and project files were kept and point at `/resume` — a stopped
  session is resumable, not a dead end. `/pause` takes effect at the next safe
  boundary between model calls/tool cycles; the synchronous REPL is not
  interruptible mid-call, which is why `Ctrl+C` remains the immediate stop.
- **Responsive and safe.** The panel re-fits on every refresh (a terminal
  resize cannot leave it wider than the screen), degrades to a single status
  line on narrow terminals, and renders ASCII borders, marks and progress bars
  on consoles that cannot draw or encode the glyphs. Every string is normalized
  through the same Unicode-safe layer; no UI element can end a session.
- **The desktop-safety rule is explicit:** if Seed Code opens an application
  (Chrome, Edge, VS Code, Explorer, a media player), it is left open. Only an
  explicit user request closes it.

## Desktop safety

- Opening an application or browser window records the launch in a session
  ledger. A cleanup path — teardown hook, workflow reset, any code that did not
  explicitly ask to close something — can no longer terminate a user-facing
  application: the close is refused with a clear reason.
- Explicit requests still work exactly as before (`close_app` / `app_close`,
  `browser_close`), and forced process kills require the same explicit intent.
- A blind `Ctrl+W` is no longer used as a "close the current tab" fallback,
  because it could close the application itself.
- Internal cleanup is unchanged: releasing keyboard/mouse control, closing
  DevTools sockets and dropping cached drivers still happen on teardown. The
  teardown hook explicitly leaves opened applications open and logs which ones.

## Providers, modes and compatibility

- **Providers preserved:** Default, OpenRouter, FreeModel (Claude and Codex),
  AeroLink and Ollama/Local, with their existing key rules (Default and Ollama
  need no key; everyone else needs their own) and their own model slots.
- **Modes preserved:** Chat, Code, Agent and Assist. The Code Mode session
  engine sits behind the same commands (`/codemode on`), and Assist/Agent keep
  their existing step-by-step task flow.
- **Commands preserved:** every existing command still resolves (plus the new
  `/session`), `/hotkeys`-style shortcuts and the interactive menu are
  unchanged, and the installer's assumptions about the package name, entry
  point and layout are untouched.
- **Version source is single:** `seedcode/__init__.py::__version__`. The CLI,
  the wheel/sdist (via hatchling), the installers, the Inno Setup metadata, the
  npm launcher (via `package.json`) and the docs all follow it.

## Verification performed

Every result below comes from an actual run against the artifacts in
`dist/release/7.1.0/`; nothing is claimed that was not executed.

### Build and validation results

| Check | Command | Result |
| --- | --- | --- |
| Source version | `python -m seedcode --version` | `Seed Code CLI 7.1.0` |
| Full test suite | `python -m pytest -q` | **899 passed, 0 failed** |
| Windows executable | `dist/seedcode.exe --version` | `Seed Code CLI 7.1.0` |
| Installer checksum test | `pytest tests/test_installer_checksums.py` | passed |

Staged assets in `dist/release/7.1.0/` (hashes as generated, verified below):

```text
975cf3c7c55fd8eec8ebda4eb433d5de156a49bfdc6d648ecf21f50ffa3813cc  SeedCode-CLI-7.1.0-windows-x64.exe
3ba3c20f6b02b704a04e08e159d9e86202c3df434f8e8646d288a614d96a84ea  SeedCode-CLI-Setup-7.1.0.exe
8231a5921a7bd904b1a6290c04bff428d27389d5bab395da8f4c5e3331199f5e  seedcode_cli-7.1.0-py3-none-any.whl
1ef7397860f70c7ecb38c0a6491bcec9b649394d03fdf6a80aa066030ca81804  seedcode_cli-7.1.0.tar.gz
```

- **Windows `--version`.** The portable build reports `Seed Code CLI 7.1.0`. It
  was then driven end-to-end in an isolated profile directory — interactive
  startup, a chat turn, Code Mode enabled, and the Unicode/ASCII fallback path
  on a console that cannot draw the glyphs — and exited cleanly each time.
- **Wheel.** The built wheel was installed into a fresh virtual environment,
  where `python -m seedcode --version` reported `Seed Code CLI 7.1.0` and a
  smoke run completed. The wheel contains `_default_key_template.py` (empty)
  and never the generated `_default_key.py`, so a key is not shipped in the
  published package.
- **`install.ps1`** was run against the staged 7.1.0 assets: fresh install
  (download → SHA256 verified → install → `seedcode --version` → `7.1.0`),
  re-run without `-Force` ("already installed", exit 0), `-Force` reinstall,
  and a run with an older `seedcode` shadowing it earlier on `PATH` (the
  installer reports the version of the file it actually installed by absolute
  path and warns that another copy is earlier on `PATH`).
- **`install.sh`** was validated on its Linux path: platform detection selected
  `linux-x64`, no prebuilt binary is published for that platform so it fell
  back to the wheel, the download's SHA256 was verified against the release's
  `SHA256SUMS.txt`, and the run finished with `seedcode --version → Seed Code
  CLI 7.1.0` and exit 0. Its checksum parsing was also checked against the real
  `SHA256SUMS.txt` (CRLF- and whitespace-tolerant, exact name match) including a
  stale `6.2.5` name resolving to nothing.

### Tests

- `python -m pytest -q` — **899 passed, 0 failed** (see above).
- New v7.1.0 suites: `tests/test_v710_codemode.py` (task engine, session loop,
  verification, recovery, retry, checkpoint/resume, `/pause` `/resume`
  `/stop`), `tests/test_v710_ui.py` (compact header, checklist, action line,
  responsiveness, resize, ASCII fallback), `tests/test_v710_session_view.py`
  (session view, per-task records, evidence, `/session` and `/status`),
  `tests/test_v710_unicode_safety.py` (surrogates at every boundary),
  `tests/test_v710_desktop_guard.py` (applications stay open), and
  `tests/test_v710_e2e_todo_app.py` — a real end-to-end run in which the actual
  `AgentEngine` and real tools build a small todo project (plan → TODOs → file
  creation → modification → commands → a real failing `pytest` run → a
  model-driven fix → green tests → next task → final verification), and the
  finished project is independently re-tested with pytest afterwards.

### UI rendering

- The startup dashboard, the Code Mode header, the session view and the final
  evidence block were rendered directly at 20, 30, 40, 64, 80, 88, 96, 100 and
  120 columns and on a legacy (raster-font) console, asserting that no line
  exceeds its terminal width and that a console which cannot encode the glyphs
  gets the ASCII rendering of the same information.

## Known limitations

- **Live provider calls were not exercised here.** The chat/completion paths are
  covered by offline unit tests and scripted agents; no request was sent to a
  real provider during this release pass.
- **Publishing is owner action.** No GitHub release, tag, PyPI upload, npm
  publish or Vercel deployment was performed from this environment (constraints:
  no publishing credentials, and external publishing is explicitly out of
  scope). The artifacts are built and staged locally for review.
- **Linux/macOS standalone binaries are not built.** The Linux/macOS installers
  install the wheel (or defer to the official installer); no ELF/mach-O
  artifact is produced by the Windows build pipeline.
- **The remote installer one-liners point at `https://seedcode-cli.vercel.app`,
  which this repository does not deploy.** Re-publishing that deployment is an
  owner action; the sources of truth are `IRM_INSTALL/install.ps1` and
  `IRM_INSTALL/install.sh`.
