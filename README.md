# SeedCode CLI

> **Plant ideas. Grow code.**

SeedCode is a terminal-first AI coding assistant and desktop operator. It
combines streaming chat, project-aware coding tools, screen intelligence,
semantic UI actions, browser intelligence, and permission-gated computer
control in one focused workflow.

Use the model and provider that fit the task. Keep the work local when you
want to. Give the operator only the permissions it needs.

## Why SeedCode

- **One workflow for code and computer tasks** — move from a code question to
  a project edit, window inspection, browser extraction, or desktop action
  without changing tools.
- **Provider-independent identity** — change providers or models without
  losing SeedCode's local identity, settings, or memory.
- **Permission-aware by design** — read, workspace, and full-access modes
  make control boundaries visible before an action runs.
- **Windows-ready packaging** — the self-contained installer includes the
  application runtime and preserves the SeedCode brand.
- **Recoverable operations** — lifecycle checks, self-guard protection,
  bounded retries, verification, and structured error handling keep failures
  explicit instead of silently repeating actions.

## Install

### Windows installer

Download the latest `seedcode-cli-setup.exe` from the
[Releases page](https://github.com/Alshahriar-07/seedcode-cli/releases).
The installer:

- installs a self-contained `seedcode.exe`;
- adds SeedCode to the system `PATH`;
- creates Start Menu shortcuts;
- optionally creates a desktop shortcut; and
- does not require Python on the target machine.

### PyPI

Requires **Python 3.12 or newer**:

```bash
python -m pip install seedcode-cli
seedcode
```

### WinGet

When the package is available in the Microsoft community repository:

```powershell
winget install SeedCode.CLI
```

The repository's manifests are in
[`winget/manifests/s/SeedCode/CLI/`](winget/manifests/s/SeedCode/CLI/).

## Quick start

Start the interactive application:

```bash
seedcode
```

The first-run flow helps you choose a provider, validate an API key, fetch
available models, and select a model. To check an installation without
starting the UI:

```bash
seedcode --version
seedcode --help
```

SeedCode supports:

| Provider | Best for |
| --- | --- |
| [OpenRouter](https://openrouter.ai) | A broad catalogue of free and paid models |
| FreeModel Claude | Claude-family models through FreeModel |
| FreeModel Codex | GPT/Codex models through FreeModel |
| [AeroLink](https://aerolink.lat) | Anthropic-compatible gateway access |
| [Ollama](https://ollama.com) | Local, key-free models |

API keys can be entered through the guided setup or supplied through
environment variables:

```bash
export OPENROUTER_API_KEY="sk-or-..."
export FREEMODEL_API_KEY="fe_oa_..."
export AEROLINK_API_KEY="..."
```

On Windows PowerShell, use `$env:OPENROUTER_API_KEY = "..."`.

## Operator capabilities

Assist Mode unifies AI reasoning with controlled computer actions. The
operator stack includes:

### Screen Intelligence Engine

- captures and inspects the current screen;
- resolves windows and controls through a detection ladder;
- combines accessibility metadata, geometry, OCR, and image refinement; and
- verifies the target before an action is committed.

### Semantic UI actions

Actions target the meaning of an element rather than a brittle coordinate.
The resolver can work with accessible names, roles, text, window context, and
visual evidence before dispatching keyboard or mouse input.

### Desktop application control

SeedCode can discover installed applications, inspect open windows, launch
known applications, and verify process/window state. Application installation
is permission-gated and uses the trusted `winget` path.

### Web Intelligence

The browser engine supports browser discovery, DevTools/CDP connectivity,
page extraction, pop-up handling, and browser-oriented operator skills. Web
extraction requires the existing DevTools/CDP browser connection; it does not
silently create an uncontrolled browser session.

### Memory and identity

- conversation history is saved locally;
- persistent memory can retain useful operator context;
- memory is stored per local SeedCode profile;
- identity remains stable when the active model or provider changes; and
- provider credentials remain isolated from one another.

## Assist Mode and permissions

Inside SeedCode, enable the operator with:

```text
/assist on
```

`/agent` and `/desktop` remain compatibility aliases. Inspect the current
boundary with:

```text
/permission
/computer
/tools
```

Permission modes:

| Mode | Behavior |
| --- | --- |
| `read_only` | Inspect files, screens, windows, and state without mutations |
| `workspace` | Allow approved changes inside the active workspace |
| `full_access` | Allow broader computer and filesystem actions after confirmation |

Use the narrowest mode that can complete the task. Mutating actions are
subject to permission checks, lifecycle state, verification, and retry
limits.

## Command reference

| Command | Purpose |
| --- | --- |
| `/help` | Search available commands |
| `/provider` | Switch the active AI provider |
| `/apikey` | Add, replace, remove, or validate a provider key |
| `/model` | Browse and select the provider's model catalogue |
| `/config` | Show current configuration |
| `/settings` | Open settings or change a named setting |
| `/assist` | Enable or disable Assist Mode |
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
| `/version` | Show the SeedCode version |
| `/exit` | Leave the current chat |

Useful keyboard shortcuts include `Ctrl+K` for the command palette,
`Ctrl+P` for project file search, `Ctrl+R` for history, `Ctrl+,` for
settings, and `Ctrl+/` for the shortcut reference.

## Configuration and local data

SeedCode stores local state under:

```text
~/.seedcode/
├── config.json       provider and application settings
├── history/          saved conversation sessions
├── memory/           persistent local memory
└── logs/             rotating diagnostic logs
```

Configuration and credentials are kept locally. Provider entries are isolated,
and environment variables take precedence over stored API keys. Logs are
designed for diagnostics and do not record API keys or message content.

The `doctor` command can check configuration, connectivity, provider health,
and available computer capabilities:

```text
/doctor
```

## Platform support

- **Windows:** full desktop operator support, including UI automation, OCR,
  application discovery, installer workflow, and browser integration.
- **Linux/macOS:** terminal chat, provider integrations, project tools, and
  local configuration.
- **Browser extraction:** requires the user's existing DevTools/CDP browser
  connection.

The desktop extra installs the computer-control dependencies:

```bash
python -m pip install "seedcode-cli[desktop]"
```

Optional WebDriver support is available for workflows that genuinely need an
isolated browser profile:

```bash
python -m pip install "seedcode-cli[browser]"
```

## Development

Clone the repository and install an editable development environment:

```bash
git clone https://github.com/Alshahriar-07/seedcode-cli.git
cd seedcode-cli
python -m pip install -e ".[dev]"
```

The public entry points are:

```bash
seedcode
python -m seedcode
seedcode --help
```

### Windows release build

The existing Windows pipeline generates branding assets, packages a
self-contained executable with PyInstaller, and compiles the Inno Setup
installer:

```bat
scripts\windows\build.bat
```

The build requires Python 3.12+, the project dependencies, PyInstaller, and
Inno Setup 6 (`ISCC.exe`). Build details and installer behavior are documented
in [`scripts/windows/README.md`](scripts/windows/README.md).

### Python distributions

```bash
python -m pip install build twine
python -m build
python -m twine check dist/*
```

## Release workflow

The version has one source of truth:
[`seedcode/__init__.py`](seedcode/__init__.py). Release automation uses the
same version for package metadata, the CLI, installer metadata, GitHub
releases, and WinGet manifests.

The release workflow:

1. builds and validates Python distributions;
2. builds the branded Windows installer;
3. computes the installer checksum;
4. generates WinGet manifests; and
5. publishes the release assets.

## Security model

SeedCode favors explicit capability boundaries:

- credentials stay local and provider-scoped;
- computer actions pass through permission checks;
- application installation uses `winget`;
- action targets are resolved and verified before dispatch;
- lifecycle/self-guard checks prevent unsafe operation states; and
- retry limits prevent uncontrolled repetition.

No automation system is risk-free. Review permissions before enabling Assist
Mode, especially when working in an unfamiliar project or with sensitive
applications.

## Credits

- **Created by:** Al Shahriar Sowan
- **Publisher:** Eagox Studio

## License

MIT — see [`LICENSE`](LICENSE).
