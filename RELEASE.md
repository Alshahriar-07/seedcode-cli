# Seed Code CLI v9.1.1 — Release Notes

**Version:** 9.1.1 · **Tag:** `v9.1.1`

v9.1.1 is an **intent and diagnostics** release on top of the v9.1.0
terminal-workspace interface. v9.1.0 gave Agent Mode the power to act; v9.1.1
makes it act *deliberately*. A turn is now classified before anything runs —
conversation, research, coding or computer — and only the capability that intent
actually needs is allowed to run, or even to be advertised to the model.

Everything that already worked is preserved: the persistent three-region
interface with the Seed Code ASCII logo in the fixed header, the bounded message
composer, the workspace picker, the permission panel, the provider system with
health tracking and failover, the tool engine, project memory, commands and
shortcuts. No capability was removed.

---

## What's new in 9.1.1

### Intent-driven agent behaviour

A deterministic, model-free classifier (`seedcode.core.intent`) resolves every
turn to one of four intents and hands the rest of the application the facts it
needs (`needs_internet`, `needs_workspace`, `needs_terminal`, `needs_desktop`,
`executes`, `reason`, `target`):

| Intent | Meaning |
| --- | --- |
| `conversation` | Answer directly. No machine access at all. |
| `research` | Answer, but current external information is required. |
| `coding` | Inspect and modify a project. |
| `computer` | Drive the browser or the desktop. |

It is pure Python — no provider call, no network — so classification can never
fail, never costs a request, and never behaves differently offline. Product
names are recognised as products, so *"What changed in the latest Node.js
release?"* is research, not a request to read a file called `Node.js`.

### The minimum-tool principle

`AgentEngine` builds its tool manifest from the intent: the `core` group always,
the `web` group only for a `research` or `computer` intent, the `desktop` group
only for a `computer` intent at a permission level that allows it. A coding task
never sees the browser tools, and a question never sees the filesystem tools.

### Zero unnecessary file access

Project context, the workspace coding preamble and the terminal hint are gated
by intent. A question that never needed your project no longer causes your
project to be read — the prompt is not silently loaded with project context
"just in case".

### Internet access is a capability of both modes

New `seedcode.core.internet` provides keyless DuckDuckGo HTML search, page
fetching with boilerplate stripped, a `research()` helper that reads the top
sources, and a char-budgeted, URL-attributed `context_block()` so retrieved text
is bounded and attributable in the prompt. Every failure path returns an empty
result instead of raising. `seedcode.tools.web` exposes `web_search` and
`web_fetch` as read-only tools in the new `web` tool group.

In Agent Mode the model calls those tools itself. In Chat Mode a research
request retrieves its sources before answering and says so while it works:

```text
You > What changed in the latest Node.js release?

AI > [Accessing internet...]
AI > [Reading relevant sources...]
AI > Node.js 24 is the current LTS line ...
```

Set `SEEDCODE_DISABLE_INTERNET=1` to turn the capability off for a session.

### Chat → Agent escalation, and back

When a request in Chat Mode is one that actually has to *execute* something, the
turn runs with the agent engine and then returns control to Chat Mode. Both
sides of the handover are announced:

```text
You > Fix the provider switching bug in src/providers.py

AI > [Agent assist - the request asks for a change to the project]
     ... the agent runs, edits and verifies ...
AI > [Temporary agent task finished - back to Chat Mode]
```

If you explicitly enable Agent Mode (`/agent on`), it **stays** on until you
turn it off — escalation is a convenience for Chat Mode, not a second source of
truth about which mode you are in.

### "Allow All" now really means the session

The action gate used to be rebuilt for every engine, so an "allow all" decision
was forgotten the moment a new engine was created and the same action type
prompted again. There is now one process-wide gate, and `allow_all`
short-circuits the check for every subsequent action. The permission panel
offers three grants on top of Deny — **Allow Once**, **Always Allow** and
**Allow All** — with the keys named on screen:

