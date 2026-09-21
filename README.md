# Seed Code CLI

**Seed Code — Eagox Studio**

> ### Faster. Smaller. Smarter. Workspace-aware.
> *Plant ideas. Grow code.*

Seed Code is a premium terminal-based AI coding assistant. v6.2.5 pairs
streaming chat and permission-gated desktop control with **Code Mode** — a
workspace-aware coding agent backed by persistent `.seedcode` project memory
— behind a structured startup dashboard that carries the brand, the live
session state and the task flow, with no ASCII logo.

- **Works out of the box:** the shipped provider is **Default** — Seed Code's
  own built-in connection. It needs **no API key** and ships pointed at
  `cohere/north-mini-code:free`, so a fresh installation can work
  immediately. Bring your own key at any time with `/provider` → OpenRouter,
  FreeModel or AeroLink.
- **Structured dashboard:** the brand on the left, identity (`Seed Code |
  Eagox Studio`), the tagline and the live provider, model, mode and status on
  the right, behind a responsive bordered panel — and no ASCII logo anywhere.
- **Step-by-step tasks:** Code, Assist and Agent Mode show a live task flow
  (analyze → inspect → plan → implement → test → verify) that reflects what
  the agent really did, and hands the prompt back when the task ends.
- **Version:** 6.2.5 (`seedcode --version`)

## Installation

