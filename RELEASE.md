# Seed Code CLI v8.2.5 — Release Notes

**Version:** 8.2.5 · **Tag:** `v8.2.5`

v8.2.5 is a **terminal-workspace** release. Seed Code is a serious AI coding
workspace that happens to run inside a terminal: a persistent, three-region
interface with the Seed Code ASCII logo permanently in the fixed header, a
professional bounded message composer, and a single unified **Agent Mode** that
does the work instead of describing it. Everything that already worked —
providers, models, the tool system, the permission model, project memory,
commands and shortcuts — is preserved. The former **Code Mode** is no longer a
separate mode: its workspace coding capabilities are now native to Agent Mode.

---

## What's new in 8.2.5

### Two modes: Chat and Agent

Seed Code exposes exactly **two** user-facing modes:

| Mode | What it does |
| --- | --- |
| **Chat Mode** | Conversation: questions, explanations, brainstorming. Never acts on your project. |
| **Agent Mode** | The unified autonomous workspace/coding agent: inspect, plan, edit, run, verify and keep working until the task is verified, using every available tool. |

The retired **Code Mode** and **Assist Mode** are this mode under their old
names. Agent Mode automatically activates the workspace coding capability
(`.seedcode` project memory + index, the persistent plan → execute → verify
session loop). `/codemode`, `/assist` and `/desktop` remain accepted aliases
that route into Agent Mode, but nothing in the interface presents a third mode.

```text
/agent on      # enable the unified Agent Mode (workspace capability included)
/agent off     # return to plain Chat
/mode agent    # the same switch through the generic mode command
/codemode on   # explicitly (re)activate the workspace capability
```

### The header keeps the Seed Code ASCII logo

The Seed Code block logo is the product's identity, so it is **not** replaced by
`SEED CODE` text: it is the first thing in the fixed header, exactly as before,
now with the live session state underneath it.

```text
╭─ Seed Code CLI v8.2.5 ───────────────────────────────────────────────────────────────────────╮
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

The art is written verbatim (never whitespace-collapsed) and is only replaced
when the terminal genuinely cannot render it: below 76 columns the 70-column
block art cannot be drawn un-clipped, so the **metadata** re-flows into a
compact panel and then plain lines while the layout keeps its information, and a
legacy console that cannot encode the block glyphs gets the wordmark form. A
clipped or mangled logo is never shown.

### Three fixed regions, one reactive state

| Region | Behaviour |
| --- | --- |
| **Header** | Fixed. A live dashboard rendered from application state on every frame; never reprinted, never scrolls away. Provider, model, mode and status each appear exactly once. |
| **Conversation** | The only scrolling region. User messages, streamed assistant output, live agent activity, tool/command output, errors and completions. Follows the bottom while you are at the bottom; stops following the moment you scroll up. |
| **Composer** | Fixed at the bottom. A real bounded multiline editor wrapped in a `╭─ Message ─╮` frame; `Enter` is the single submission path (there is no separate Submit button). |

A single `AppState` (`seedcode/ui/state.py`) holds the live session values —
provider, model, mode, status, workspace, masked key, context budget, messages,
activities, current operation, connection state. The header, the content region
and the composer are rendered from it, and a change repaints only what changed.
There is no clear-screen-and-redraw loop, so streamed output appears smoothly
and the header and composer stay stable while it does.

### The composer: a real bounded textbox

```text
╭─ Message ────────────────────────────────────────────────╮
│ › Build the authentication system.                        │
│   Requirements:                                           │
│   - Use the existing project structure                    │
│   - Add validation                                        │
│   - Run tests                                             │
╰──────────────────────────────────────────────────────────╯
  Enter ↵ send  ·  Shift+Enter newline