```text
╭─ Agent Action ───────────────────────────────────────────────╮
│ Agent wants to run:                                          │
│ npm install                                                  │
│ Reason: Run command                                          │
│   ❯ Allow Once       approve this action only                │
│     Always Allow     this action type, this session          │
│     Allow All        every action type, this session         │
│     Deny             block this action                       │
│   Enter allow · a allow session · A allow all · d deny        │
╰──────────────────────────────────────────────────────────────╯
```

### Provider failover explains itself

A provider switch records why it happened and reports it — `rate limited`,
`authentication rejected`, `network unreachable`, `provider unavailable`,
`repeated transient errors` — instead of switching silently. Conversation
history, task state and TODO state are still preserved across a switch, so the
work resumes at the current operation rather than restarting.

### A build that is missing its credential says so

The `.env` variable name was misspelt (`DEFULT_API_KEY`), which no consumer
read: the packaging step embedded nothing, and the installed artifact reported
the built-in Default connection as unavailable with no indication why.

`scripts/windows/embed_default_key.py` now recognises **both** documented names
(`OPENROUTER_API_KEY`, `SEEDCODE_DEFAULT_API_KEY`), rejects a typo'd name
explicitly instead of ignoring it, validates the value's shape (length,
whitespace, ASCII), and exits non-zero naming the exact fix. It never prints the
value, its length, or a fragment of it. `seedcode.default_api` gained
`builtin_status()` / `describe_builtin_status()` so the running application can
report *available / missing / invalid / unavailable* (also surfaced by
`/doctor`), again without ever revealing the credential.

---

## Interface (unchanged from 9.1.0)

The persistent terminal workspace is unchanged in 9.1.1 and is summarised here
because it is what a new install sees first.

### Select the workspace at startup

Before the interface comes up, Seed Code asks **where it may work**. This is the
folder Agent Mode reads, writes, indexes and runs commands in:

```text
  Select Workspace
  --------------------------------------------------------------
  Where should Seed Code work? Agent Mode reads, writes, indexes and
  runs commands only inside the folder you pick.

    1  Current Folder    D:\Projects\my-site
    2  Choose a Folder   browse this PC and pick any directory

  Select [1] >
```

`Current Folder` keeps the directory Seed Code was launched from. `Choose a
Folder` opens the operating system's own folder picker (Tk on any platform, then
PowerShell on Windows and `osascript` / `zenity` / `kdialog` on Linux and macOS)
and makes that directory the active workspace. Headless or scripted hosts are
never blocked: `SEEDCODE_WORKSPACE=<dir>` selects the workspace directly and
`SEEDCODE_NO_WORKSPACE_PROMPT=1` keeps the launch directory.

### Two modes: Chat and Agent

| Mode | What it does |
| --- | --- |
| **Chat Mode** | Conversation: questions, explanations, brainstorming. Escalates a single executing task to Agent Mode and returns. |
| **Agent Mode** | The unified autonomous workspace/coding agent: inspect, plan, edit, run, verify and keep working until the task is verified. |

```text
/agent on      # enable Agent Mode (workspace capability included)
/agent off     # return to plain Chat
/mode agent    # the same switch through the generic mode command
/codemode on   # explicitly (re)activate the workspace capability
```

`/codemode`, `/assist` and `/desktop` remain accepted aliases; nothing in the
interface presents a third mode.

### The header keeps the Seed Code ASCII logo

```text
╭─ Seed Code CLI v9.1.1 ───────────────────────────────────────────────────────────────────────╮
│    ▄█████ ▄▄▄▄▄ ▄▄▄▄▄ ▄▄▄▄    ▄█████  ▄▄▄  ▄▄▄▄  ▄▄▄▄▄   ▄█████ ██     ██                    │
│    ▀▀▀▄▄▄ ██▄▄  ██▄▄  ██▀██   ██     ██▀██ ██▀██ ██▄▄    ██     ██     ██                    │
│    █████▀ ██▄▄▄ ██▄▄▄ ████▀   ▀█████ ▀███▀ ████▀ ██▄▄▄   ▀█████ ██████ ██                    │
│    Plant ideas. Grow code.                                                                   │
│ Provider  OpenRouter                            Model     cohere/north-mini-code:free        │
│ Mode      Agent Mode                            Status    ◌ Working                          │
│ Workspace D:/my-project                         Context   16,384                             │
│ API Key   sk-or-v1...e031                                                                    │
╰──────────────────────────────────────────────────────────────────────────────────────────────╯
```

