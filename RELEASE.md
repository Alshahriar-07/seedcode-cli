# Seed Code CLI v9.3.2 — Release Notes

**Version:** 9.3.2 · **Tag:** `v9.3.2` · **Release date:** 2026-11-03
**Release page:** https://github.com/Alshahriar-07/seedcode-cli/releases/tag/v9.3.2

v9.3.2 is a reliability and interface-polish release on top of the v9.3.x agent
architecture: real workspace execution is now enforced by evidence, the
terminal interface is quieter, and a timestamp that appeared beside AI
responses is gone.

## What's new in v9.3.2

- **Minimal thinking display.** The bottom strip is now one small dim
  `Thinking...` label followed by the reasoning summaries the connected model
  or provider actually exposes. The former full-width "LIVE AI BRIEFING"
  dashboard — heading, model/provider identity, status fields, LIVE badges,
  metadata — was removed from the terminal. The strip:
  - hides itself entirely when there is no reasoning to show;
  - keeps a compact bounded height and never squeezes a short terminal;
  - follows the newest update automatically, while a manual scroll-up keeps
    the reader's position;
  - preserves completed reasoning after the turn (`Ctrl+B` toggles it);
  - records the honest line `Reasoning stream unavailable for this model.`
    when the provider exposes nothing — reasoning is never fabricated.
  The execution-status object still exists as internal backend state (phase,
  task, activity, retry, error) for logs and debugging; it is simply no longer
  rendered into the conversation.
- **No timestamp beside AI responses.** The activity log no longer prefixes
  each line with a bracketed `[HH:MM:SS]` wall-clock stamp. That prefix,
  printed between streamed assistant output during a turn, is what appeared as
  a timestamp next to AI responses. Timestamps remain in the backend
  `ActivityEvent` record for sorting, logs and session management; activity
  lines keep their real state markers and messages.
- **Guaranteed real workspace execution.** A task that must modify files can
  no longer complete on a printed claim. Completion is gated on observed
  evidence — the target file really created/changed, or the command really
  run — and the session re-instructs the agent with the unmet criteria when
  only text was produced. Read-only tasks (research, explanation, review)
  verify from real succeeded-inspection evidence instead, so the gate
  understands intent rather than forcing writes.
- **Explicit execution rules in the system prompts.** Code belongs in files; a
  printed tool call is not an executed tool call; chat output is not file
  output; verify before reporting; never claim success that did not happen.
  These rules are in both the native JSON-tool and text-protocol preambles.
- **File tools hardened.**
  - `read_file` refuses probable binaries (NUL probe) with actionable
    guidance instead of feeding mojibake into the model; line ranges remain
    available for large files.
  - All writes (`write_file`, `edit_file`, `insert_in_file`, `append_file`)
    are atomic — temp file → flush → fsync → replace — so an interruption
    cannot leave a truncated file, and a probable binary target is refused
    rather than overwritten with text.
  - `edit_file` gained an explicit `replace_all` for several independent
    occurrences; the default stays exactly-one with ambiguous targets
    rejected, and unrelated content is preserved.
  - `search_text` gained `ignore_case` and `exclude`; path globs are
    path-aware (`src/*.py` is one level, `src/**/*.py` recurses — `fnmatch`
    no longer lets `*` cross separators); the workspace walker honors
    `.gitignore`.
- **TODO / task orchestration improvements carried in this release line.**
  Hierarchical plans keep their nesting, dependencies schedule
  dependency-first, repeated failures cannot be marked complete, and the
  right-side panel always reflects real backend task state.

## Carried over from v9.3.1 / v9.2.x

- **One unified agent** with a deterministic intent classifier: every request
  is one agent turn (no Chat/Agent/Assist/Code modes).
- **Clean activity log** with real markers (`⚙` started, `✓` succeeded,
  `✗` failed, `ℹ` informational) and a spinner that shows the *current*
  operation with a live elapsed timer.
- **Real terminal and file reporting**: exit codes, command output, file
  diffs, one operation per line.
- **Right-side task plan panel** driven only by orchestrator task events.
- **Full tool surface** including `create_file`, `copy_file`, `run_tests`,
  `get_command_output`, `check_exit_code`, `inspect_process`, and the
  contract aliases `list_directory` / `search_files` / `search_content`.