```

`Enter` sends, `Shift+Enter` (or `Ctrl+J` / `Alt+Enter`) inserts a newline, and
`↑`/`↓` move the cursor inside a multiline message (walking the input history
only at the first/last line). The composer is a prompt_toolkit `Frame` around
the editor, so the border *is* the visual boundary: long lines wrap inside it,
taller content scrolls internally, and typed text can never leak outside the
border. The composer carries **no** `You >` prefix — the textbox holds exactly
what you typed. `You >` is only the conversation-history label.

### `AI > ◌ Thinking…` before every answer

Sending a message immediately shows a lightweight, animated thinking state in
the conversation region; it is replaced the moment the provider starts
streaming:

```text
You > Fix the authentication bug.

AI > ◌ Thinking...

AI > I found the issue in the authentication middleware.
```

The animation is a UI state only. It runs on its own daemon thread, stops the
instant real output arrives, and never blocks or delays the model call — if the
provider answers immediately the sequence is simply
`THINKING → STREAMING → READY`. A finished Agent Mode task ends on
`✓ Completed`.

### Live activity, no checklist

The visible Agent/Chat to-do checklist is gone. Agent Mode decides its own
execution sequence and the middle region shows an activity stream whose lines
appear only when the underlying operation really starts:

```text
You > Build a login system.

AI > ◌ Thinking...
AI > ⚙ Inspecting project structure...
AI > ⚙ Reading src/auth/login.py
AI > ⚙ Editing authentication logic...
AI > ⚙ Running tests...
AI > ⚙ Fixing test failure...
AI > ✓ Tests passed
```

Nothing is fabricated: an event appears when the tool call really happens, and
completing a task never closes the application.

### Interactive permissions in a bounded panel

A permission request is a first-class TUI component — a bounded panel shown
between the conversation and the composer, inside the same application (never a
nested prompt, never a blocking read):

```text
╭─ Agent Action ────────────────────────────────────────────╮
│ Agent wants to run:                                       │
│ npm install                                               │
│ Reason: Run command                                       │
│   [A] Allow   [D] Deny   [Y] Allow session                │
│   Enter → Allow   Esc → Deny                              │
╰──────────────────────────────────────────────────────────╯
```

Keys: `Enter` allows once, `A` allows for the session, `D` denies, `Esc`
cancels/denies. Only the agent turn waits — the event loop, the header and the
composer stay live while you decide. Harmless read operations (reading project
files, listing directories, searching sources, inspecting structure) do not
prompt; consequential actions go through the existing permission model.

### Stable single-application lifecycle

The prompt_toolkit `Application`, the `Layout` and every region are built
**once** per session and re-entered. Mode switches (`/agent on`, `/agent off`,
`/mode`, `/chat`, `/codemode`) run *inside* the live application as a state
transition instead of exiting the event loop and reconstructing the screen.
Turns run on a background worker while the UI thread owns the screen, so a
long-running command or a slow provider can never freeze the interface, and
cancellation/exit stop the thinking animation and release the application
cleanly.

### Interface behaviour

- **Incremental rendering** — streamed tokens update only the conversation
  region at a throttled rate; the header and composer are never rebuilt. (The
  previous per-command application rebuild that caused switching flicker/freeze
  is gone.)
- **Terminal resize** — every region re-fits to the new size on the next frame;
  the header degrades to a compact form rather than overflowing, and no line is
  ever wider than the terminal.
- **Scrolling** — mouse wheel, `PageUp`/`PageDown`, `Ctrl+Home`/`Ctrl+End`, and
  a `↓ n new` hint in the composer when output arrives below the viewport.
- **Ctrl+C** — cancels the running turn cooperatively and leaves the session
  usable; `Esc` clears the composer or denies a pending permission prompt;
  `Ctrl+D` exits.
- **Commands and shortcuts** — `/help`, `/status`, `/provider`, `/model`,
  `/mode`, `/codemode`, `/agent`, `/permission`, `/settings`, `/doctor` and the
  rest keep working; `Ctrl+K` palette, `Ctrl+P` files, `Ctrl+R` history,
  `Ctrl+/` shortcuts, `Ctrl+,` settings, `Ctrl+L` clear.
- **Host compatibility** — the persistent TUI is used on an interactive
  console; `SEEDCODE_NO_TUI=1`, `SEEDCODE_PLAIN`, piped input and consoles that
  cannot draw the interface keep the sequential console experience.

---

## Release artifacts (v8.2.5)

| Artifact | Purpose |
| --- | --- |
| `SeedCode-CLI-Setup-8.2.5.exe` | Windows setup installer (Inno Setup wizard, uninstaller, Start Menu entry) |
| `SeedCode-CLI-8.2.5-windows-x64.exe` | Standalone Windows executable (portable; downloaded by the Windows IRM installer) |
| `seedcode_cli-8.2.5-py3-none-any.whl` | Python wheel (`pip install seedcode-cli`) |
| `seedcode_cli-8.2.5.tar.gz` | Python source distribution |
| `SHA256SUMS.txt` | SHA256 checksums covering every artifact above |

Both remote installers read `SHA256SUMS.txt` from the release named `v8.2.5` and
refuse to install anything that is not listed or that fails its digest. Neither
of them installs a checksum it cannot verify.

## Verify a download

```bash
# Linux / macOS
sha256sum -c SHA256SUMS.txt

