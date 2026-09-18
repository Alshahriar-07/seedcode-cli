# Seed Code CLI

**Seed Code — Eagox Studio**

> ### Faster. Smaller. Smarter. Workspace-aware.
> *Plant ideas. Grow code.*

Seed Code is a premium terminal-based AI coding assistant. v6.2.0 pairs
streaming chat and permission-gated desktop control with **Code Mode** — a
workspace-aware coding agent backed by persistent `.seedcode` project memory
— on a compact, redesigned startup dashboard.

- **Works out of the box (Windows release):** the Windows EXE and installer
  ship with a built-in default OpenRouter configuration, so a fresh
  installation can chat immediately — no API key required on first launch.
  You can switch to your own key at any time (`/apikey`).
- **Version:** 6.2.0 (`seedcode --version`)

## Installation

### Windows installer (recommended on Windows)

Download `SeedCode-CLI-Setup-6.2.0.exe` (or the stable-named
`seedcode-cli-setup.exe`) from the
[Releases page](https://github.com/Alshahriar-07/seedcode-cli/releases).

The installer packages a fully self-contained `seedcode.exe` (no Python
needed), adds Seed Code to the system `PATH`, creates a Start Menu shortcut
with an optional desktop shortcut, verifies the installation before
reporting success, and ships a clean uninstaller that never deletes your
project data silently. SHA256 checksums for every release artifact are in
the release's `SHA256SUMS.txt`.

### WinGet

```powershell
winget install SeedCode.CLI
```

> **Status:** the 6.2.0 manifests are prepared in
> [`winget/manifests/s/SeedCode/CLI/6.2.0/`](winget/manifests/s/SeedCode/CLI/6.2.0/)
> with the real installer SHA256, but the package is **NOT YET PUBLISHED**
> to the Microsoft community repository — a `winget-pkgs` pull request has
> not been submitted. Until it merges, use the Windows installer above.

### PyPI

Requires **Python 3.12 or newer**:

```bash
python -m pip install seedcode-cli
seedcode
```

> **Status:** `seedcode-cli==6.2.0` wheel and sdist are built and verified
> in this repository (see Release artifacts). **NOT YET PUBLISHED** to
> PyPI — run `python -m twine upload dist/*` when ready.

### npm (Windows launcher)

```bash
npm install -g seedcode-cli
seedcode
```

> **Status:** the npm package is **NOT YET PUBLISHED** to the npm registry.
> The package in [`npm/`](npm/) is a real launcher: on Windows it downloads
> the official `SeedCode-CLI-6.2.0-windows-x64.exe` from the GitHub release,
> verifies its SHA256 against `SHA256SUMS.txt`, caches it under
> `~/.seedcode/npm/`, and runs the actual binary (it never prints fake
> instructions). On Linux/macOS it points to the pip install.

### From source

```bash
git clone https://github.com/Alshahriar-07/seedcode-cli.git
cd seedcode-cli
python -m pip install -e ".[dev]"
seedcode
```

## First run

Start the app:

```bash
seedcode
```

You land on the compact startup dashboard — provider, model, mode, and
status in one branded panel — and the chat prompt appears immediately:

```text
╭─ Seed code v6.2.0 ──────────────────────────────────────────────╮
│   [logo]   SEEDCODE CLI              │  Seed Code | Eagox Studio│
│            Plant ideas. Grow code.   │  Provider   OpenRouter   │
│                                      │  Model      deepseek/…   │
│                                      │  Mode       Assist       │
│                                      │  Status     ● Ready      │
╰─────────────────────────────────────────────────────────────────╯
You >
```

### Default OpenRouter behavior (v6.2.0)

The Windows release (EXE and installer) embeds a Seed Code default
OpenRouter credential, so the provider is ready the moment the app starts.
The resolution order is always:

1. **Your own key** — entered via `/apikey` or guided setup (stored in
   `~/.seedcode/config.json`, never in any project directory);
2. **The `OPENROUTER_API_KEY` environment variable** (CI / power users);
3. **The embedded Seed Code default** — used only when 1 and 2 are absent.

When your own key or the environment variable is set, the embedded default
is never used. The default is a temporary v6.2.0 arrangement; a server-side
gateway will replace it in a later release. pip/source installs do not
include a default credential and run guided setup on first launch.

### Your own API key

```bash
export OPENROUTER_API_KEY="sk-or-..."     # Windows PowerShell: $env:OPENROUTER_API_KEY = "..."
export FREEMODEL_API_KEY="fe_oa_..."
export AEROLINK_API_KEY="..."
```

Or manage keys interactively with `/apikey` (view / replace / remove /
validate). Keys are validated with a real authenticated request before they
are saved.

### Providers

| Provider | Best for |
| --- | --- |
| [OpenRouter](https://openrouter.ai) | A broad catalogue of free and paid models |
| FreeModel Claude | Claude-family models through FreeModel |
| FreeModel Codex | GPT/Codex models through FreeModel |
| [AeroLink](https://aerolink.lat) | Anthropic-compatible gateway access |
| [Ollama](https://ollama.com) | Local, key-free models |

Switch with `/provider`; pick models with `/model` (OpenRouter filters
`free` vs `pro` models; FreeModel offers Auto mode). Custom provider
configuration is unchanged in v6.2.0 — every provider keeps its own key,
model, and settings.

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

v6.2.0 performance work in Assist Mode: independent read-only tool calls
run in parallel, mutating steps stay sequential for correctness, tool
dispatch is faster, and desktop actions verify state live instead of
sleeping fixed delays.

## Code Mode (new in v6.2.0)

Code Mode is Assist Mode sharpened into a real coding agent for the current
project. Your working directory becomes the **workspace**:

```text
/codemode on        # treat the CWD as the workspace, enable .seedcode memory
/codemode off       # back to the previous mode (memory stays on disk)
/codemode status    # workspace, memory, and index state
```

In Code Mode the agent follows the coding-agent workflow: consult the
project index, find relevant files with targeted searches, read only what
it needs, plan, edit, run a relevant command or test, and report what
changed. File operations stay inside the workspace root.

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

- **Incremental indexing** — every indexed file is hashed; only changed
  files are re-summarized on the next run.
- **Secrets never land here** — writes pass a secret-key filter; API keys,
  tokens, and passwords are rejected at write time.
- **Not source code** — `.seedcode/` is excluded from workspace search,
  indexing, and the agent's project view (the repo `.gitignore` template
  lists it too).

## Command reference

| Command | Purpose |
| --- | --- |
| `/help` | Search available commands |
| `/provider` | Switch the active AI provider |
| `/apikey` | Add, replace, remove, or validate a provider key |
| `/model` | Browse and select the provider's model catalogue |
| `/agent` | Enable or disable Assist Mode (alias `/assist`) |
| `/codemode` | Workspace-aware Code Mode (`on` / `off` / `status`) |
| `/workspace` | Show the active Code Mode workspace |
| `/permission` | View or set the Assist permission mode |
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

All exit paths are clean in v6.2.0: `/exit` → menu, menu → Exit,
`Ctrl+C` (cancels a response or the current line), and `Ctrl+D`/EOF.
No traceback appears on normal exit.

## Desktop control

With the `desktop`/`full_system` permission level, Seed Code can inspect
windows, resolve UI elements semantically (accessibility tree, OCR, and
image refinement — no brittle coordinates), launch and focus applications,
and drive keyboard/mouse with per-action verification. v6.2.0 replaces
hardcoded waits with state-based detection: `open_app` polls for real
window evidence instead of sleeping, verification pauses are shorter
because state is re-read live, and screenshots are taken only when
information is genuinely needed.

## Configuration and local data

```text
~/.seedcode/
├── config.json       provider and application settings
├── history/          saved conversation sessions (per provider)
├── memory/           persistent local memory
└── logs/             rotating diagnostic logs
```

Credentials stay local and provider-scoped; environment variables take
precedence over stored keys. Logs never record API keys or message content.
`/doctor` checks configuration, connectivity, and provider health.

## Platform support

- **Windows:** full experience — desktop control, one-click installer,
  standalone EXE. Primary platform for v6.2.0.
- **Linux / macOS:** terminal chat, providers, project tools, Code Mode.
  Install with pip; prebuilt binaries are not published yet.

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
resource). Stage 0b embeds the default API configuration from a local
`.env` (git-ignored; skipped when absent — never printed or committed).
Stage 1 builds the self-contained `dist\seedcode.exe` with PyInstaller
(icon + version resource embedded) and verifies it. Stage 2 compiles the
Inno Setup installer and verifies it. Stage 3 stages everything into
`dist\release\<version>\` and writes `SHA256SUMS.txt` with real hashes.

Details: [`scripts/windows/README.md`](scripts/windows/README.md).

### Release artifacts (v6.2.0)

| Artifact | Purpose |
| --- | --- |
| `SeedCode-CLI-Setup-6.2.0.exe` | Windows installer (Inno Setup) |
| `SeedCode-CLI-6.2.0-windows-x64.exe` | Standalone Windows EXE |
| `seedcode_cli-6.2.0-py3-none-any.whl` | pip wheel |
| `seedcode-cli-6.2.0.tar.gz` | pip source distribution |
| `seedcode-cli-6.2.0.tgz` | npm launcher package |
| `SHA256SUMS.txt` | SHA256 checksums of the above |

Built artifacts are collected in `dist/release/6.2.0/` during a release
build; publishing (GitHub release, PyPI, npm, winget-pkgs PR) is a separate
manual step.

## Troubleshooting

- **`seedcode` is not recognized** — open a *new* terminal after
  installing; PATH changes only apply to fresh sessions. The installer
  verifies this before it finishes.
- **"Setup needed" on the dashboard** — run `/provider`, then `/model`.
  Windows-release installs start with the default OpenRouter credential;
  pip/source installs run guided setup on first launch.
- **401/403 errors** — your key is invalid or lacks access; `/apikey` to
  replace it, `/doctor` for diagnostics.
- **402 errors** — the model needs credits; `/model` and pick a free model.
- **Rate limits (429)** — wait and retry; consider a different provider.
- **Desktop actions fail** — check `/permission` (desktop requires the
  `desktop` level) and `/computer` for engine status. OCR availability is
  reported by `/doctor`.
- **Reset everything** — delete `~/.seedcode/` (settings, keys, history);
  `.seedcode/` project memory lives in each project and is separate.

## Security model

- Credentials stay local and provider-scoped; the embedded default key
  never overrides your own key or environment variable.
- Computer actions pass permission checks, verification, and retry limits.
- `.seedcode/` memory refuses secret-looking fields at write time.
- The release build consumes the local `.env` only during packaging; the
  secret never enters source control, logs, manifests, or package metadata.

Review permissions before enabling Assist Mode — especially in unfamiliar
projects or with sensitive applications.

## Credits

- **Created by:** Al Shahriar Sowan
- **Publisher:** Eagox Studio

## License

MIT — see [`LICENSE`](LICENSE).
