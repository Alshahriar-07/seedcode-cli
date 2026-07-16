# Seed Code

**Plant ideas. Grow code.**

Seed Code is a premium, terminal-based AI coding assistant with three
interchangeable backends — [OpenRouter](https://openrouter.ai),
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

- **Three AI providers, one CLI** — switch anytime with `/provider`;
  each provider remembers its own API key and model, so switching never
  loses anything:
  - **OpenRouter** — live catalogue of **free models only** (zero prompt
    and completion pricing). Nothing is hardcoded.
  - **AeroLink** — Anthropic-compatible gateway; models fetched dynamically.
  - **Ollama** — fully local and key-free; lists the models you have
    installed.
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

On first launch, pick a provider and model — the same flows as `/provider`
and `/model`. For OpenRouter, create a key at <https://openrouter.ai/keys>;
for AeroLink, use your dashboard at <https://aerolink.lat>; for Ollama, just
have `ollama serve` running.

API keys can also come from environment variables (these override the
stored keys):

```bash
export OPENROUTER_API_KEY="sk-or-..."
export AEROLINK_API_KEY="..."
seedcode
```

## Commands

| Command      | Description                                            |
| ------------ | ------------------------------------------------------ |
| `/help`      | Show available commands                                |
| `/provider`  | Switch the active provider (OpenRouter/AeroLink/Ollama)|
| `/apikey`    | Set or replace the API key for the active provider    |
| `/model`     | Browse the live model list and pick one                |
| `/config`    | Show configuration (all providers' keys and models)    |
| `/settings`  | Change a setting: `username`, `stream`, `ollama_host`, `max_tokens` |
| `/history`   | List saved conversation sessions                       |
| `/reset`     | Forget the current conversation                        |
| `/clear`     | Clear the screen                                       |
| `/about`     | About Seed Code                                        |
| `/version`   | Show the version                                       |
| `/exit`      | Quit                                                   |

## Configuration

Config lives at `~/.seedcode/config.json` (owner-only permissions where the
OS supports it). Each provider keeps its own entry, so nothing is shared or
overwritten:

```json
{
  "active_provider": "openrouter",
  "providers": {
    "openrouter": { "api_key": "sk-or-...", "model": "vendor/model:free" },
    "aerolink":   { "api_key": "...",       "model": "..." },
    "ollama":     { "api_key": "",          "model": "llama3.2" }
  },
  "ollama_host": "http://localhost:11434",
  "max_tokens": 1024
}
```

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
