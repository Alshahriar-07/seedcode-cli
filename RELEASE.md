# Seed Code CLI v10.1.0 — Release Notes

**Version:** 10.1.0 · **Tag:** `v10.1.0` · **Release date:** 2026-11-04
**Release page:** https://github.com/Alshahriar-07/seedcode-cli/releases/tag/v10.1.0

v10.1.0 is the deep OpenCode-style platform phase of the v9.3.x agent
architecture: agent profiles, a plugin system, tool-call state tracking,
delegated subagents, and a fuzzier-but-still-safe file editing tier.

## What's new in v10.1.0

- **Agent profiles and the `/agents` command.** A *profile* is a focused
  persona preset over the one unified agent engine: it appends its persona
  instructions, restricts which tools its turns may call (really enforced —
  an out-of-whitelist call is refused before it runs), and may cap the
  per-turn step budget. Built-in profiles: `build` (default, unrestricted),
  `plan` (read-only strategist), `explore` (read-only investigator) and
  `general` (full tool access). `/agents` lists them and switches between
  them (e.g. `/agents plan`).
- **Custom agents.** Project-specialised agents are defined in
  `.seedcode/agents.json` (or user-level `agents.json` next to the SeedCode
  config): a same-id definition overrides the built-in and any new id
  becomes another `/agents` profile. Accepted keys: `name`, `description`,
  `system` (required), `tools`, `groups`, `steps`, `disabled`. Invalid
  definitions are skipped with a logged reason — they never break the
  session.
- **Plugins: custom tools and hooks.** Python files in
  `<workspace>/.seedcode/plugins/` and `plugins/` next to the SeedCode
  config are imported at agent startup. A plugin may register a custom tool
  with the same `@register` decorator the built-ins use (it then appears
  like any other tool — `/tools`, schemas, permissions included), and may
  add hooks: `before_tool` returning `False` vetoes the call (the veto
  reason is fed back to the model as a real failure) and `after_tool`
  observes every completed call. One broken plugin is skipped with a logged
  traceback; the others still load. Plugins run under the session's own
  permission level — the permission system, not the loader, is the gate.
- **Tool-call state tracking.** New call tracker (`seedcode/core/calls.py`)
  records unique ids, real timing and statuses (`completed`, `failed`,
  `blocked`, `deduped`) and prevents duplicate provider call-ids, so the UI
  and reports reflect real tool-call state instead of guesses.
- **Subagent delegation.** A `subagent` tool spawns a fresh-context child
  AgentEngine with a depth guard (no runaway nesting), the session's shared
  permissions and isolated history/failures. The child does its own tool
  loop and returns only its result.
- **Per-agent model routing.** `AgentProfile` may carry a provider/model
  route parsed from `agents.json`; delegated children use it without
  touching the main session's routing.
- **Fuzzy edit tier for `edit_file`.** Behind the exact-match tier, an
  OpenCode-parity normalized match (quote/dash lookalikes, unicode spaces,
  whitespace-run collapse, per-line trailing whitespace) is attempted, with
  byte-exact replacement and uniqueness enforced in both tiers. Ambiguous
  targets are still rejected; unrelated content is preserved.
- **Deliverable documentation.** The deep-integration matrix, TUI report,
  agent-engine report and tool matrix were added under `docs/`.

## Carried over from v9.3.2 / v9.3.x

- One unified agent with a deterministic intent classifier (no modes).
- Minimal `Thinking...` strip; no timestamp beside AI responses.
- Guaranteed real workspace execution: file-modification tasks complete
  only on observed evidence.
- Atomic, binary-refusing file writes; binary-aware `read_file`; path-aware
  `search_text` globs and a `.gitignore`-honouring workspace walker.
- Clean activity log with real markers, live spinner, right-side task plan
  panel, full tool surface, and Ollama / OpenRouter / FreeModel / AeroLink /
  custom OpenAI-compatible providers, each with its own key and model.

## Backend changes

- `seedcode/core/agents.py`: AgentProfile registry, agents.json parsing,
  per-profile tool whitelist and step budget.
- `seedcode/tools/hooks.py`: before/after hook managers.
- `seedcode/tools/subagent.py`: fresh-context child engine with depth guard.
- `seedcode/core/calls.py`: call tracker (ids, timing, statuses, dedupe).
- `seedcode/tools/patch.py`: fuzzy edit tier behind the exact tier.
- `seedcode/core/agent.py`: profile-aware turn orchestration and tool
  enforcement; subagent plumbing.
- Version 10.1.0 across package metadata, npm launcher, IRM installers,
  Setup script and docs.

## Tool-level notes

- The fuzzy tier only relaxes *matching*; replacement bytes are taken from
  the actual text and both tiers require an unambiguous, unique target.
- Tool-hook vetoes surface as real tool failures with reasons, never as
  silent suppressions.
- Subagents never exceed the session permission level, even when they have
  their own model route.

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

Windows users can also download `SeedCode-CLI-Setup-10.1.0.exe` (Setup
wizard) or `SeedCode-CLI-10.1.0-windows-x64.exe` (portable) directly from
the release page.

Verify afterwards:

```bash
seedcode --version    # -> Seed Code CLI 10.1.0
```

## Release artifacts

Prepared and verified locally in `dist/release/v10.1.0/`.

| File | Size (bytes) | Description |
| --- | ---: | --- |
| `seedcode_cli-10.1.0-py3-none-any.whl` | see `SHA256SUMS.txt` | Python wheel (any OS) |
| `seedcode_cli-10.1.0.tar.gz` | see `SHA256SUMS.txt` | Python source distribution |
| `seedcode-cli-10.1.0.tgz` | see `SHA256SUMS.txt` | npm package tarball (launcher) |
| `SeedCode-CLI-10.1.0-windows-x64.exe` | see `SHA256SUMS.txt` | Standalone Windows x64 executable |
| `SeedCode-CLI-Setup-10.1.0.exe` | see `SHA256SUMS.txt` | Windows Setup installer (Inno Setup) |
| `SHA256SUMS.txt` | — | SHA-256 of all five artifacts above |

Filenames for this release use 10.1.0 only; no artifact reuses a previous
version number.

## SHA-256 verification

```bash
cd dist/release/v10.1.0
sha256sum -c SHA256SUMS.txt

# Windows PowerShell
Get-FileHash .\SeedCode-CLI-10.1.0-windows-x64.exe -Algorithm SHA256
```

Both remote installers download `SHA256SUMS.txt` and abort on a missing
entry, an unreachable checksum file, or a mismatch. Verification is never
bypassed.

## Known limitations

- **The automated test suite was not executed for this release packaging
  run** (per maintainer instruction); release-integrity checks only were
  performed. The feature work itself shipped with its tests in-tree.
- **Cloud providers were not live-tested in this environment.** No valid
  cloud keys were available; their request/response paths are covered by
  the mocked provider suite.
- **No prebuilt Linux/macOS binary** is published in this release; the
  shell installer uses the verified wheel.
- **The npm tarball is a launcher**, not a self-contained runtime: it
  downloads the official binary from the release on first run.
- **The IRM installers were not executed against a published GitHub
  release** because publishing is not authorized; their logic is covered
  by the installer test suites.
- **The GitHub release has not been published by this build.** All
  artifacts are prepared and verified locally in `dist/release/v10.1.0/`;
  publishing is a separate, explicit step.
