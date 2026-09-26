# Seed Code CLI

**Seed Code — Eagox Studio**

> ### Faster. Smaller. Smarter. Workspace-aware.
> *Plant ideas. Grow code.*

Seed Code is a premium terminal-based AI coding assistant and task runner.
v8.2.5 exposes exactly **two modes** — **Chat Mode** and **Agent Mode** — over a
multi-provider, multi-model engine, behind the Seed Code startup logo and a
compact block of live session state.

| Mode | What it does |
| --- | --- |
| **Chat Mode** | Conversation: questions, explanations, brainstorming. Never acts on your project. |
| **Agent Mode** | The unified autonomous workspace/coding agent: inspect, plan, edit, run, verify and keep working until the task is verified, using every available tool. |

The retired **Code Mode** and **Assist Mode** are no longer separate modes;
their capabilities now belong to Agent Mode. Agent Mode automatically activates
the workspace coding capability (`.seedcode` project memory + index, the
plan → execute → verify session loop). `/codemode`, `/assist` and `/desktop`
remain accepted aliases, but nothing in the UI presents a third mode.

- **Providers:** choose **OpenRouter**, **Ollama** (started automatically when
  you select it), or any number of your own **Custom** OpenAI-compatible
  providers with `/provider`. Custom configurations are saved without limit,
  each with its own name, base URL, key and model.
- **Reliable by design:** provider health is tracked, a failing request fails
  over to the next healthy provider without restarting the task, and a lost
  connection pauses the session instead of failing it.
- **Seed Code ASCII logo, preserved:** the fixed header leads with the exact
  Seed Code block logo and tagline, followed by the live Provider / Model /
  Mode / Status / Workspace / Context / key state. The header is never
  reprinted — every value updates in place the moment it changes — and the
  branding is never replaced by plain text or clipped to fit.
- **Professional message composer:** the fixed bottom region carries the
  `You >` prompt, which is display only and never part of the message itself.
  Multiline input keeps its continuations aligned under the prompt, `Enter`
  sends, `Shift+Enter` inserts a newline, and history, paste, long prompts,
  Unicode and terminal resize all work.
- **`AI > ◌ Thinking…` before every answer:** a lightweight animated indicator
  appears the instant you send a message and is replaced by streaming output.
  It is a UI state only — it never blocks or delays the agent.
- **Live activity, no checklist:** Agent Mode decides its own execution
  sequence and shows an activity stream (`AI > ⚙ Running pytest`,
  `AI > ✓ Tests passed`) whose lines appear only when the underlying operation
  really starts. There is no manual to-do list to create or manage, and
  completing a task never closes the application.
- **Version:** 8.2.5 (`seedcode --version`)

## Installation

After any installation method, **`seedcode` is the primary command**:

```bash
seedcode
```

### pip (any platform)

```bash
pip install seedcode-cli
```

Requires **Python 3.10 or newer**. The wheel exposes the `seedcode` console
script; `python -m seedcode` also works as a developer fallback.