- **Ollama, OpenRouter, FreeModel, AeroLink, default and custom
  OpenAI-compatible providers**, each with its own key and model.

## Backend changes

- `verify_task` takes a read-only intent switch; the session classifies a task
  as read-only only when it has no modify verbs and at least one research
  verb. Mutating intent is the default — enforcement stays strict for
  anything ambiguous.
- A file-modification task whose evidence contains only reads is rejected
  with an explicit unmet criterion; the claim alone can never complete it.
- File edits and writes are atomic and fsynced; a locked target fails with an
  actionable error and leaves no partial file or temp residue behind.
- The search walker applies a conservative `.gitignore` subset (names, paths,
  directory rules; negations skipped) to reduce noise without hiding source.
- Version 9.3.2 across package metadata, npm launcher, IRM installers, Setup
  script and docs.

## UI changes

- Bottom strip: `Thinking...` + dim reasoning, directly above the composer.
  No heading, no model/provider metadata, no LIVE badge, no status fields.
- Activity lines no longer carry a bracketed wall-clock prefix.
- The right-side TODO panel, the left chat/activity pane, the composer and the
  hint row are unchanged; resizing still re-fits every region without
  clipping.

## Provider changes

- Thinking streams remain provider-specific: Ollama's `thinking` capability
  (probed via `GET /api/show`) and OpenAI-compatible `reasoning` /
  `reasoning_content` / `reasoning_details` deltas surface as
  `ThinkingDelta` events. A failed request is reported as a failure;
  automatic failover stays opt-in (`auto_failover`, off by default).

## Bug fixes

- Bracketed `[HH:MM:SS]` timestamp appearing next to AI responses (activity
  log prefix removed; the response renderer itself never added one).
- Large "LIVE AI BRIEFING" dashboard with model/provider/status metadata
  replaced by the minimal thinking strip.
- Binary files decoded into model context and binary overwrites corrupting
  files (both refused).
- Non-atomic writes that could leave a truncated file when interrupted.
- `search_text` globs matching across path separators (`src/*.py` matching
  `src/gen/x.py`).
- File-modification tasks completing on a printed claim with no file
  operation.

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

Windows users can also download `SeedCode-CLI-Setup-9.3.2.exe` (Setup wizard) or
`SeedCode-CLI-9.3.2-windows-x64.exe` (portable) directly from the release page.

Verify afterwards:

```bash
seedcode --version    # -> Seed Code CLI 9.3.2
```

## Release artifacts

| File | Size (bytes) | Description |
| --- | ---: | --- |
| `seedcode_cli-9.3.2-py3-none-any.whl` | see `SHA256SUMS.txt` | Python wheel (any OS) |
| `seedcode_cli-9.3.2.tar.gz` | see `SHA256SUMS.txt` | Python source distribution |
| `SeedCode-CLI-9.3.2-windows-x64.exe` | see `SHA256SUMS.txt` | Standalone Windows x64 executable |
| `seedcode-cli-9.3.2.tgz` | see `SHA256SUMS.txt` | npm package tarball (launcher) |
| `SeedCode-CLI-Setup-9.3.2.exe` | see `SHA256SUMS.txt` | Windows Setup installer (Inno Setup) |
| `SHA256SUMS.txt` | — | SHA-256 of all five artifacts above |

## SHA-256 verification

```bash
cd dist/release/v9.3.2
sha256sum -c SHA256SUMS.txt

# Windows PowerShell
Get-FileHash .\SeedCode-CLI-9.3.2-windows-x64.exe -Algorithm SHA256
```

Both remote installers download `SHA256SUMS.txt` and abort on a missing entry,
an unreachable checksum file, or a mismatch. Verification is never bypassed.

## Known limitations

- **Cloud providers were not live-tested in this environment.** No valid cloud
  keys were available; their request/response paths are covered by the mocked
  provider suite.
- **No prebuilt Linux/macOS binary** is published in this release; the shell
  installer uses the verified wheel.
- **The npm tarball is a launcher**, not a self-contained runtime: it downloads
  the official binary from the release on first run.
- **The IRM installers were not executed against a published GitHub release**
  because publishing is not authorized; their logic is covered by the
  installer test suites.
- **The GitHub release has not been published by this build.** All artifacts
  are prepared and verified locally in `dist/release/v9.3.2/`; publishing is a
  separate, explicit step.
