# Seed Code CLI v9.2.0 — Release Notes

**Version:** 9.2.0 · **Tag:** `v9.2.0` · **Release date:** 2026-11-01
**Release page:** https://github.com/Alshahriar-07/seedcode-cli/releases/tag/v9.2.0

v9.2.0 makes Seed Code a dedicated AI coding agent and turns the terminal into
a live, honest view of its work. The remaining mode machinery and the non-coding
desktop features are gone; what is left is one engine that reads the project,
plans, edits real files, runs real commands, verifies its own work and reports
what actually happened.

## Highlights

- **One unified agent.** Chat Mode, Agent Mode, Assist Mode and Code Mode were
  merged into a single engine. Every request is one agent turn; a deterministic,
  model-free classifier decides whether it needs project context, terminal
  access or the web. `/chat`, `/agent`, `/mode`, `/assist` and `/codemode` were
  removed.
- **Clean activity log.** Activity no longer repeats an `AI >` prefix. Lines are
  indented under the user's `You >` turn and marked with the real state: `⚙`
  started, `✓` succeeded, `✗` failed, `ℹ` informational.
- **A spinner that says what is happening.** The animated indicator displays
  the backend's current operation (`Reading README.md…`, `Waiting for the model
  response…`, `Running pytest…`) with a live elapsed timer instead of a generic
  "Working".
- **Real terminal and file reporting.** Commands report their real exit code;
  file changes report path, line counts, line range and a bounded unified diff;
  one operation produces one line (no duplicates).
- **Compact header.** The oversized boxed logo was replaced by a two-row bar
  that never clips at any width and never churns while the agent streams.
- **Non-coding features removed.** Desktop control, OCR/vision, the Computer
  Engine and the YouTube Music command are gone, along with the `desktop`,
  `vision` and `browser` extras.

## Backend changes

- The engine's permission manager is constructed with the selected workspace:
  the missing `active_workspace` import that crashed a normal message with
  `NameError: name 'active_workspace' is not defined` is fixed.
- Provider selection is honoured exactly. A failed request is reported as a
  failure; automatic failover is opt-in via `auto_failover` and is off by
  default, so a failed Ollama request never silently becomes a cloud request.
- A clean shutdown clears an in-flight initialization state.
- Terminal tools report the real exit code (parsed from the tool's own output)
  and the event stream carries it in the human-readable message.
- File changes are rendered once: the diff view owns the summary line, so an
  event is no longer printed twice.
- The build pipeline (the release gate) runs the full test suite and refuses to
  package a failing tree; it also cleans only build outputs so earlier release
  artifacts in `dist/release/` are preserved.

## UI changes

- The fixed header is a compact two-row bar: `Seed Code CLI v9.2.0 · Provider ·
  Model · Agent`, then `● Status · Workspace … · Context … tokens`.
- Activity lines are prefix-free and indented two columns; all five markers are
  used consistently.
- The thinking indicator is a braille spinner beside the current operation with
  an elapsed timer.
- The stop/cancel path (Ctrl+C) still ends the active turn; the interface stays
  responsive during streaming and tool execution; terminal resizing re-fits the
  regions without clipping.

## Provider changes

- Default (built in), OpenRouter, FreeModel Claude, FreeModel Codex, AeroLink and
  Ollama, plus unlimited custom OpenAI-compatible providers.
- Ollama is started automatically when selected and fails loudly when it cannot
  answer; its models are listed from the live server.
- Every provider keeps its own key and model; switching never copies one onto
  another.

## Bug fixes

- `NameError: name 'active_workspace' is not defined` when sending a message.
- The header showed a stale `Chat` mode after the unified-agent switch.
- Duplicate success lines for a single file change.
- Commands reported `Command succeeded` with no exit code.
- The activity log repeated an `AI >` prefix on every line.
- The spinner hid the current operation behind a generic label.
- `tui.shutdown()` left the initializing state set.
- The release build deleted `dist/release/` (previous artifacts) when cleaning.

## Installation

```powershell
# Windows (recommended, per-user, no admin, verifies SHA-256)
irm https://seedcode-cli.vercel.app/install.ps1 | iex
```

```bash
# Linux / macOS
curl -fsSL https://seedcode-cli.vercel.app/install.sh | bash

# Any OS with Python 3.10+
pip install seedcode-cli
```

Windows users can also download `SeedCode-CLI-Setup-9.2.0.exe` (Setup wizard) or
`SeedCode-CLI-9.2.0-windows-x64.exe` (portable) directly from the release page.

Verify afterwards:

```bash
seedcode --version    # -> Seed Code CLI 9.2.0
```

## Release artifacts

| File | Size (bytes) | Description |
| --- | ---: | --- |
| `seedcode_cli-9.2.0-py3-none-any.whl` | see `SHA256SUMS.txt` | Python wheel (any OS) |
| `seedcode_cli-9.2.0.tar.gz` | see `SHA256SUMS.txt` | Python source distribution |
| `SeedCode-CLI-9.2.0-windows-x64.exe` | see `SHA256SUMS.txt` | Standalone Windows x64 executable |
| `seedcode-cli-9.2.0.tgz` | see `SHA256SUMS.txt` | npm package tarball (launcher) |
| `SeedCode-CLI-Setup-9.2.0.exe` | see `SHA256SUMS.txt` | Windows Setup installer (Inno Setup) |
| `SHA256SUMS.txt` | — | SHA-256 of all five artifacts above |

## SHA-256 verification

```bash
cd dist/release/9.2.0
sha256sum -c SHA256SUMS.txt

# Windows PowerShell
Get-FileHash .\SeedCode-CLI-9.2.0-windows-x64.exe -Algorithm SHA256
```

Both remote installers download `SHA256SUMS.txt` and abort on a missing entry,
an unreachable checksum file, or a mismatch. Verification is never bypassed.

## Known limitations

- **Model latency is model-bound.** On a local `qwen3:4b` a single agent turn can
  take 30–300 s depending on the task and the machine; the interface stays live
  and every turn is cancellable.
- **Cloud providers were not live-tested in this environment.** No valid cloud
  keys were available; their request/response paths are covered by the existing
  provider tests, while Ollama was exercised end-to-end with a real model.
- **No prebuilt Linux/macOS binary** is published in this release; the shell
  installer uses the verified wheel.
- **The npm tarball is a launcher**, not a self-contained runtime: it downloads
  the official binary from the release on first run (and is therefore only as
  usable as the release it points at).
- **npm and winget are not advertised installation channels**; the supported
  channels are pip and the two remote installers.
- **Desktop control, OCR and browser automation were removed** in this release.
  Requests that would have needed them are answered without those tools.
- **The GitHub release has not been published by this build.** All artifacts are
  prepared and verified locally in `dist/release/9.2.0/`; publishing is a
  separate, explicit step.