The art is written verbatim and is only replaced when the terminal genuinely
cannot render it: below 76 columns the *metadata* re-flows into a compact panel
and then plain lines while the layout keeps its information, and a legacy
console that cannot encode the block glyphs gets the wordmark form. A clipped or
mangled logo is never shown.

### Three fixed regions, one reactive state

| Region | Behaviour |
| --- | --- |
| **Header** | Fixed. A live dashboard rendered from application state on every frame; never reprinted, never scrolls away. |
| **Conversation** | The only scrolling region: messages, streamed output, live agent activity, tool/command output, errors, completions. |
| **Composer** | Fixed at the bottom. A bounded multiline editor in a `╭─ Message ─╮` frame; `Enter` sends, `Shift+Enter` inserts a newline. |

A single `AppState` (`seedcode/ui/state.py`) holds the live session values, and
a change repaints only what changed. There is no clear-screen-and-redraw loop,
and mode switches run *inside* the live application as a state transition
instead of rebuilding the screen.

### Interface behaviour

- **Incremental rendering** — streamed tokens update only the conversation
  region at a throttled rate; the header and composer are never rebuilt.
- **Terminal resize** — every region re-fits on the next frame; no line is ever
  wider than the terminal.
- **Scrolling** — mouse wheel, `PageUp`/`PageDown`, `Ctrl+Home`/`Ctrl+End`, and
  a `↓ n new` hint when output arrives below the viewport.
- **Ctrl+C** — cancels the running turn cooperatively and leaves the session
  usable; `Esc` clears the composer or denies a pending permission prompt;
  `Ctrl+D` exits. Completing a task never closes the application.
- **Host compatibility** — `SEEDCODE_NO_TUI=1`, `SEEDCODE_PLAIN`, piped input
  and consoles that cannot draw the interface keep the sequential console.

---

## Configuration and credentials

- `seedcode.__version__` (`seedcode/__init__.py`) is the single authoritative
  version source. The wheel, sdist, CLI `--version`, Windows executable
  resource, installer metadata and documentation all read from it.
- `OPENROUTER_API_KEY` and `SEEDCODE_DEFAULT_API_KEY` are the two recognised
  names for the built-in Default credential. The release build reads them from a
  local, git-ignored `.env` and embeds the value into the **Windows standalone
  EXE and Setup installer only** — the wheel and sdist never carry it, because
  the build hook refuses to package the generated credential module. The value
  is never printed, logged, committed or packaged in any Python distribution,
  and the generated module (`seedcode/_default_key.py`) is git-ignored.
- Configuration, history, memory and logs live under `~/.seedcode/`, never
  inside the install directory, so installers never delete user settings.
- `.seedcode/` project memory refuses secret-looking fields at write time.

## Compatibility notes

- **Python 3.10+** for the wheel and sdist (`requires-python = ">=3.10"`).
- **No API compatibility break** with 9.1.0: commands, shortcuts, configuration
  files, stored sessions and the two-mode model are unchanged.
- The `web` tool group is new; a deployment that enumerated tool groups
  exhaustively will see one additional group.
- An existing installation upgrades in place and keeps its configuration.

## Install

```powershell
# Windows
irm https://seedcode-cli.vercel.app/install.ps1 | iex
```

```bash
# Linux / macOS
curl -fsSL https://seedcode-cli.vercel.app/install.sh | bash

# Any platform (Python 3.10+)
pip install seedcode-cli
```

After installing by any route:

```bash
seedcode --version    # -> Seed Code CLI 9.1.1
```

## Release artifacts (v9.1.1)

