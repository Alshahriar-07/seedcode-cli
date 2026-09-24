# Seed Code CLI v8.1.0 — Release Notes

**Version:** 8.1.0 · **Tag:** `v8.1.0`

v8.1.0 is a **modes and task-lifecycle** release. Seed Code now exposes exactly
three modes — **Chat**, **Code** and **Agent** — resolved through a single
shared source of truth so no surface can disagree or invent a fourth, and the
entire release surface (package, installers, checksums, documentation) is
synchronised on 8.1.0. Completing a task never closes the application: the
session returns to a ready prompt and accepts the next request.

---

## What's new in 8.1.0

### Exactly three modes

| Mode | What it does |
| --- | --- |
| **Chat Mode** | Conversation: questions, explanations, brainstorming. Never acts on your project. |
| **Code Mode** | A real coding agent for the current workspace: inspect, plan, edit, run, verify, and keep working until the task is verified. |
| **Agent Mode** | General-purpose multi-step execution using the available tools, verified before it is reported complete. |

- A new single source of truth, `seedcode.core.modes`, owns the mode enum,
  labels, descriptions and parsing. `/mode`, `/chat`, `/codemode`, `/agent`,
  `/assist`, `/desktop`, the dashboard, the status bar, the interactive menu and
  the task header all resolve through it.
- **Assist Mode is retired as a separate mode**; its capabilities now belong to
  Agent Mode. `assist` and `desktop` remain accepted *input* aliases and route
  to Agent Mode, so an existing habit or stored value keeps working — but no
  fourth mode can be selected or displayed.
- The interactive main menu no longer offers a separate "Assist Mode" entry.

### Task lifecycle

- **A completed task does not close the application.** Every task path —
  success, failure, provider error, `Ctrl+C` — finishes the task view, prints a
  status and returns to a ready prompt. Only `/exit` leaves the chat loop.
- Code Mode keeps its evidence-based task graph: a task is only `COMPLETED`
  when its acceptance criteria are satisfied by observed evidence, and a step
  is never reported done unless the work behind it really happened.
- The live progress view is driven by the model's plan and by real tool
  events, so what is on screen is what actually ran.

### Provider persistence and default configuration

- Adding a custom provider no longer disturbs the built-in providers: an
  existing per-provider key and model survive the save, and both the default
  provider set and the custom provider remain after a configuration reload.
- A fresh configuration initialises cleanly: with no built-in credential in the
  build, the active provider is reported as a setup state (`Setup needed` /
  `No Key`) rather than a misleading missing-key error. No credential is ever
  hardcoded, and keys are stored per provider in `~/.seedcode/config.json`,
  masked or absent in every display, log and error message.

### Distribution

- **Version 8.1.0** is read from a single place (`seedcode.__version__`) and
  propagated to the wheel, sdist, CLI `--version`, the executable, the remote
  installers, `IRM_INSTALL/RELEASE_INFO.txt` and the release workflow.
- **`IRM_INSTALL/install.sh` rewritten** to the documented checksum contract:
  it prefers the platform standalone binary and falls back to the official
  wheel; either way it fetches, verifies against the release's
  `SHA256SUMS.txt`, then installs, and verifies the file it installed by
  absolute path. A missing entry, a mismatch, or an unreachable checksum file
  aborts — verification is never bypassed.
- The Windows installer's pinned-checksum map is reset for 8.1.0; digests are
  computed from the real published artifacts at release time and never
  invented.
- Added `.github/CODE_OF_CONDUCT.md` and `.github/SECURITY.md`.

---

## Release artifacts (v8.1.0)

| Artifact | Purpose |
| --- | --- |
| `SeedCode-CLI-Setup-8.1.0.exe` | Windows setup installer (Inno Setup wizard, uninstaller, Start Menu entry) |
| `SeedCode-CLI-8.1.0-windows-x64.exe` | Standalone Windows executable (portable; downloaded by the Windows IRM installer) |
| `seedcode_cli-8.1.0-py3-none-any.whl` | Python wheel (`pip install seedcode-cli`) |
| `seedcode_cli-8.1.0.tar.gz` | Python source distribution |
| `SHA256SUMS.txt` | SHA256 checksums covering every artifact above |

Both remote installers read `SHA256SUMS.txt` from the release named `v8.1.0`
and refuse to install anything that is not listed or that fails its digest.

## Verify a download

```bash
# Linux / macOS
sha256sum -c SHA256SUMS.txt

# Windows (PowerShell)
Get-FileHash .\SeedCode-CLI-8.1.0-windows-x64.exe -Algorithm SHA256
```

## Verification performed

The following was verified against this repository (offline, no network):

| Check | How | Result |
| --- | --- | --- |
| Three modes only | `seedcode.core.modes` label set; legacy aliases resolve to Chat/Code/Agent | Pass |
| Config migration | legacy `agent_mode` config loads and round-trips to `mode` | Pass |
| Code Mode end-to-end task | `tests/test_v710_e2e_todo_app.py` (real agent loop, real subprocess tests) | Pass |
| Agent Mode multi-step | `tests/test_task_flow.py` (both code-mode and non-code-mode paths) | Pass |
| Completion does not exit | task-flow tests assert `Ready for next task.` and a live session; no `sys.exit`/`os._exit` on the task path | Pass |
| Provider persistence | defaults + a custom provider survive save and reload | Pass |
| Installer checksum contract | `tests/test_installer_checksums.py` | Pass |
| Source audit — no hardcoded credentials / no telemetry | repository-wide search | Pass |

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

## Upgrade safety

Upgrades preserve user configuration and provider/API settings. Configuration
lives in the Seed Code config directory (`~/.seedcode/`), never inside the
install directory, and is never deleted by an installer.

---

Version **8.1.0**, tag **`v8.1.0`** —
<https://github.com/Alshahriar-07/seedcode-cli/releases/tag/v8.1.0>