# Windows (PowerShell)
Get-FileHash .\SeedCode-CLI-8.2.5-windows-x64.exe -Algorithm SHA256
```

## Verification performed

Verified against this repository (offline, no network):

| Check | How | Result |
| --- | --- | --- |
| Version is 8.2.5 everywhere | `seedcode.__version__`, CLI `--version`, installer/Docs references, distribution tests | Pass |
| ASCII logo preserved | `tests/test_v825_tui.py` asserts the exact logo lines appear in the header at Unicode widths and are never clipped below them | Pass |
| Two modes only | `Mode` enum has `CHAT`/`AGENT`; `code`/`codemode`/`assist`/`desktop` resolve to Agent Mode | Pass |
| Code capabilities inside Agent Mode | `/agent on` activates the workspace capability; `/codemode on` enables it explicitly | Pass |
| Three fixed regions | header/content/composer rendered, regions fixed, resize re-fits | Pass |
| Reactive state | `AppState` subscription/notification and config sync tests | Pass |
| Header responsiveness | every width from 20 to 200 columns renders with no line over the terminal width | Pass |
| Bounded composer | `Frame`-bounded multiline editor; long input wraps inside the border | Pass |
| Thinking animation | the indicator animates, is replaced by streamed output, and a late stop cannot wipe an answer | Pass |
| `AI >` attribution | streamed answers and activity lines are prefixed | Pass |
| Input behaviour | Enter sends, Ctrl+J newline, ↑/↓ history, Ctrl+D exits, Ctrl+C cancels | Pass |
| Streaming isolation | streamed markdown commits to the conversation without touching the header | Pass |
| In-place mode switching | mode commands run inside the application; the `Application` object is reused across runs | Pass |
| Permission panel | bounded panel; Enter allows, A allows the session, D/Esc deny | Pass |
| Existing behaviour preserved | full suite (987 tests) | Pass |

The Windows standalone executable and the Inno Setup installer are produced by
`scripts/windows/build.bat` on a Windows release runner (PyInstaller + Inno
Setup); the checksums in `SHA256SUMS.txt` are computed from those final
artifacts at build time.

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
seedcode --version    # -> Seed Code CLI 8.2.5
```

## Upgrade safety

Upgrades preserve user configuration and provider/API settings. Configuration
lives in the Seed Code config directory (`~/.seedcode/`), never inside the
install directory, and is never deleted by an installer.

---

Version **8.2.5**, tag **`v8.2.5`** —
<https://github.com/Alshahriar-07/seedcode-cli/releases/tag/v8.2.5>