Seed Code CLI is distributed through the official IRM installer system and
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
release with `pip install --user`, so it needs **Python 3.12 or newer (with
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
| `SeedCode-CLI-Setup-6.2.5.exe` | **Setup installer (recommended).** A wizard that installs to Program Files, adds Seed Code to the system `PATH`, creates a Start Menu shortcut with an optional desktop shortcut, verifies the installation before reporting success, and ships a clean uninstaller that never deletes your project data silently. |
| `SeedCode-CLI-6.2.5-windows-x64.exe` | **Standalone executable.** A single portable `seedcode.exe` — no installation and no admin rights. The Windows IRM installer above downloads exactly this file. Run it directly from wherever you put it. |

Both are published on the
[Releases page](https://github.com/Alshahriar-07/seedcode-cli/releases) with
their SHA256 in `SHA256SUMS.txt`.

After installing by any route:

```bash
seedcode --version    # -> Seed Code CLI 6.2.5
```

> **`seedcode` not recognized?** Open a *new* terminal. `PATH` changes only
> apply to fresh sessions; the installers verify this before they finish.

### From source (development)

Requires **Python 3.12 or newer**:

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

You land on the Seed Code dashboard — the structured startup panel: branding
on the left, a divider, and the live session state on the right. The ASCII
logo is permanently gone (the brand is plain text); the layout, sections and
status indicators stay:

```text
╭─ Seed Code CLI v6.2.5 ───────────────────────────────────────────────────────────────────────╮
│                                                                                              │
│   Seed Code                                │ Seed Code  |  Eagox Studio                      │
│   AI CODING AGENT                          │ Plant ideas. Grow code.                         │
│                                            │ Provider   Default                              │
│                                            │ Model      cohere/north-mini-code:free          │
│                                            │ Mode       Chat  •  ● Ready                     │
╰──────────────────────────────────────────────────────────────────────────────────────────────╯
Commands  /help  /status  /codemode  /assist  /provider  /model
You >
```

The panel is 96 columns wide on a wide terminal — wide enough that the whole
`cohere/north-mini-code:free` model name fits without clipping — and kept
deliberately short (one blank row under the title, then the live rows; no
padding rows to scroll past). Narrower terminals slide the info section left
so the whole model name still fits (80 columns shows it in full), then fall
back to a compact one-row panel, then to plain lines. Consoles that cannot
draw (or encode) the glyphs get the same layout in ASCII.

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

## Assist Mode and permissions

Assist Mode lets the model act on your project through the tool engine:

```text
/assist on
```

Permission modes (view/set with `/permission`):

| Mode | Behavior |
| --- | --- |
| `read_only` | Inspect files, screens, windows, and state without mutations |
| `workspace` | Allow approved changes inside the active workspace |
| `desktop` | Add desktop automation capability after confirmation |
| `full_system` | Allow broader computer and filesystem actions after confirmation |

## Code Mode

Code Mode is Assist Mode sharpened into a real coding agent for the current
project. Your working directory becomes the **workspace**:

```text
/codemode on        # treat the CWD as the workspace, enable .seedcode memory
/codemode off       # back to the previous mode (memory stays on disk)
/codemode status    # workspace, memory, and index state
```

In Code Mode the agent consults the project index, finds relevant files with
targeted searches, reads only what it needs, plans, edits, runs a relevant
command or test, and reports what changed. File operations stay inside the
workspace root.

### `.seedcode` project memory

Enabling Code Mode creates a `.seedcode/` directory in the project root:

```text
my-project/
├── .seedcode/
│   ├── memory/      durable project knowledge (architecture, decisions…)
│   ├── index/       per-file summaries + a file map (incremental, hashed)
│   ├── context/     reusable project context (conventions, snippets)
│   ├── sessions/    compact per-session summaries (never raw transcripts)
│   └── config.json  safe project configuration
├── src/
└── ...
```

- **Incremental indexing** — every indexed file is hashed; only changed files
  are re-summarized on the next run.
- **Secrets never land here** — writes pass a secret-key filter; API keys,
  tokens, and passwords are rejected at write time.
- **Not source code** — `.seedcode/` is excluded from workspace search,
  indexing, and the agent's project view.

## Task flow (Code / Assist / Agent Mode)

Every task in Code Mode, Assist Mode or Agent Mode is shown as a compact live
flow, and each step changes state only when the work behind it really
happened:

```text
Task  ·  Code Mode
Fix authentication persistence
────────────────────────────────────────────
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
  Tests: 773 passed — pytest tests -q
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
| `/mode` | Show or switch the mode: `chat` / `assist` / `code` / `agent` |
| `/chat` | Switch to plain Chat Mode (`/chat on`) |
| `/agent` | Enable or disable Assist Mode (alias `/assist`) |
| `/codemode` | Workspace-aware Code Mode (`on` / `off` / `status`) |
| `/workspace` | Show the active Code Mode workspace |
| `/permission` | View or set the Assist permission mode (alias `/permissions`) |
| `/computer` | Show Computer Engine status and permissions |
| `/screenshot` | Capture a screenshot |
| `/windows` | List open windows |
| `/tools` | List tools available in Assist Mode |
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

Keyboard shortcuts: `Ctrl+K` command palette, `Ctrl+P` project file search,
`Ctrl+R` history, `Ctrl+,` settings, `Ctrl+/` shortcut reference,
`Ctrl+L` clear.

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
- **Linux / macOS:** terminal chat, providers, project tools, Code Mode.
  Install with the `install.sh` command above — it installs the official
  Python wheel, so **Python 3.12+ (with pip)** is required. No prebuilt
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

### Release artifacts (v6.2.5)

Release: [v6.2.5](https://github.com/Alshahriar-07/seedcode-cli/releases/tag/v6.2.5)

| Artifact | Purpose |
| --- | --- |
| `SeedCode-CLI-Setup-6.2.5.exe` | Windows installer (Inno Setup) |
| `SeedCode-CLI-6.2.5-windows-x64.exe` | Standalone Windows EXE — downloaded by the Windows IRM installer |
| `seedcode_cli-6.2.5-py3-none-any.whl` | Python wheel — downloaded by the Linux IRM installer |
| `seedcode_cli-6.2.5.tar.gz` | Python source distribution |
| `SHA256SUMS.txt` | SHA256 checksums; verified by both installers |

Built artifacts are collected in `dist/release/6.2.5/` during a release
build. Publishing (GitHub Release) is a separate step; the remote installers
read the release named `v6.2.5`.

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

Review permissions before enabling Assist Mode — especially in unfamiliar
projects or with sensitive applications.

## Credits

- **Created by:** Al Shahriar Sowan — <https://alshahriarsayon.vercel.app/>
- **Studio:** Eagox Studio — <https://eagoxstudio.vercel.app/>
- **Contact:** github@eagox.studio

## License

MIT — see [`LICENSE`](LICENSE).