| Artifact | Purpose |
| --- | --- |
| `SeedCode-CLI-Setup-9.1.1.exe` | Windows setup installer (Inno Setup wizard, uninstaller, Start Menu entry) |
| `SeedCode-CLI-9.1.1-windows-x64.exe` | Standalone Windows executable (portable; downloaded by the Windows IRM installer) |
| `seedcode_cli-9.1.1-py3-none-any.whl` | Python wheel (`pip install seedcode-cli`) |
| `seedcode_cli-9.1.1.tar.gz` | Python source distribution |
| `SHA256SUMS.txt` | SHA256 checksums covering every artifact above |

Both remote installers read `SHA256SUMS.txt` from the release named `v9.1.1` and
refuse to install anything that is not listed or that fails its digest. Neither
of them installs a checksum it cannot verify.

## Verify a download

```bash
# Linux / macOS
sha256sum -c SHA256SUMS.txt

# Windows (PowerShell)
Get-FileHash .\SeedCode-CLI-9.1.1-windows-x64.exe -Algorithm SHA256
```

## Verification performed

Verified against this repository on the release machine (Windows x64,
Python 3.12.10, PyInstaller 6.21.0, Inno Setup 7, npm 11.12.1):

| Check | How | Result |
| --- | --- | --- |
| Version is 9.1.1 everywhere it should be | `seedcode.__version__`, `seedcode --version`, the two IRM installers, `setup.iss`, the npm launcher, `README`, `RELEASE`, `CHANGELOG`, `IRM_INSTALL/RELEASE_INFO.txt` | Pass |
| No stale active version reference | repository-wide search for `9.1.0` / `8.2.5` / `8.1.0` / `7.2.5` / `6.2.5`: only historical changelog entries, provenance notes in module docstrings, and deliberate test fixtures remain | Pass |
| Full test suite | `python -m pytest tests/ -q` | Pass — 1136 passed, 1 skipped |
| Windows standalone EXE | built by `scripts/windows/build.bat`; `--version` reports `Seed Code CLI 9.1.1`; the Seed Code icon is verified inside the binary | Pass |
| Windows Setup installer | compiled by Inno Setup 7; the icon is verified inside the binary and the wizard metadata carries 9.1.1 | Pass (compiled and inspected; not executed — see the release report) |
| Python wheel | `python -m build`; installed into a fresh virtual environment; `seedcode --version` reports `Seed Code CLI 9.1.1`; metadata name, version, `Requires-Python >=3.10`, license expression and Core Metadata 2.4 all correct | Pass |
| Python sdist | `python -m build`; installed into a fresh virtual environment from the tarball; `seedcode --version` reports `Seed Code CLI 9.1.1` | Pass |
| npm launcher tarball | `npm pack` — 6 files, version 9.1.1 | Pass |
| Default credential works on a clean machine | `dist/seedcode.exe` run with a fresh `USERPROFILE`, both credential environment variables unset and `SEEDCODE_DISABLE_DOTENV=1`: `/doctor` reports `PASS Built-in connection - embedded release credential` and `PASS Provider reachable - 20 models available` | Pass |
| No secret in any artifact | the plaintext credential is absent from the EXE, the Setup installer, the wheel, the sdist and the npm tarball; the wheel and sdist contain no generated credential module at all | Pass |
| Package contents | wheel (150 entries) and sdist (154 entries) carry no `.env`, no caches and no secret module, and do include `core/intent.py`, `core/internet.py` and `tools/web.py` | Pass |
| Checksums | every digest in `SHA256SUMS.txt` recomputed against the final files — 5 of 5 match; the file is LF-only with the `64-hex + two spaces + name` format both installers parse | Pass |
| Installer scripts | `bash -n IRM_INSTALL/install.sh`; PowerShell AST parse of `IRM_INSTALL/install.ps1` | Pass |
| Released behaviour | the full suite plus a fresh-home run of the shipped executable (`/status`, `/doctor`) | Pass |

## Upgrade safety

Upgrades preserve user configuration and provider/API settings. Configuration
lives in the Seed Code config directory (`~/.seedcode/`), never inside the
install directory, and is never deleted by an installer.

---

Version **9.1.1**, tag **`v9.1.1`** —
<https://github.com/Alshahriar-07/seedcode-cli/releases/tag/v9.1.1>
