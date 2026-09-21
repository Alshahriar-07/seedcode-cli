# Changelog

All notable changes to Seed Code CLI are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project adheres to [Semantic Versioning](https://semver.org/).

## [6.2.5] — 2026-09-21

A UI, provider and distribution release: the startup screen lost its ASCII
logo, the built-in **Default** connection became a first-class provider
separate from OpenRouter, and installation moved to the official IRM
installer system.

### UI

- Removed the large ASCII logo from the CLI interface; `seedcode.ui.logo` no
  longer exists and nothing renders block or box art at startup.
- Simplified and compacted the startup screen to a borderless five-line
  header (`Seed Code CLI v6.2.5` plus Provider / Model / Mode / Status).
- The `API Key` row is rendered **only** for providers that actually require
  a key, so Default and Ollama never show one.
- `mode_label()` remains the single source of truth for the active mode, and
  the header, `/mode`, `/chat` and `/status` all name it identically.

### Providers

- Added **Default** — Seed Code's built-in API connection — as a first-class
  provider that needs no API key, and made it the provider a fresh install
  ships with (`defaults.DEFAULT_PROVIDER`).
- Default and OpenRouter are separate choices everywhere: separate `/provider`
  entry, config slot, model, connection status and credential.
- Provider-specific API-key rules are enforced by one shared helper: Default
  and Ollama require no key; OpenRouter, FreeModel Claude, FreeModel Codex and
  AeroLink each require their own. The provider picker groups choices by what
  they need and shows each provider's backend, model and key state.
- Provider configurations are now strictly isolated: the embedded release
  credential is no longer copied into OpenRouter's stored slot, and the
  previously hardcoded `freemodel_claude` migration fallbacks now resolve to
  the built-in Default provider.
- `/status` gained an `API Key` row and reads its backend label from the
  provider, so Ollama is "Local server" and Default is "Seed Code API".

### Connectivity

- Audited provider connections: streaming errors now name the provider the
  user selected rather than the internal backend, and a rejected built-in
  credential points at `/provider` instead of an API-key prompt that does not
  apply.
- Audited terminal control: execution streams output live, captures `stderr`
  with `stdout`, reports exit codes, bounds long-running commands, and kills
  the whole process tree on timeout or `Ctrl+C`. A command whose pipe closes
  while it keeps running can no longer block the UI loop.
- Code Mode, Agent Mode and Assist Mode are covered against every provider by
  a provider × mode matrix test; an unreachable catalogue or unknown provider
  produces a clear message instead of a crash.
- **Chat Mode commands**: `/chat` and `/chat on` return the session to plain
  conversation (no tools) from anywhere; `/chat` alone shows the active mode.
- **`/mode` command**: one generic switcher — `/mode chat|assist|code|agent` —
  alongside the existing direct commands (`/chat`, `/assist`, `/codemode`).
- **`/permissions` alias** for `/permission`.
- **`Retry-After` handling**: a genuine HTTP 429 reports the provider's
  requested wait, honored (capped) during the bounded retry. The retry count
  is configurable via `SEEDCODE_MAX_RETRIES` (clamped to 0–5; default 2).
- HTTP 429 is reported only for actual rate limiting: timeouts, DNS/connect
  failures, invalid keys, and unknown models keep their specific messages.
- **Environment selection overrides**: `SEEDCODE_PROVIDER` and
  `SEEDCODE_MODEL` (also readable from a project `.env`) preseed the
  first-run provider/model. A stored user selection is never overwritten.

### Distribution

- Added the official **IRM installer system** in `IRM_INSTALL/`:
  `install.ps1` (Windows, PowerShell 5.1+), `install.sh` (Linux, bash) and
  `RELEASE_INFO.txt`.
- Both installers download the official release artifact, verify its SHA256
  against the release's `SHA256SUMS.txt` before installing, install per-user,
  configure `PATH`, support an existing installation, and verify the result
  with `seedcode --version`. Neither depends on a cloned repository.
- Aligned the GitHub release workflow with the artifact names the installers
  download (`SeedCode-CLI-<version>-windows-x64.exe`,
  `SeedCode-CLI-Setup-<version>.exe`, the wheel/sdist and a `SHA256SUMS.txt`
  covering every attached asset), and made it fail loudly on a version
  mismatch.
- Removed stale v6.2.0 release artifacts, the old v6.2.0 WinGet manifests and
  the associated untracked build output from the repository.

### Documentation

- Rewrote `README.md`: the installation section now shows only the official
  IRM commands, and the first-run example, provider table, persistence layout
  and security model describe the v6.2.5 behaviour.
- Removed npm / pip / winget from the user-facing installation instructions;
  the release-notes body generated by the release workflow no longer
  advertises them either.
- Documented the Default provider's credential resolution and the
  provider-isolated `config.json` layout.

### Changed

- `seedcode --version` prints `Seed Code CLI 6.2.5` (machine-parseable for
  installers and scripts).
- The provider registry has six providers; `default` leads the `/provider`
  list as the zero-setup option.
- Provider display label for local Ollama is now `Ollama` (previously
  `Ollama (local)`), with the connection type shown separately.

## [6.2.0] — 2026-08-19

- Code Mode: workspace-aware agent sessions with `.seedcode` project memory,
  an incremental project index, and `/codemode on|off|status`.
- Unified permission system (read_only / workspace / desktop / full_system)
  with per-action confirmation for dangerous operations.
- Default API configuration layer: embedded release key resolution with
  user-config and environment precedence (key material never committed).
- Live terminal output streaming for run_command; process-tree cancellation.
- Dashboard redesigned around a compact 88-column grid with the pixel-art
  brand mark; removed the giant banner.

## [6.1.5] — 2026-08-17

- Universal Desktop Operator Stack: Computer Engine (mouse, keyboard,
  windows, browser, vision/OCR detection ladder) behind permission gates.

## [6.1.4] — 2026-08-17

- Packaging and installer maintenance release (Windows build pipeline,
  Inno Setup installer, WinGet manifests).