Seed Code CLI is also distributed through the official IRM installer system and
[GitHub Releases](https://github.com/Alshahriar-07/seedcode-cli/releases).
The installers download the official release artifact for your platform,
verify its SHA256 against the release's `SHA256SUMS.txt`, and then verify the
installed command.

### Windows

PowerShell 5.1 or newer:

```powershell
irm https://seedcode-cli.vercel.app/install.ps1 | iex
```

Installs for the current user (no administrator rights, no Python) into
`%LOCALAPPDATA%\Programs\SeedCode`, adds `seedcode` to your user `PATH`, and
prints the verified version when it finishes.

### Linux

```bash
curl -fsSL https://seedcode-cli.vercel.app/install.sh | bash
```

Installs for the current user. No prebuilt Linux/macOS binary is published in
this release: `install.sh` installs the official Python wheel from the same
release with `pip install --user`, so it needs **Python 3.10 or newer (with
pip)** on the machine. If a prebuilt binary for your platform is ever
published, it is used directly instead. Either way `install.sh` installs only
what it can verify against the release's `SHA256SUMS.txt` — a missing
checksum entry, a mismatching digest, or a host with no SHA256 tool makes it
stop instead of installing unverified code.

### Windows installer (GUI alternative)

The release publishes two Windows binaries; both are fully self-contained (no
Python required) and both run the same CLI:

| Download | What it is |
| --- | --- |
| `SeedCode-CLI-Setup-8.2.5.exe` | **Setup installer (recommended).** A wizard that installs to Program Files, adds Seed Code to the system `PATH`, creates a Start Menu shortcut with an optional desktop shortcut, verifies the installation before reporting success, and ships a clean uninstaller that never deletes your project data silently. |
| `SeedCode-CLI-8.2.5-windows-x64.exe` | **Standalone executable.** A single portable `seedcode.exe` — no installation and no admin rights. The Windows IRM installer above downloads exactly this file. Run it directly from wherever you put it. |

Both are published on the
[Releases page](https://github.com/Alshahriar-07/seedcode-cli/releases) with
their SHA256 in `SHA256SUMS.txt`.

After installing by any route:

```bash
seedcode --version    # -> Seed Code CLI 8.2.5
```

> **`seedcode` not recognized?** Open a *new* terminal. `PATH` changes only
> apply to fresh sessions; the installers verify this before they finish.

### From source (development)

Requires **Python 3.10 or newer**:

```bash
git clone https://github.com/Alshahriar-07/seedcode-cli.git
cd seedcode-cli
python -m pip install -e ".[dev]"
seedcode
```

## First run

```bash
seedcode
```

You land in the persistent Seed Code workspace: a fixed header carrying the
Seed Code ASCII logo, a scrolling conversation region and a fixed message
composer. The header is a live dashboard — it never scrolls away, and its
values (provider, model, mode, status, workspace, context budget and the masked
API key) update in place the moment they change.

```text
╭─ Seed Code CLI v8.2.5 ───────────────────────────────────────────────────────────────────────╮
│    ▄█████ ▄▄▄▄▄ ▄▄▄▄▄ ▄▄▄▄    ▄█████  ▄▄▄  ▄▄▄▄  ▄▄▄▄▄   ▄█████ ██     ██                    │
│    ▀▀▀▄▄▄ ██▄▄  ██▄▄  ██▀██   ██     ██▀██ ██▀██ ██▄▄    ██     ██     ██                    │
│    █████▀ ██▄▄▄ ██▄▄▄ ████▀   ▀█████ ▀███▀ ████▀ ██▄▄▄   ▀█████ ██████ ██                    │
│    Plant ideas. Grow code.                                                                   │
│ OpenRouter · cohere/north-mini-code:free · Agent Mode                             ◌ Thinking │
│ Workspace D:\my-project                         Context   16,384                             │
│ API Key   sk-or-v1...e031                                                                    │
╰──────────────────────────────────────────────────────────────────────────────────────────────╯
──────────────────────────────────── Conversation ────────────────────────────────────────────
You > Build the authentication system.

AI > ◌ Thinking...

AI > I found the issue in the authentication middleware.

┌─────────────────────────────────────────| Message |──────────────────────────────────────────┐
│ › Build a login system using the existing authentication architecture.                       │
│                                                                                              │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
  Enter ↵ send  ·  Shift+Enter newline
```

The header adapts to the terminal: wide terminals get the full panel with the
logo; below 76 columns — where the 70-column block art cannot be drawn without
clipping — the *metadata* re-flows into a compact panel and then into plain
lines. The logo is omitted whole rather than truncated, and no line is ever
wider than the screen. A console that cannot encode the block glyphs gets the
wordmark form of the same layout.

The composer is a real, bounded multiline text editor: prompt_toolkit draws its
`┌─ Message ─┐` frame around the text buffer, so typed text can never escape the
border. Long lines wrap inside it and taller content scrolls internally. It has
**no** prompt prefix of its own — the message you type is exactly the message
that is sent (the `You >` in the conversation is only the history label).
`Enter` sends, `Shift+Enter` inserts a newline, and `↑`/`↓` move the cursor
inside a multiline message (walking the input history only at the edges). The
conversation follows the bottom until you scroll up — at which point the footer
shows `↓ n new` instead of pulling you back down.

Provider, model, mode and status are shown exactly once, in the header
dashboard itself — there is no second toolbar repeating them. Switch provider,
model or mode with `/provider`, `/model`, `/mode` or the `Ctrl+K` command
palette.

Below is the rendering used when the persistent interface is not available — a
piped host, `SEEDCODE_NO_TUI=1`, or a console that cannot draw it:

```text
╭─ Seed Code CLI v8.2.5 ───────────────────────────────────────────────────────────────────────╮
│    ▄█████ ▄▄▄▄▄ ▄▄▄▄▄ ▄▄▄▄    ▄█████  ▄▄▄  ▄▄▄▄  ▄▄▄▄▄   ▄█████ ██     ██                    │
│    ▀▀▀▄▄▄ ██▄▄  ██▄▄  ██▀██   ██     ██▀██ ██▀██ ██▄▄    ██     ██     ██                    │
│    █████▀ ██▄▄▄ ██▄▄▄ ████▀   ▀█████ ▀███▀ ████▀ ██▄▄▄   ▀█████ ██████ ██                    │
│                                                                                              │
│ Seed Code CLI v8.2.5                                                                         │
│ Plant ideas. Grow code.                                                                      │
│ Provider   OpenRouter                                                                        │
│ Model      cohere/north-mini-code:free                                                       │
│ Mode       Chat                                                                              │
│ Status     ● Ready                                                                           │
│ API Key    **********                                                                        │
╰──────────────────────────────────────────────────────────────────────────────────────────────╯
Commands  /help  /status  /codemode  /agent  /provider  /model
You >
```

The panel is 96 columns wide on a wide terminal — wide enough that the whole
`cohere/north-mini-code:free` model name fits without clipping. Below 78
columns the block logo cannot be drawn un-clipped, so the dashboard switches
to the text wordmark; below 64 columns it becomes a compact one-row panel, and
below 40 columns plain lines. Consoles that cannot draw (or encode) the glyphs
get the same layout in ASCII.

The `API Key` row appears **only** for providers that actually require a key,
so Default and Ollama never show one. `cohere/north-mini-code:free` is the
ships-with default for **Default only**; every other provider keeps its own
model, and switching providers never copies one model onto another.

### Default provider (no API key)

**Default** is Seed Code's built-in API connection and the provider the app
ships with. It is a first-class provider of its own — separate from
OpenRouter everywhere: its own entry in `/provider`, its own saved model, its
own status, and its own credential slot. It resolves its credential itself,
in this order:

1. a key stored in Default's own slot (advanced/manual use);
2. the embedded Seed Code release credential (release artifacts only);
3. `OPENROUTER_API_KEY` / `SEEDCODE_DEFAULT_API_KEY` in the environment.

It never reads or writes another provider's configuration, and no other
provider inherits Default's credential. If a build carries no built-in
credential (a source checkout, for example), Default says so plainly instead
of failing with an authentication error — pick OpenRouter and add your own
key.

### Your own API key

Use `/apikey` (view / replace / remove / validate) or set an environment
variable:

```bash
export OPENROUTER_API_KEY="sk-or-..."      # PowerShell: $env:OPENROUTER_API_KEY = "..."
export FREEMODEL_API_KEY="fe_oa_..."
export AEROLINK_API_KEY="..."
```

Keys are validated with a real authenticated request before they are saved.
They are stored per provider in `~/.seedcode/config.json`, never in any
project directory, and never printed, logged, or included in an error
message.

### Providers

Every provider is fully independent: its own API key, model, settings, and
connection status. Switching providers never touches another one's
configuration.

| Provider | API key | Best for |
| --- | --- | --- |
| **Default** | not required | Working immediately on a release install (`cohere/north-mini-code:free`) |
| [OpenRouter](https://openrouter.ai) | required | A broad catalogue of free and paid models |
| FreeModel Claude | required | Claude-family models through FreeModel |
| FreeModel Codex | required | GPT/Codex models through FreeModel |
| [AeroLink](https://aerolink.lat) | required | Anthropic-compatible gateway access |
| [Ollama](https://ollama.com) | not required | Local, key-free models |

Switch with `/provider` — the picker groups choices by what they need
(*Built-in · no API key*, *Your own API key*, *Local*) and shows each
provider's backend, current model, and key state. Pick models with `/model`
(OpenRouter filters `free` vs `pro` models; FreeModel offers Auto mode).

## Agent Mode and permissions

Agent Mode lets the model act on your project through the tool engine: a
multi-step task that uses real tools, verifies what it did, and reports what
actually happened instead of claiming success:

```text
/agent on
```

Permission modes (view/set with `/permission`):

| Mode | Behavior |
| --- | --- |
| `read_only` | Inspect files, screens, windows, and state without mutations |
| `workspace` | Allow approved changes inside the active workspace |
| `desktop` | Add desktop automation capability after confirmation |
| `full_system` | Allow broader computer and filesystem actions after confirmation |

## Workspace coding (part of Agent Mode)

Agent Mode is a real coding agent for the current project. Your working
directory becomes the **workspace**:

```text
/agent on           # enable Agent Mode; the workspace capability is automatic
/codemode on        # explicitly (re)activate the workspace capability
/codemode off       # deactivate it (memory stays on disk)
/codemode status    # workspace, memory, and index state
```

In Agent Mode the agent consults the project index, finds relevant files with
targeted searches, reads only what it needs, plans, edits, runs a relevant
command or test, and reports what changed. File operations stay inside the
workspace root.

### Persistent task execution

Agent Mode is a **long-running agent**, not one model call. A request becomes a
plan (a task graph with dependencies and acceptance criteria), and the session
runs task after task until each one is *verified*:

```text
Plan (3 tasks) → Task 1 → verify → Task 2 → verify → Task 3 → verify
              → final verification → project complete
```

- **A response is not completion.** A task is only `COMPLETED` when its
  acceptance criteria are satisfied by real evidence — files that exist, a
  command that ran and exited 0, tests that passed, no unresolved error. A
  model that says “done” while the tests fail is sent back to fix them.
- **A task is a unit of work, not a model call.** Each task may take many
  model/tool cycles: analyse → inspect → implement → run → fail → fix → re-run
  → verify.
- **Limits do not end a task.** An output/context limit continues the task with
  another call; a temporary provider error retries with backoff; a repeated
  identical failure stops with an explicit blocker instead of looping.
- **State survives.** Every transition is checkpointed to
  `.seedcode/checkpoints/`, so a pause, a crash or an API failure resumes from
  the current task.

```text
/session   # the detailed view: session state + one row per task record
/pause     # stop at the next safe boundary, keeping plan + files
/resume    # continue from the checkpoint (current task, not from zero)
/stop      # safely end the autonomous session
```

Ctrl+C stops the session the same way; nothing here closes an application or
rolls back your files. A stopped session is **not** lost: the CLI says so and
`/resume` continues from the checkpoint.

Every task also keeps its own execution record — files inspected and affected,
commands run with their outcome, tool calls, test results, errors, retries,
timestamps and the verification result — and it is stored per task in
`.seedcode/plan.json`, so a resume continues with the full per-task history.
`/session` renders that record:

```text
State            RUNNING
Progress         1/3 tasks verified
Current task     2. Create database
Action           Running pytest tests/db
Model calls      12
Tool calls       31
Tests            passed (pytest -q)
Commands         1/1 ok
Files changed    1
Files inspected  2
Checkpoint       saved on pause/stop (see /resume)

✓ 1  Setup project    verified 2 criteria  1 inspected; 1 file(s); 1/1 cmd ok; tests passed
● 2  Create database  —                    no tool activity recorded
○ 3  Add tests        —                    not started
```

`/status` carries the same state in one line
(`Session   RUNNING (1/3 verified) — Task 2`), and a stopped one reports that it
is resumable. A task row only ever shows what was observed — a task that did
nothing reads `no tool activity recorded`, never a success.

### `.seedcode` project memory

Enabling the workspace capability (`/agent on` or `/codemode on`) creates a `.seedcode/` directory in the project root:

```text
my-project/
├── .seedcode/
│   ├── memory/       durable project knowledge (architecture, decisions…)
│   ├── index/        per-file summaries + a file map (incremental, hashed)
│   ├── context/      reusable project context (conventions, snippets)
│   ├── sessions/     compact per-session summaries (never raw transcripts)
│   ├── checkpoints/  resumable agent session state
│   ├── plan.json     the current task graph
│   └── config.json   safe project configuration
├── src/
└── ...
```

- **Incremental indexing** — every indexed file is hashed; only changed files
  are re-summarized on the next run.
- **Secrets never land here** — writes pass a secret-key filter; API keys,
  tokens, and passwords are rejected at write time.
- **Not source code** — `.seedcode/` is excluded from workspace search,
  indexing, and the agent's project view.

## Task flow (Agent Mode)

Every Agent Mode task is shown as a live flow, and each line appears only when
the work behind it really happened. In the persistent interface the header
carries the status and the conversation carries the activity stream; in the
sequential console Agent Mode additionally shows the plan as a checklist. Both
reflect what the agent really did:

```text
╭─ SEEDCODE 8.2.5 • AGENT MODE ───────────────╮
│ ● RUNNING   Task 3/8   Build authentication │
│   ████████████░░░░  72% • 4m 32s • 18 calls │
│ → Running: pytest tests/auth                │
╰─────────────────────────────────────────────╯
✓ Setup project
✓ Create database
● Implement authentication
○ Build dashboard
○ Add tests
```

The action line is a real phase, not decoration: it shows the tool actually
running (reading a file, editing one, running a command), and it switches to
`Verifying acceptance criteria` / `Verifying the project (tests / build)` when
the session moves into those phases. The panel re-fits itself on every refresh,
so resizing the terminal mid-session cannot leave a panel wider than the
screen — it degrades to a single status line when the space runs out.

Agent Mode keeps the event-driven step flow:

```text
Task  ·  Agent Mode
Fix authentication persistence
✓ Analyze project  request understood
✓ Inspect files  read_file seedcode/config.py
✓ Plan implementation  I'll patch the persistence layer…
● Implement changes  edit_file seedcode/config.py
○ Run tests
○ Verify result
  Working…
```

The five states are `pending`, `running`, `completed`, `failed` and
`skipped`. A step the task never needed is reported as **skipped** — “Run
tests” is never marked done unless a recognised test command actually ran, and
a failing run is shown as failed, showing what failed instead of a passing
count:

```text
✓ Task completed
  2 file(s) changed: seedcode/config.py, seedcode/providers.py
  Tests: 898 passed — pytest tests -q
Ready for next task.
```

A whole agent project closes with the evidence that verified it, not a
restatement of the model's replies:

```text
✓ Project completed — 3/3 tasks verified
  ✓ Verification: accepted
  ✓ Tests: passed
  ✓ Files: 4 affected
  ✓ Commands: 7/7 ok
  • Inspected: 12 item(s)
Ready for next task.
```

```text
✗ Task failed  —  tests failed
  1 step(s) failed: Run tests
Ready for another task.
```

A task never closes the CLI: success, failure and `Ctrl+C` all return to the
prompt (or the menu) so you can inspect the result, run another task, switch
mode or provider, or `/exit` yourself. Plain Chat Mode is unaffected — it
answers with the ordinary spinner.

## Terminal execution

The agent runs commands through the tool engine's `run_command` tool:

- output streams line-by-line **while the command is still running**, so long
  builds and test runs stay visible instead of blocking. The live view is
  compact: the first few lines of each command are echoed, then one summary
  line. The agent and `~/.seedcode/logs/seedcode.log` still receive the full
  output, and failures are never hidden;
- `stderr` is captured along with `stdout`, in order;
- the exit code is reported, and a non-zero exit is an explicit failure the
  model can react to;
- commands have a bounded timeout (default 60s, up to 300s) and a timeout
  kills the whole process tree;
- `Ctrl+C` cancels the running command — its process tree is terminated — and
  the agent turn continues with the cancellation reported as a failed result;
- shells: `cmd`, `powershell`, `pwsh`, `bash`, or `auto` (the shell you are
  actually in), on Windows and Linux.

## Command reference

| Command | Purpose |
| --- | --- |
| `/help` | Search available commands |
| `/provider` | Switch the active AI provider |
| `/apikey` | Add, replace, remove, or validate a provider key |
| `/model` | Browse and select the provider's model catalogue |
| `/mode` | Show or switch the mode: `chat` / `agent` (`code` is an alias for Agent) |
| `/chat` | Switch to plain Chat Mode (`/chat on`) |
| `/agent` | Select Agent Mode (`on` / `off`; aliases `/assist`, `/desktop`) |
| `/codemode` | Agent Mode workspace capability (`on` / `off` / `status`) |
| `/workspace` | Show the active Agent Mode workspace |
| `/session` | Inspect the agent session: state, evidence, per-task records |
| `/pause` | Pause the running agent session (state is kept) |
| `/resume` | Resume a paused session from its checkpoint |
| `/stop` | Safely stop the running agent session |
| `/permission` | View or set the Agent Mode permission level (alias `/permissions`) |
| `/computer` | Show Computer Engine status and permissions |
| `/screenshot` | Capture a screenshot |
| `/windows` | List open windows |
| `/tools` | List the tools available to the agent modes |
| `/index` | Show a compact project tree |
| `/files` | Search project files |
| `/history` | Browse saved sessions |
| `/doctor` | Diagnose configuration, network, and provider health |
| `/theme` | Change the terminal theme |
| `/shortcuts` | Show keyboard shortcuts |
| `/reset` | Forget the current conversation context |
| `/clear` | Clear the screen |
| `/version` | Show the Seed Code version |
| `/exit` | Leave the current chat (opens the main menu) |

### Persistent interface (TUI)

Running `seedcode` on an interactive console opens the persistent workspace.
The three regions are fixed: the header never scrolls away, only the
conversation scrolls, and the composer stays at the bottom.

| Key | Action |
| --- | --- |
| `Enter` | Send the message |
| `Shift+Enter` (or `Ctrl+J` / `Alt+Enter`) | Insert a newline without sending |
| `↑` / `↓` | Walk the input history |
| `Ctrl+C` | Cancel the running turn (or clear the composer when idle) |
| `Esc` | Clear the composer / deny a pending permission prompt |
| `Ctrl+K` | Command palette |
| `Ctrl+P` | Project file search |
| `Ctrl+R` | Saved session history |
| `Ctrl+,` | Settings |
| `Ctrl+/` | Shortcut reference |
| `Ctrl+L` | Clear the conversation |
| `PageUp` / `PageDown`, `Ctrl+Home` / `Ctrl+End` | Scroll the conversation |
| Mouse wheel | Scroll the conversation |
| `Ctrl+D` | Exit |

Only the conversation scrolls, and output is repainted incrementally: there is
no clear-screen-and-redraw loop, so the header and composer stay stable while a
model streams, the thinking indicator animates, commands run, tool events
appear, or the terminal is resized.

Set `SEEDCODE_NO_TUI=1` to use the sequential console instead (as the
`SEEDCODE_PLAIN` mode and piped hosts do automatically).

All exit paths are clean: `/exit` → menu, menu → Exit, `Ctrl+C` (cancels a
response or the current line), and `Ctrl+D`/EOF. No traceback appears on
normal exit.

## Desktop control

With the `desktop`/`full_system` permission level, Seed Code can inspect
windows, resolve UI elements semantically (accessibility tree, OCR, and image
refinement — no brittle coordinates), launch and focus applications, and
drive keyboard/mouse with per-action verification. Waits are state-based:
`open_app` polls for real window evidence instead of sleeping, verification
pauses are short because state is re-read live, and screenshots are taken
only when information is genuinely needed.

**Applications stay open.** When Seed Code opens an application or a browser
to do something for you ("play *this* song"), the app remains open when the
task finishes. Closing is only ever done on an explicit request
(`close_app` / `browser_close`); no teardown, retry, or cleanup path may close
a window, a browser, or a tab behind your back. Internal cleanup (releasing
mouse/keyboard control, closing DevTools sockets, dropping cached drivers) is
unchanged, and Seed Code never closes its own terminal — `/exit` does that.

## Configuration and local data

```text
~/.seedcode/
├── config.json       provider and application settings
├── history/          saved conversation sessions (per provider)
├── memory/           persistent local memory
└── logs/             rotating diagnostic logs
```

`config.json` keeps **one isolated entry per provider**:

```text
config.json
├── active_provider
├── providers
│   ├── default          { api_key(unused), model }
│   ├── openrouter       { api_key, model }
│   ├── freemodel_claude { api_key, model }
│   ├── freemodel_codex  { api_key, model }
│   ├── aerolink         { api_key, model }
│   └── ollama           { api_key(unused), model }
```

Switching providers loads that provider's own key and model, and saving one
provider never overwrites another's. Credentials stay local; environment
variables take precedence over stored keys. Logs never record API keys or
message content. `/doctor` checks configuration, connectivity, and provider
health.

## Platform support

- **Windows:** full experience — desktop control, one-click installer,
  standalone EXE. Primary platform.
- **Linux / macOS:** terminal chat, providers, project tools, Agent Mode workspace.
  Install with the `install.sh` command above — it installs the official
  Python wheel, so **Python 3.10+ (with pip)** is required. No prebuilt
  Linux/macOS binary is published in this release.

## Building from source

### Python distributions

```bash
python -m pip install build twine
python -m build          # wheel + sdist, version read from seedcode/__init__.py
python -m twine check dist/*
```

### Windows EXE + installer

```bat
scripts\windows\build.bat
```

Stage 0 generates the branding assets (icon, wizard art, exe version
resource). Stage 0b embeds the default API configuration from a local `.env`
(git-ignored; skipped when absent — never printed or committed). Stage 1
builds the self-contained `dist\seedcode.exe` with PyInstaller (icon +
version resource embedded) and verifies it. Stage 2 compiles the Inno Setup
installer and verifies it. Stage 3 stages everything into
`dist\release\<version>\` and writes `SHA256SUMS.txt` with real hashes. Every
stage fails loudly on a version mismatch, so a stale binary can never ship.

Details: [`scripts/windows/README.md`](scripts/windows/README.md).

### Release artifacts (v8.2.5)

Release: [v8.2.5](https://github.com/Alshahriar-07/seedcode-cli/releases/tag/v8.2.5)

| Artifact | Purpose |
| --- | --- |
| `SeedCode-CLI-Setup-8.2.5.exe` | Windows installer (Inno Setup) |
| `SeedCode-CLI-8.2.5-windows-x64.exe` | Standalone Windows EXE — downloaded by the Windows IRM installer |
| `seedcode_cli-8.2.5-py3-none-any.whl` | Python wheel — downloaded by the Linux IRM installer |
| `seedcode_cli-8.2.5.tar.gz` | Python source distribution |
| `SHA256SUMS.txt` | SHA256 checksums; verified by both installers |

Built artifacts are collected in `dist/release/8.2.5/` during a release
build. Publishing (GitHub Release) is a separate step; the remote installers
read the release named `v8.2.5`.

### The remote installers

[`IRM_INSTALL/`](IRM_INSTALL/) is the source of the scripts served at
`https://seedcode-cli.vercel.app`:

| File | Served at |
| --- | --- |
| `install.ps1` | `/install.ps1` (Windows) |
| `install.sh` | `/install.sh` (Linux) |
| `RELEASE_INFO.txt` | Official installation summary |

## Troubleshooting

- **`seedcode` is not recognized** — open a *new* terminal after installing;
  `PATH` changes only apply to fresh sessions. The installer verifies this
  before it finishes.
- **"Setup needed" on the header** — the active provider is not usable yet.
  Run `/provider`. Default needs a built-in credential (release builds have
  one); OpenRouter/FreeModel/AeroLink need your own key.
- **Default says the built-in connection is unavailable** — this build has no
  embedded credential. Run `/provider` and choose OpenRouter, or set
  `OPENROUTER_API_KEY`.
- **401/403 errors** — your key is invalid or lacks access; `/apikey` to
  replace it, `/doctor` for diagnostics.
- **402 errors** — the model needs credits; `/model` and pick a free model
  (Default ships with `cohere/north-mini-code:free`).
- **Rate limits (429)** — wait and retry; Seed Code honors the provider's
  `Retry-After` hint. Consider a different provider.
- **Desktop actions fail** — check `/permission` (desktop requires the
  `desktop` level) and `/computer` for engine status.
- **Reset everything** — delete `~/.seedcode/` (settings, keys, history);
  `.seedcode/` project memory lives in each project and is separate.

## Security model

- Credentials stay local and **provider-scoped**. Saving or switching one
  provider never reads or writes another provider's key slot, so a key cannot
  leak between Default, OpenRouter, FreeModel, AeroLink and Ollama.
- The built-in Default credential is resolved per request and is never copied
  into another provider's stored configuration.
- Keys are shown masked (`sk-or-••••••••`) or not at all — never in full,
  never in logs, never in error messages, never in a release artifact.
- Computer actions pass permission checks, verification, and retry limits.
- `.seedcode/` memory refuses secret-looking fields at write time.
- The release build consumes the local `.env` only during packaging; the
  secret never enters source control, logs, manifests, or package metadata.

Review permissions before enabling Agent Mode — especially in unfamiliar
projects or with sensitive applications.

## Credits

- **Created by:** Al Shahriar Sowan — <https://alshahriarsayon.vercel.app/>
- **Studio:** Eagox Studio — <https://eagoxstudio.vercel.app/>
- **Contact:** seedcode.ai@gmail.com

## License

Seed Code CLI is licensed under the [PolyForm Noncommercial License 1.0.0](LICENSE) (`PolyForm-Noncommercial-1.0.0`) — free for personal learning and other noncommercial use; commercial use is not permitted. See [`LICENSE`](LICENSE) for the full terms.
