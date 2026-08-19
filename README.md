# Seed Code

**Plant ideas. Grow code.**

Seed Code is a premium, terminal-based AI coding assistant with five fully
independent backends — [OpenRouter](https://openrouter.ai) (full catalogue,
free/paid filtering), **FreeModel Claude** and **FreeModel Codex**
(free AI models from [freemodel.dev](https://freemodel.dev)),
[AeroLink](https://aerolink.lat), and local [Ollama](https://ollama.com).
It feels like a real developer tool — fast, minimal, and professional — in
the spirit of Claude Code, the Gemini CLI, Ollama, and Git.

```
 ███████╗███████╗███████╗██████╗      ██████╗ ██████╗ ██████╗ ███████╗
 ██╔════╝██╔════╝██╔════╝██╔══██╗    ██╔════╝██╔═══██╗██╔══██╗██╔════╝
 ███████╗█████╗  █████╗  ██║  ██║    ██║     ██║   ██║██║  ██║█████╗
 ╚════██║██╔══╝  ██╔══╝  ██║  ██║    ██║     ██║   ██║██║  ██║██╔══╝
 ███████║███████╗███████╗██████╔╝    ╚██████╗╚██████╔╝██████╔╝███████╗
 ╚══════╝╚══════╝╚═════╝╚═════╝      ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝

                         S E E D   C O D E
                      Plant ideas. Grow code.
```

## Install

### Install with PyPI

Requires **Python 3.12+**.

```bash
pip install seedcode-cli
```

This installs the `seedcode` command globally on your PATH — no manual PATH
editing, no `python seedcode.py`.

### Install with WinGet

> **Status:** The package has been prepared for the Microsoft community
> repository. `winget install SeedCode.CLI` will only work after the manifests
> in [`winget/`](winget/manifests/s/SeedCode/CLI/) have been submitted and
> **accepted** into [microsoft/winget-pkgs](https://github.com/microsoft/winget-pkgs).
> Until then, install with PyPI or the Windows installer below.

```powershell
winget install SeedCode.CLI
```

### Windows installer (no Python required)

Download **`seedcode-cli-setup.exe`** from the
[Releases page](https://github.com/Alshahriar-07/seedcode-cli/releases).
It is a self-contained Inno Setup installer: it installs `seedcode.exe` to
Program Files, adds it to your system PATH, creates Start Menu shortcuts, and
works in CMD, PowerShell, and Windows Terminal. No Python needed.

```bat
seedcode-cli-setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
```

### Run

```bash
seedcode
```

### Upgrade

```bash
winget upgrade SeedCode.CLI
```

or, for pip:

```bash
pip install --upgrade seedcode-cli
```

### Uninstall

```powershell
winget uninstall SeedCode.CLI
```

If you installed with pip:

```bash
pip uninstall seedcode-cli
```

If you used the Windows installer, uninstall from **Settings → Apps** (or
run the Inno uninstaller, which also removes the PATH entry).

### Version

```bash
seedcode --version
```

### Help

```bash
seedcode --help
```

## Requirements

- **Python 3.12 or newer** for the pip distribution.
- An **API key** for at least one provider, or a running **Ollama** server.
- Windows, Linux, or macOS (desktop-control features are Windows-only).

## Authentication / API setup

API keys are saved only after **real authenticated validation** — no format
guessing. On first launch, choosing **Start Chat** walks you through setup
(provider → API key → validate → fetch models → select → save).

| Provider | Get a key |
|----------|-----------|
| OpenRouter | <https://openrouter.ai/keys> |
| FreeModel Claude / Codex | <https://freemodel.dev/dashboard> (a `fe_oa_...` key) |
| AeroLink | <https://aerolink.lat> |
| Ollama | none — just run `ollama serve` |

API keys can also come from environment variables (these override stored
keys):

```bash
export OPENROUTER_API_KEY="sk-or-..."
export FREEMODEL_API_KEY="fe_oa_..."
export AEROLINK_API_KEY="..."
seedcode
```

## Features

- **Five independent AI providers, one CLI** — switch anytime with
  `/provider`; each provider owns its API key, model, connection status,
  client, and even its own history, so switching never loses anything:
  - **OpenRouter** — two modes on one key: **Free Models** (default) or
    **Pro Models**; switch with `free`/`pro` in the model picker or
    `/settings mode free|pro`.
  - **FreeModel Claude** — Claude API at cc.freemodel.dev; Claude-family
    models (live catalogue with a maintained fallback list). Auto mode
    (`/model auto`) picks the best available model.
  - **FreeModel Codex** — Responses API at api.freemodel.dev; GPT/Codex
    models. The same FreeModel key (fe_oa_...) works on both FreeModel
    providers, but each stores it — and its model — independently.
  - **AeroLink** — Anthropic-compatible gateway; Claude-family models,
    fetched dynamically.
  - **Ollama** — fully local and key-free; lists the models you have
    installed (`/settings host <url>` to point elsewhere).
- **Startup menu** — banner, current provider/model status, and a numbered
  menu (Start Chat, Provider, API Key, Model, Settings, About, Exit).
  Guided setup runs automatically until configuration is complete.
- **Streaming responses** with live markdown and syntax-highlighted code
  blocks.
- **Windows-first** — verified in Windows Terminal, PowerShell, CMD, and the
  VS Code terminal; one-click installer with PATH integration.
- **Conversation memory** within a session, auto-saved to history.
- **Never crashes** — network and API errors are shown as friendly messages,
  never raw tracebacks. Ctrl+C cancels the current response, not the app.
- **Quiet diagnostics** — a rotating log at `~/.seedcode/logs/seedcode.log`
  (API keys and message content are never logged).

## Commands

| Command      | Description                                            |
| ------------ | ------------------------------------------------------ |
| `/help`      | Show available commands                                |
| `/provider`  | Switch provider (OpenRouter/FreeModel Claude/FreeModel Codex/AeroLink/Ollama) |
| `/apikey`    | View, replace, remove, or validate the active key      |
| `/model`     | Browse the live model list ('auto' = FreeModel Auto)   |
| `/config`    | Show configuration (all providers' keys and models)    |
| `/settings`  | Change a setting: `username`, `stream`, `ollama_host`, `max_tokens` |
| `/doctor`    | Diagnose config, network, and provider health          |
| `/agent`     | Toggle agent mode (the AI can read, edit, search, and run commands in your project) |
| `/permission`| Show or set the agent permission mode: `read_only`, `workspace`, `full_access` |
| `/tools`     | List the tools available in agent mode                 |
| `/index`     | Show a compact tree of the current project             |
| `/history`   | List saved conversation sessions                       |
| `/reset`     | Forget the current conversation                        |
| `/clear`     | Clear the screen                                       |
| `/about`     | About Seed Code                                        |
| `/version`   | Show the version                                       |
| `/exit`      | Leave the chat (back to the main menu)                 |

## Configuration

Config lives at `~/.seedcode/config.json` (owner-only permissions where the
OS supports it). Each provider keeps its own entry, so nothing is shared or
overwritten:

```json
{
  "active_provider": "freemodel_claude",
  "providers": {
    "openrouter":       { "api_key": "sk-or-...", "model": "vendor/model" },
    "freemodel_claude": { "api_key": "fe_oa_...", "model": "claude-sonnet-4-6" },
    "freemodel_codex":  { "api_key": "fe_oa_...", "model": "auto" },
    "aerolink":         { "api_key": "...",       "model": "..." },
    "ollama":           { "api_key": "",          "model": "llama3.2" }
  },
  "ollama_host": "http://localhost:11434",
  "max_tokens": 1024
}
```

Chat history is stored per provider under `~/.seedcode/history/<provider>/`.

No model is ever hardcoded — you always pick from the provider's live
catalogue. `max_tokens` defaults to a free-tier-safe 1024 and is clamped to
1–4096 per request (older config formats migrate automatically).

Troubleshooting: check `~/.seedcode/logs/seedcode.log`; set
`SEEDCODE_DEBUG=1` for verbose logging.

## Development

Clone the repository and install in editable mode with the dev extra:

```bash
git clone https://github.com/Alshahriar-07/seedcode-cli.git
cd seedcode-cli
python -m pip install -e ".[dev]"
pytest tests/ -q
```

The `seedcode` entry point, `seedcode --version`, and `python -m seedcode`
are all verified by the CI workflow.

## Building

### Python package (wheel + sdist)

```bash
python -m pip install build twine
python -m build
python -m twine check dist/*
```

Outputs `dist/seedcode_cli-*.whl` and `dist/seedcode_cli-*.tar.gz`.

### Windows installer

Requires Python 3.12+, PyInstaller, and Inno Setup 6 (`ISCC.exe`).

```bat
scripts\windows\build.bat
```

The pipeline generates branding assets, runs the full test suite (a release
gate), builds `dist\seedcode.exe` with PyInstaller, compiles the Inno Setup
installer, and publishes it to the repository root as
`seedcode-cli-setup.exe` — verifying version and icon at every stage.

## Release process

The version lives in **one place**: `seedcode/__init__.py` (`__version__`).
Bump it there, commit, tag, and push — automation does the rest:

```bash
git tag v5.0.3
git push origin v5.0.3
```

`.github/workflows/release.yml` then:

1. checks out the tag
2. sets up Python 3.12
3. installs dependencies
4. runs the test suite
5. builds the PyPI package (wheel + sdist) and validates it with twine
6. builds the Windows installer (`seedcode-cli-setup.exe`)
7. computes the installer SHA256
8. generates the WinGet manifests for `SeedCode.CLI`
9. creates a GitHub Release and uploads the installer, checksum, Python
   distributions, and WinGet manifests

## PyPI publishing

`.github/workflows/publish.yml` publishes `seedcode-cli` to PyPI whenever a
GitHub Release is published. It uses **PyPI Trusted Publishing (OIDC)** — no
API token is stored in the repository.

**One-time manual setup** (before the first release):

1. Open <https://pypi.org/manage/account/publishing/>.
2. "Add a new pending publisher":
   - **PyPI project name:** `seedcode-cli`
   - **Owner:** `Alshahriar-07`
   - **Repository name:** `seedcode-cli`
   - **Workflow name:** `publish.yml`
   - **Environment name:** *(leave empty — the workflow does not use an
     environment)*

After that, publishing is fully automatic on each tagged release.

## WinGet submission

The WinGet manifests live in
[`winget/manifests/s/SeedCode/CLI/`](winget/manifests/s/SeedCode/CLI/).
They are generated by:

```bash
python scripts/winget/build_manifest.py --sha256 <REAL-SHA256-OF-INSTALLER>
```

(`winget hash <path-to-installer>` computes the real hash; never invent one.)
Validate locally with:

```powershell
winget validate winget\manifests\s\SeedCode\CLI\5.0.2
```

To publish on WinGet, submit the manifests to
[microsoft/winget-pkgs](https://github.com/microsoft/winget-pkgs):

1. Fork `microsoft/winget-pkgs`.
2. Copy the manifests to `manifests/s/SeedCode/CLI/<version>/` in your fork.
3. Open a pull request. Microsoft's validation pipelines check the schema,
   the installer URL, the SHA256, and that the app installs/uninstalls
   silently.
4. Once merged, `winget install SeedCode.CLI` works worldwide.

## Credits

- **Created by:** Al Shahriar Sowan
- **Publisher:** Eagox Studio
- Vibe coded with GPT-5.5 + Claude Opus 4.8

## License

MIT — see [LICENSE](LICENSE).