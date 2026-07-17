# Seed Code

**Plant ideas. Grow code.**

Seed Code is a premium, terminal-based AI coding assistant with four fully
independent backends — [OpenRouter](https://openrouter.ai) (full catalogue,
free/paid filtering), **FreeModel** (free AI models only),
[AeroLink](https://aerolink.lat), and local [Ollama](https://ollama.com).
It feels like a real developer tool — fast, minimal, and professional — in
the spirit of Claude Code, the Gemini CLI, Ollama, and Git.

```
 ███████╗███████╗███████╗██████╗      ██████╗ ██████╗ ██████╗ ███████╗
 ██╔════╝██╔════╝██╔════╝██╔══██╗    ██╔════╝██╔═══██╗██╔══██╗██╔════╝
 ███████╗█████╗  █████╗  ██║  ██║    ██║     ██║   ██║██║  ██║█████╗
 ╚════██║██╔══╝  ██╔══╝  ██║  ██║    ██║     ██║   ██║██║  ██║██╔══╝
 ███████║███████╗███████╗██████╔╝    ╚██████╗╚██████╔╝██████╔╝███████╗
 ╚══════╝╚══════╝╚══════╝╚═════╝      ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝

                         S E E D   C O D E
                      Plant ideas. Grow code.
```

## Features

- **Four independent AI providers, one CLI** — switch anytime with
  `/provider`; each provider owns its API key, model, connection status,
  client, and even its own history, so switching never loses anything:
  - **OpenRouter** — two modes on one key: **Free Models** (default) or
    **Pro Models**; switch with `free`/`pro` in the model picker or
    `/settings mode free|pro`.
  - **FreeModel** — two backends on one key: **Codex**
    (OpenAI-compatible, api.freemodel.dev) or **Claude**
    (Claude-compatible, cc.freemodel.dev), each remembering its own model;
    switch with `/settings backend codex|claude`. Auto mode (`/model auto`)
    always picks the best available model.
  - **AeroLink** — Anthropic-compatible gateway; Claude-family models,
    fetched dynamically.
  - **Ollama** — fully local and key-free; lists the models you have
    installed (`/settings host <url>` to point elsewhere).
  - API keys are only ever saved after **real authenticated validation** —
    no format guessing.
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

## Install

### Windows (recommended)

Either run the packaged installer **`Release\SeedCodeSetup.exe`** (installs
to Program Files, adds `seedcode` to PATH, creates Start Menu shortcuts), or
install from source:

```bat
scripts\windows\install.bat
```

To build the standalone exe + installer yourself:

```bat
scripts\windows\build.bat
```

Uninstall with `scripts\windows\uninstall.bat` (add `/keepdata` to preserve
your config and history).

### Any platform (pip)

Requires **Python 3.12+**.

```bash
pip install .
```

This installs the `seedcode` command globally. Linux/macOS helper scripts
live in `scripts/linux/` and `scripts/macos/`.

## Usage

```bash
seedcode
```

On first launch you get the menu; choosing **Start Chat** walks through
setup (provider → API key → validate → fetch models → select → save). For
FreeModel, get a free API key at <https://freemodel.dev/dashboard>; for
OpenRouter, create a key at <https://openrouter.ai/keys>; for AeroLink,
use your dashboard at <https://aerolink.lat>; for Ollama, just have
`ollama serve` running.

API keys can also come from environment variables (these override the
stored keys):

```bash
export OPENROUTER_API_KEY="sk-or-..."
export FREEMODEL_API_KEY="fe_oa_..."
export AEROLINK_API_KEY="..."
seedcode
```

## Commands

| Command      | Description                                            |
| ------------ | ------------------------------------------------------ |
| `/help`      | Show available commands                                |
| `/provider`  | Switch provider (OpenRouter/FreeModel/AeroLink/Ollama) |
| `/apikey`    | View, replace, remove, or validate the active key      |
| `/model`     | Browse the live model list ('auto' = FreeModel Auto)   |
| `/config`    | Show configuration (all providers' keys and models)    |
| `/settings`  | Change a setting: `username`, `stream`, `ollama_host`, `max_tokens` |
| `/doctor`    | Diagnose config, network, and provider health          |
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
  "active_provider": "freemodel",
  "providers": {
    "openrouter": { "api_key": "sk-or-...", "model": "vendor/model" },
    "freemodel":  { "api_key": "fe_oa_...", "model": "auto" },
    "aerolink":   { "api_key": "...",       "model": "..." },
    "ollama":     { "api_key": "",          "model": "llama3.2" }
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

## Credits

- **Created by:** Al Shahriar Sowan
- Vibe coded with GPT-5.5 + Claude Opus 4.8

## License

MIT — see [LICENSE](LICENSE).
