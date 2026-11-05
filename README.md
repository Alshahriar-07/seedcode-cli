# Seed Code CLI

<div align="center">

![Release](https://img.shields.io/badge/release-v10.2.1-22C55E?style=flat-square)
![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey?style=flat-square)
![License](https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-informational?style=flat-square)

**Seed Code — Eagox Studio**

> ### Faster. Smaller. Smarter. Workspace-aware.
> *Plant ideas. Grow code.*

</div>

---

**Jump to:** [What you get](#what-you-get) · [Installation](#installation) ·
[First run](#first-run) · [Providers](#providers-and-models) ·
[How a task runs](#how-a-task-runs) · [Commands](#command-reference) ·
[Permissions](#permissions) · [Config](#configuration-and-local-data) ·
[Internet](#internet-access) · [Build](#building-from-source) ·
[Artifacts](#release-artifacts-v1021) · [Troubleshooting](#troubleshooting) ·
[Security](#security-model) · [License](#license)

---

Seed Code is a premium terminal-based **AI coding agent** that runs directly
inside your terminal. One unified agent — not a chat bot with a mode switch —
reads your project, plans the work, writes and edits real files, runs real
commands, reads their output, searches the web for documentation, fixes what
fails, and reports honestly on what it actually did.

`seedcode --version` → `Seed Code CLI 10.2.1`

---

## What you get

- **One unified agent.** There are no Chat/Agent/Code modes: every request is
  one agent turn. The engine decides from the request itself (conversation,
  research, coding) which tools it needs, so a question does not read your
  project and a coding task does not fetch the web unless it must.
- **Real tools, not descriptions.** The agent acts through a registry of 27
  tools with strict schemas: `read_file`, `write_file`, `create_file`,
  `edit_file`, `append_file`, `insert_in_file`, `delete_file`, `rename_file`,
  `move_file`, `copy_file`, `list_dir`/`list_directory`, `create_directory`,
  `delete_directory`, `project_index`, `find_files`/`search_files`,
  `search_text`/`search_content`, `run_command` (foreground or background),
  `run_tests`, `get_command_output`, `check_exit_code`, `inspect_process`,
  `git`, `web_search` and `web_fetch`. Every call performs the real operation
  and its real result is returned to the model.
- **A live, honest execution view.** While the agent works you see the actual
  operation and the actual tool results — never a fabricated progress bar:

  ```text
  You > read README.md

    ℹ Analyzing the task and inspecting the workspace...
    ⚙ Reading README.md
    ✓ Read README.md
    ⚙ Running python -m pytest -q
    ✓ Tests passed · Exit code: 0
    ⚙ Searching the web for 'python fastapi docs'
    ✓ Searched online for 'python fastapi docs'
    ⚙ Editing app.py
    ✓ Updated app.py — +12/-5 lines (lines 1–18)
      @@ -1,5 +1,6 @@
       import sys
      -print("old")
      +print("new")
  ```

  Activity lines carry no `AI >` prefix: they are indented under your turn,
  with a marker for the real state — `⚙` started, `✓` succeeded, `✗` failed,
  `ℹ` informational. User input keeps its `You >` label.
- **A real thinking indicator.** A small animated spinner sits beside the
  *current operation* with a live elapsed timer, e.g. `⠹ Reading README.md……
  · 12s`. The label is read from backend state (`state.operation`), so it says
  what the agent is doing, not just "Working".
- **A minimal thinking strip** for models that expose a reasoning channel
  (Ollama models with the `thinking` capability, and cloud providers that
  relay `reasoning` deltas). It is just a small dim `Thinking...` label plus
  the reasoning the model itself exposes, directly above the input — never
  invented, never mixed with the response, hidden entirely when there is
  nothing to show. Height is compact and bounded, auto-scroll follows the
  stream unless you scroll up manually, and completed reasoning is preserved
  after the turn (toggle with `Ctrl+B`). No model/provider metadata is shown;
  a provider that exposes no reasoning records the honest line
  `Reasoning stream unavailable for this model.`
- **Inline task progress.** The agent's TODO list renders in the conversation
  timeline as `Task Progress` (`○` pending, `●` in progress, `✓` done,
  `✗` failed, `↻` retrying), full width and with no reserved sidebar. It is
  driven only by real orchestrator task events — the current task is
  highlighted and progress is shown as `N / M completed`.
- **Model-aware execution.** Three execution profiles — `small`, `standard`
  and `advanced` — control prompt size, step and tool-call budgets, output
  caps, history compaction and the tool protocol. Auto-detection uses local
  model metadata; `prompt_profile` and a per-model `model_profiles` map let
  you override it. A small local model gets short sequential instructions and
  the explicit `TOOL_CALL:` protocol; a strong model keeps the full context.
- **Responses are continued, not truncated.** If a provider stops at the
  output-token limit, SeedCode preserves what was written, continues from that
  point and returns the whole answer. A completion claim after a failed tool
  is sent back for one verification step instead of being taken at face value.
- **Safe recovery.** Transient failures retry with bounded jittered backoff,
  then try the next configured fallback model, then another provider. A
  local Ollama session is never silently moved to a cloud provider: that
  requires the explicit `allow_local_to_cloud` opt-in.
- **Real file operations and diffs.** File reads, creations, updates and
  deletions are reported with the path, the line counts, the affected line
  range and a bounded unified-diff preview; `Ctrl+E` expands the last diff.
- **Real terminal execution.** Commands show the command, the working
  directory, live output, the exit code and the duration; `Ctrl+T` expands the
  last command's full output. Windows PowerShell and POSIX shells both work.
- **Real internet research.** When a task needs current documentation the
  agent searches keyless and reads the pages it found; the search query, the
  source URL and the retrieved information are surfaced as real events.
- **A final report that matches reality.** When the turn ends you get a
  report of the files created/modified/deleted, commands run, tests run and
  their results — built only from operations that really happened.
- **Permissions that are asked, not assumed.** Dangerous actions (shell
  commands, deletes, git mutations, writes outside the workspace) ask first:
  Allow once / Always (session) / Allow All (session) / Deny. Grants live for
  the session only and are never written to disk.
- **Providers and models.** Default (built in, no key), OpenRouter, FreeModel
  Claude, FreeModel Codex, AeroLink and **Ollama** (started automatically when
  you select it). Custom OpenAI-compatible providers can be added without
  limit, each with its own name, base URL, key and model. Selecting a provider
  never copies another provider's key or model, and a failed request is
  reported rather than silently rerouted (automatic failover is opt-in).
- **Compact, responsive header.** A two-row bar — branding, provider, model and
  mode on the first row; live status, workspace, context budget and the masked
  key on the second — that never clips at any terminal width.

---

## Installation

After any installation method, `seedcode` is the primary command:

```bash
seedcode
```

### pip (any platform)

```bash
pip install seedcode-cli
```

Requires **Python 3.10 or newer**. The wheel exposes the `seedcode` console
script; `python -m seedcode` also works as a developer fallback.

### Windows — remote installer (recommended)

PowerShell 5.1 or newer:

```powershell
irm https://seedcode-cli.vercel.app/install.ps1 | iex
```

Installs for the current user (no administrator rights, no Python) into
`%LOCALAPPDATA%\Programs\SeedCode`, adds `seedcode` to your user `PATH`,
creates Desktop and Start Menu shortcuts (`seedcode --install-shortcuts`
recreates them at any time; `-NoShortcuts` skips them), and
verifies the installed binary reports exactly the requested version. It
downloads the official `SeedCode-CLI-10.2.1-windows-x64.exe` and the release's
`SHA256SUMS.txt`, and **refuses to install** if the checksum entry is missing,
the checksum file is unreachable, or the digest does not match.

### Windows — Setup installer

Download `SeedCode-CLI-Setup-10.2.1.exe` from the
[Releases page](https://github.com/Alshahriar-07/seedcode-cli/releases/tag/v10.2.1)
and run it. The wizard installs Seed Code, adds it to `PATH`, creates a Start
Menu shortcut, creates a Desktop shortcut (checked by default), verifies that
the installed `seedcode.exe` reports the installer's own version, and ships an
uninstaller that never deletes your project data silently.

### Windows — portable executable

Download `SeedCode-CLI-10.2.1-windows-x64.exe` and run it directly. It is fully
self-contained (no Python, no installation, no `PATH` changes).

### Linux / macOS — remote installer

```bash
curl -fsSL https://seedcode-cli.vercel.app/install.sh | bash
```

Installs for the current user and verifies the downloaded artifact against the
release's `SHA256SUMS.txt` before anything is installed. This release publishes
no prebuilt Linux/macOS binary, so the installer uses the official Python wheel
(requires **Python 3.10+ with pip**, or pipx when available).

### From source (development)

```bash
git clone https://github.com/Alshahriar-07/seedcode-cli.git
cd seedcode-cli
python -m pip install -e ".[dev]"
seedcode
```

After installing by any route:

```bash
seedcode --version    # -> Seed Code CLI 10.2.1
```

> **`seedcode` not recognized?** Open a *new* terminal. `PATH` changes only
> apply to fresh sessions; the installers verify this before they finish.

---

## First run

On first launch Seed Code asks where it may work:

```text
  Select Workspace
  --------------------------------------------------------------
  Where should Seed Code work? It reads, writes, indexes and runs
  commands only inside the folder you pick.

    1  Current Folder    D:\Projects\my-site
    2  Choose a Folder   browse this PC and pick any directory

  Select [1] >
```

`Current Folder` keeps the directory you launched from; `Choose a Folder` opens
your operating system's native folder picker. The chosen directory becomes the
**workspace** — the single root for file reads and writes, project indexing,
`.seedcode` project memory, terminal commands and verification. Nothing outside
it is touched without permission.

Automation is never blocked: `SEEDCODE_WORKSPACE=<dir>` selects the workspace
directly, and `SEEDCODE_NO_WORKSPACE_PROMPT=1` (or a non-interactive host)
keeps the launch directory.

You then land in the persistent interface:

```text
Seed Code CLI v10.2.1 · Ollama · qwen3:4b · Agent
● Ready · Workspace D:\Projects\demo · Context 16,384 tokens

You > read README.md

  ℹ Analyzing the task and inspecting the workspace...
  ⚙ Reading README.md
  ✓ Read README.md
  ⠹ Inspecting project structure...  · 4s

╭─ Message ──────────────────────────────────────────────────────────────────╮
│ › read README.md                                                           │
╰────────────────────────────────────────────────────────────────────────────╯
  Enter ↵ send  ·  Shift+Enter newline  ·  / commands  ·  Ctrl+C cancel
```

The header is fixed: it never scrolls away and every value updates in place the
moment it changes. Only the conversation scrolls; the composer stays at the
bottom.

### Composer and keys

The composer is a real bounded multiline editor with no prefix of its own — the
message you type is exactly the message that is sent.

| Key | Action |
| --- | --- |
| `Enter` | Send the message |
| `Shift+Enter` (or `Ctrl+J` / `Alt+Enter`) | Insert a newline without sending |
| `↑` / `↓` | Move inside a multiline message; walk history at the edges |
| `/` at the start of a line | Slash-command autocomplete (filter, arrows, Enter, Esc, mouse) |
| `Ctrl+C` | Cancel the running turn (or clear the composer when idle) |
| `Esc` | Dismiss suggestions / deny a pending permission prompt |
| `Ctrl+E` | Expand the last file diff |
| `Ctrl+T` | Expand the last command's output |
| `PageUp` / `PageDown`, `Ctrl+Home` / `Ctrl+End` | Scroll the conversation |
| Mouse wheel | Scroll the conversation |
| `Ctrl+D` | Exit |

Set `SEEDCODE_NO_TUI=1` (or `SEEDCODE_PLAIN=1`) to use the sequential console
instead — piped hosts do this automatically.

---

## Providers and models

| Provider | Key required | Notes |
| --- | --- | --- |
| **Default** | No | Seed Code's built-in connection; ships pointed at a free coding model. Also reads `OPENROUTER_API_KEY` / `SEEDCODE_DEFAULT_API_KEY` from the environment. |
| **OpenRouter** | Yes | Bring your own OpenRouter key. |
| **FreeModel Claude** | Yes | FreeModel backend, Claude models. |
| **FreeModel Codex** | Yes | FreeModel backend, Codex models. |
| **AeroLink** | Yes | AeroLink backend. |
| **Ollama** | No | Local models. Selecting Ollama starts `ollama serve` automatically when it is not already running. |
| **Custom** | Depends | Any OpenAI-compatible endpoint: name, base URL, key, model. |

Every provider keeps its **own** key and model: switching providers never copies
one provider's credentials or model onto another.

### Ollama

1. Install Ollama from <https://ollama.com> and pull a model, e.g.
   `ollama pull qwen3:4b`.
2. In Seed Code run `/provider` and choose **Ollama**, then `/model` and pick
   the model you pulled.

Models that expose a thinking channel (Seed Code reads the model's
`capabilities` from `GET /api/show`) also stream that reasoning into the
`Thinking...` strip; the request enables `think` only for models that report
the capability, and a model without one records the honest
"Reasoning stream unavailable" note instead of inventing reasoning.

If the server is not running, Seed Code starts `ollama serve` for you and waits
for it to become reachable. The default host is `http://localhost:11434`. A
request that fails is reported as a failure — Seed Code never silently switches
to a cloud provider when you selected Ollama (automatic failover is off unless
you enable `auto_failover`, and local→cloud failover additionally requires
`allow_local_to_cloud: true`).

### Model-aware execution profiles

| Profile | Used for | Behaviour |
| --- | --- | --- |
| `small` | Small local models | Short sequential prompt, `TOOL_CALL:` protocol, ≤3 calls/step, bounded output, aggressive compaction. |
| `standard` | General coding models | Native tools when available, normal budgets. |
| `advanced` | Strong coding models | Full context, more steps, native orchestration. |

`prompt_profile` pins a profile (`auto`, `small`, `standard`, `advanced`);
`model_profiles` pins individual models (`{"qwen3:4b": "small"}`). With
`auto`, a local provider model whose tag declares a small parameter size
(e.g. `:4b`) gets the compact profile; everything else is `standard`.

---

## How a task runs

1. **Your request** is classified by a deterministic, model-free classifier
   (`seedcode/core/intent.py`): conversation, research or coding.
2. **The model-aware profile is resolved** from your model metadata and the
   optional `prompt_profile` / `model_profiles` configuration, setting the
   prompt style, step/call budgets, output cap and preferred tool protocol.
3. **The engine selects tools** for that intent — filesystem, terminal, git and
   search for coding; the web tools for research. Nothing else is offered.
4. **Every tool call performs a real operation.** A read reads, a write writes,
   a command runs in the workspace shell; the result (including its error) is
   returned to the model, which continues from the real output. Names,
   required arguments and types are validated before execution; recognized
   call JSON is never shown as assistant text.
5. **The loop continues** until the task is complete or genuinely blocked —
   bounded so it can never spin forever, streamed so the UI never freezes,
   continued automatically after an output-token stop, and cancellable at any
   moment with `Ctrl+C`.
6. **A report is produced** from the operations that actually happened: files
   created/modified/deleted, commands executed with exit codes, tests run and
   their results, and anything still incomplete.

### Project memory and sessions

An active workspace gets a `.seedcode/` directory with a project index, memory
notes and the session checkpoint. A long task is checkpointed, so `/pause`
followed by `/resume` continues from the current task instead of starting over.
`/session` shows the live session's plan and per-task records; `/memory` shows
the project memory.

---

## Command reference

Every command is real and reachable; typing `/` at the start of a line shows
them with descriptions and filters as you type.

| Command | Purpose |
| --- | --- |
| `/help` | Show available commands |
| `/status` | Show the live runtime status |
| `/provider` | Select the active provider; `/provider manage` opens the provider screen |
| `/provider test` / `/provider failover on|off` | Test the active connection / enable automatic provider failover |
| `/model` | Browse and select a model for the active provider |
| `/models` | Manage models: custom ids, fallback models, and execution profiles |
| `/custom` | Manage custom providers (add, edit, test, reorder, enable, delete) |
| `/apikey` | View, replace, remove, or validate the active provider's key |
| `/workspace` | Show the active project, or select a different folder |
| `/permission` | Show or set the tool permission level (alias `/permissions`) |
| `/session` | Show agent session state and per-task records |
| `/pause` / `/resume` / `/stop` | Pause, resume or stop the running agent session |
| `/memory` | Show the `.seedcode` project memory and recent session notes |
| `/index` | Show a compact tree of the current project |
| `/files` | Search project files (`Ctrl+P`) |
| `/tools` | List the tools available to the coding agent |
| `/agents` | List agent profiles, or switch (`/agents build|plan|explore|general`) |
| `/history` | Browse the active provider's saved sessions |
| `/doctor` | Diagnose configuration, network and provider health |
| `/settings` | Open interactive settings (or `/settings <name> <value>`) |
| `/theme` | Pick a colour theme |
| `/palette` | Open the command palette (`Ctrl+K`) |
| `/shortcuts` | Show keyboard shortcuts |
| `/reset` | Forget the current conversation context |
| `/clear` | Clear the screen |
| `/config` | Show the current configuration |
| `/about` / `/version` | About / version |
| `/exit` | Leave the session (back to the main menu) |

### Model and provider management

`/models` is the model management screen: switch from the provider's live
catalogue, enter a custom model id, maintain the ordered `fallback_models`
list, clear the active model, and choose an execution profile (or pin one to
the active model). `/provider manage` is the provider screen: select a
provider, run a real connection test, see each provider's status/model/key
state, toggle automatic failover, and manage custom providers or the active
key. `/provider failover on` enables failover for the running session and is
persisted; the default remains off. Local sessions never fail over to a cloud
provider unless `allow_local_to_cloud` is explicitly enabled.

Aliases: `quit`, `q`, `menu` → `/exit`; `keys` → `/shortcuts`; `commands` →
`/palette`; `search` → `/files`; `providers`/`custom-providers` → `/custom`;
`key` → `/apikey`; `project`/`folder`/`ws` → `/workspace`; `perm` →
`/permission`.

---

## Permissions

Seed Code uses a single hierarchical permission level:

| Level | May do |
| --- | --- |
| `read_only` | Inspect only: no writes, no commands, no git mutations. |
| `workspace` | Read anywhere, mutate only inside the workspace (the default). |
| `full_system` | No path restriction on mutations (still confirmed per action). |

On top of the level, *dangerous* actions ask for confirmation: shell commands,
file deletes, git mutations and writes outside the workspace. Answers are
`Allow once`, `Always` (this category, this session), `Allow All` (everything,
this session) or `Deny`. Nothing is persisted; a denial is reported to the
model as a real tool failure, not hidden.

---

## Agent profiles

Seed Code runs one unified agent engine; a *profile* is a focused persona
capability preset over it — not a mode switch. A profile appends its persona
instructions, restricts which tools its turns may call (really enforced: an
out-of-whitelist call is refused before it runs), and may cap the per-turn
step budget.

| Profile | What it is |
| --- | --- |
| `build` | Default, the normal coding-agent behaviour (no restriction). |
| `plan` | Read-only strategist: investigates, then returns an implementation plan. Inspection tools only. |
| `explore` | Read-only investigator: maps a codebase, locates features/bugs, cites paths and lines. Inspection tools only. |
| `general` | Full tool access for work beyond one project's code. |

List and switch with `/agents` (e.g. `/agents plan`; the default is
`/agents build`).

### Custom agents

Define project-specialised agents in `.seedcode/agents.json` (or user-level
`agents.json` next to your SeedCode config): a same-id definition overrides
the built-in, and any new id becomes another `/agents` profile.

```json
{
  "reviewer": {
    "name": "Reviewer",
    "description": "Audits recent changes for correctness and style",
    "system": "Review code changes in the workspace. Use git diff and reads only; never modify files. Report findings with file:line references.",
    "tools": ["read_file", "search_text", "git", "list_dir"],
    "steps": 10
  }
}
```

Accepted keys: `name`, `description`, `system` (required), `tools`
(optional whitelist), `groups` (optional tool-group override), `steps`
(positive integer per-turn budget), `disabled` (`true` skips the entry).
Invalid definitions are skipped with a logged reason — they never break the
session.

### Plugins: custom tools and hooks

Python files in `<workspace>/.seedcode/plugins/` and `plugins/` next to your
SeedCode config are imported at agent startup. A plugin can:

* register a **custom tool** with the same `@register` decorator the built-in
  tools use — it then appears to the agent like any other tool (`/tools`,
  schemas, permissions included);
* add **hooks**: return `False` from a `before_tool` hook to veto a tool call
  (the veto reason is fed back to the model as a real failure), and observe
  every completed call through `after_tool`.

```python
# .seedcode/plugins/my_plugin.py
from seedcode.tools import ToolResult, register
from seedcode.tools.hooks import MANAGER


@register(
    "team_note",
    "Append an entry to TEAM_NOTES.md",
    {"text": "note to append"},
    mutates=True,
)
def team_note(permissions, args):
    path = permissions.workspace / "TEAM_NOTES.md"
    permissions.check_write(path)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(args["text"] + "\n")
    return ToolResult(True, f"appended to {path.name}")


def block_asset_writes(tool: str, args: dict) -> bool:
    path = str(args.get("path") or "").replace("\\", "/")
    return not (tool.startswith("write") and path.startswith("assets/"))


MANAGER.add_before(block_asset_writes)  # return False = veto the call
```

One broken plugin is skipped with a logged traceback; the others still load.
Plugins run with the session's own permission level — the permission system,
not the loader, is the gate on what plugins may do.

Set the level with `/permission workspace` (or `read_only`, `full_system`).

---

## Internet access

Research requests can search and read current sources keylessly: a DuckDuckGo
HTML search, page fetching with boilerplate stripped, and a
character-budgeted, URL-attributed context block. Every failure degrades to
"no results" instead of crashing the turn. Set
`SEEDCODE_DISABLE_INTERNET=1` to turn it off for a session, and
`SEEDCODE_MAX_RETRIES` to bound provider retries.

---

## Configuration and local data

```text
~/.seedcode/
├── config.json       provider and application settings
├── history/          saved conversation sessions (per provider)
├── memory/           persistent local memory
└── logs/             rotating diagnostic logs
```

`config.json` keeps one isolated entry per provider. Upgrades preserve it: your
settings, keys and history live outside the install directory and are never
deleted by an installer. `default_api`/`OPENROUTER_API_KEY` environment
variables are honoured for the Default provider.

Execution settings (all optional): `prompt_profile` (`auto` by default),
`model_profiles`, `max_agent_steps`, `max_tool_calls_per_step`,
`continue_truncated` (`true`), `fallback_models` and `allow_local_to_cloud`
(`false`).

Environment variables: `SEEDCODE_WORKSPACE`, `SEEDCODE_NO_WORKSPACE_PROMPT`,
`SEEDCODE_NO_TUI`, `SEEDCODE_PLAIN`, `SEEDCODE_PROVIDER`, `SEEDCODE_MODEL`,
`SEEDCODE_SHELL`, `SEEDCODE_DISABLE_INTERNET`, `SEEDCODE_MAX_RETRIES`,
`SEEDCODE_DIRNAME`, `SEEDCODE_DEBUG`, `SEEDCODE_DISABLE_DOTENV`,
`SEEDCODE_DOTENV`, `SEEDCODE_DEFAULT_API_KEY`, `OPENROUTER_API_KEY`.

---

## Building from source

### Python distributions

```bash
python -m pip install build twine
python -m build                     # dist/seedcode_cli-10.2.1-*.whl and *.tar.gz
python -m twine check dist/*
```

### Windows executable + installer

```bat
scripts\windows\build.bat
```

The pipeline runs the test suite as a release gate, builds the standalone
`dist\seedcode.exe` with PyInstaller (icon and version resource embedded,
`--version` verified against the source version), compiles the Inno Setup
installer, and stages the release directory with real SHA-256 checksums.
Requirements: Python 3.10+, PyInstaller, Inno Setup 6 or 7.

---

## Release artifacts (v10.2.1)

Release: [v10.2.1](https://github.com/Alshahriar-07/seedcode-cli/releases/tag/v10.2.1)

| File | What it is |
| --- | --- |
| `SeedCode-CLI-Setup-10.2.1.exe` | Windows Setup installer (Inno Setup; uninstaller, Start Menu entry). |
| `SeedCode-CLI-10.2.1-windows-x64.exe` | Standalone Windows executable — downloaded by the Windows IRM installer. |
| `seedcode_cli-10.2.1-py3-none-any.whl` | Python wheel — downloaded by the Linux IRM installer. |
| `seedcode_cli-10.2.1.tar.gz` | Python source distribution. |
| `seedcode-cli-10.2.1.tgz` | npm package tarball (launcher that downloads the official binary). |
| `SHA256SUMS.txt` | SHA-256 of every artifact above. |

Verify a download before using it:

```bash
sha256sum -c SHA256SUMS.txt              # Linux / macOS
Get-FileHash <file> -Algorithm SHA256    # Windows PowerShell
```

All release artifacts are staged in `dist/release/v10.2.1/` by the build pipeline;
digests are computed from the final files, never copied from an older release.
This source change does not build, publish or upload any release artifact.

---

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `seedcode` not found after install | Open a new terminal (`PATH` applies to new sessions). |
| "No model selected" | Run `/model` (and `/provider` first if needed). |
| Ollama errors | Make sure Ollama is installed; Seed Code starts `ollama serve` automatically. `ollama list` shows pulled models. |
| Provider needs a key | `/apikey` for the active provider, or `/provider` to switch. |
| A command was blocked | Approve it at the permission prompt, or set `/permission full_system` for path-unrestricted writes. |
| Diagnostics | `/doctor` reports configuration, connection and provider health. |
| Corporate proxy / offline | Set `SEEDCODE_DISABLE_INTERNET=1`; cloud providers still need network access. |

Logs live in `~/.seedcode/logs/`.

---

## Security model

- API keys are stored in your user configuration directory, never in the repo,
  never in shell history, and are always masked in the UI (`sk-or-v1...e031`).
- The workspace boundary is enforced by the permission manager, not by prompt
  wording: tools ask before any privileged operation.
- Release installers verify SHA-256 against the published `SHA256SUMS.txt` and
  abort on a missing entry or a mismatch; verification is never bypassed.

---

## Credits

Built by **Al Shahriar Sowan** (Eagox Studio).

## License

Seed Code is licensed under the **PolyForm Noncommercial License 1.0.0** —
see [LICENSE](LICENSE) for the full terms.

In short:

| | |
| :---: | --- |
| ✓ | Personal, educational, research and hobby use — free. |
| ✓ | Modification and distribution with these terms included — allowed. |
| ✗ | Commercial use — not allowed without a separate license. |

Copyright © 2026 **Al Shahriar Sowan** · Eagox Studio.
Commercial licensing or questions about the terms → open an issue or reach the
author via <https://github.com/Alshahriar-07>.
