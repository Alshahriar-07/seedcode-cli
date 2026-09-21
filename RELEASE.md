# Seed Code CLI v6.2.5 — Release Notes

**Version:** 6.2.5 · **Tag:** `v6.2.5`

v6.2.5 is a UI, provider and distribution release. The startup screen lost its
ASCII logo (and gained a compact, structured header), tasks became
step-by-step, the built-in **Default** connection became a first-class provider
separate from OpenRouter and now ships with `cohere/north-mini-code:free`, and
installation moved to the official IRM installer system.

This pass also includes a **distribution fix**: the Windows installer's
`SHA256SUMS.txt` parser reported "No SHA256 checksum for …" for artifacts the
release did list. Both installers now parse the checksum file
whitespace-agnostically (one space, several spaces, tabs, CRLF, the `*`
binary-mode marker) and match the file name exactly — while still refusing to
install anything whose digest is missing or wrong.

## Official installation

Windows:

```powershell
irm https://seedcode-cli.vercel.app/install.ps1 | iex
```

Linux:

```bash
curl -fsSL https://seedcode-cli.vercel.app/install.sh | bash
```

Both installers download the official release artifact, verify its SHA256
against the release's `SHA256SUMS.txt` before installing, install per-user,
configure `PATH`, and finish by running `seedcode --version`. Verification is
mandatory: a missing checksum entry, a mismatching digest, or a host with no
SHA256 tool makes the installer stop instead of installing unverified code.

## Release assets

Version **6.2.5**, tag **`v6.2.5`** —
<https://github.com/Alshahriar-07/seedcode-cli/releases/tag/v6.2.5>

| Asset | Kind |
| --- | --- |
| `SeedCode-CLI-6.2.5-windows-x64.exe` | Standalone Windows executable (portable; downloaded by the Windows IRM installer) |
| `SeedCode-CLI-Setup-6.2.5.exe` | Windows setup installer (Inno Setup wizard, uninstaller, Start Menu entry) |
| `seedcode_cli-6.2.5-py3-none-any.whl` | Python wheel (downloaded by the Linux IRM installer when no prebuilt binary is published for the platform) |
| `seedcode_cli-6.2.5.tar.gz` | Python source distribution |
| `SHA256SUMS.txt` | SHA256 digest for every attached asset; verified by both installers |

Artifact names are part of the release contract — the installers look each one
up by exact name in `SHA256SUMS.txt`, so they must not be renamed. Every
release must attach the full set above, including the wheel: `install.sh` has
no verifiable artifact to install on Linux/macOS without it.

## Highlights

### UI

- The large ASCII logo is gone: `seedcode.ui.logo` was removed and nothing
  renders block or box art at startup.
- The startup screen is the restored structured dashboard — a bordered
  reference panel with the branding cell, a divider and the live session
  state — with **one permanent change: the ASCII logo is gone.** The brand is
  plain text; nothing draws pixel or block art.

  ```text
  ╭─ Seed Code CLI v6.2.5 ───────────────────────────────────────────────────────────────────────╮
  │                                                                                              │
  │   Seed Code                                │ Seed Code  |  Eagox Studio                      │
  │   AI CODING AGENT                          │ Plant ideas. Grow code.                         │
  │                                            │ Provider   Default                              │
  │                                            │ Model      cohere/north-mini-code:free          │
  │                                            │ Mode       Chat  •  ● Ready                     │
  ╰──────────────────────────────────────────────────────────────────────────────────────────────╯
  ```

- The panel is wider and shorter: 96 columns at full width (the design
  target) so the whole `cohere/north-mini-code:free` model name fits without
  clipping, and one less blank row — a single blank row under the title, the
  live rows, then the border, with no padding rows to scroll past.
- The header is responsive: 96-column design width on wide terminals, an info
  section that slides left so the whole model value still fits (80 columns
  shows `cohere/north-mini-code:free` in full), a compact one-row panel under
  64 columns, plain lines under 40, and an all-ASCII rendering on consoles
  that cannot draw or encode the glyphs.
- Mode switches reprint the one-line session summary (`provider · model ·
  mode · status`) instead of the whole dashboard.
- The `API Key` row appears only for providers that require a key. Default and
  Ollama never show one, and no key is ever displayed in full.

### Task flow

- Code Mode, Assist Mode and Agent Mode show a compact live task flow
  (`analyze → inspect → plan → implement → test → verify`). The states
  `pending`, `running`, `completed`, `failed` and `skipped` are driven by real
  engine activity — a step the task did not perform reads *skipped*, and tests
  are only marked done when a test command actually ran and exited 0.
- A finished task prints a persistent summary (files changed, test result) and
  then `Ready for next task.` — success, failure and `Ctrl+C` all return to the
  prompt. Nothing in the task path can close the CLI.
- Live command output is echoed compactly (first lines, then one elision
  note); the model and the log file still receive the complete output.
- Plain Chat Mode keeps its fast, clean spinner — no task view.

### Default model

- The Default provider now ships with `cohere/north-mini-code:free`
  (`seedcode.defaults.DEFAULT_MODEL`), so a fresh install can work
  immediately without choosing a model or entering a key.
- The default is applied to Default's own config slot only; OpenRouter,
  FreeModel, AeroLink and Ollama keep their own models, and switching
  providers never copies one model onto another.

### Providers

- **Default** is a first-class provider: Seed Code's built-in connection, no
  user API key, and the provider a fresh install starts on. It is separate
  from OpenRouter in the picker, in `config.json`, in status, and in
  credential handling.
- Default resolves its own credential (own slot → embedded release credential →
  `OPENROUTER_API_KEY` / `SEEDCODE_DEFAULT_API_KEY`) and never reads or writes
  another provider's slot. The embedded credential is no longer copied into
  OpenRouter's stored configuration.
- Provider-specific API-key rules are enforced from one shared helper:
  Default and Ollama require no key; OpenRouter, FreeModel Claude, FreeModel
  Codex and AeroLink each require their own.
- The provider picker groups choices by what they need (*Built-in · no API key*,
  *Your own API key*, *Local*) and shows backend, current model and key state.
- `/status` gained an `API Key` row and reads its backend label from the
  provider.

### Connectivity

- Provider connection errors name the provider the user selected, not the
  internal backend; a rejected built-in credential directs the user to
  `/provider` rather than an inapplicable API-key prompt.
- Terminal execution streams output live, captures `stderr` with `stdout`,
  reports exit codes, bounds long-running commands, and kills the whole
  process tree on timeout or `Ctrl+C`. A command that closes its output pipe
  while still running can no longer block the UI loop.
- A provider × mode matrix test covers every provider in Chat, Assist and Code
  Mode, and asserts that an unreachable catalogue or unknown provider produces
  a clear message instead of a crash.

### Distribution

- `IRM_INSTALL/{install.ps1,install.sh,RELEASE_INFO.txt}` is the official
  remote-installer source. Neither script depends on a cloned repository.
- The GitHub release workflow now attaches exactly the artifact names the
  installers download, plus a `SHA256SUMS.txt` covering every attached asset,
  and fails loudly on a version mismatch.
- Stale v6.2.0 release artifacts, the old v6.2.0 WinGet manifests and untracked
  build output were removed from the repository, along with the now-unreferenced
  WinGet manifest builder and the unused duplicate PyPI workflow.

## Verification performed

- Full test suite: **773 tests passing** (`python -m pytest`), including new
  suites for the logo-free header (`tests/test_dashboard.py`), the task flow
  (`tests/test_task_flow.py`), installer checksum parsing
  (`tests/test_installer_checksums.py`), the provider × mode matrix
  (`tests/test_provider_matrix.py`), Default/OpenRouter isolation, and
  terminal streaming/`stderr`/exit-code behaviour.
- `seedcode --version` prints `Seed Code CLI 6.2.5`.
- `IRM_INSTALL/install.sh` passes `bash -n` and its argument validation paths
  were exercised (`--help`, unknown option, malformed version).
- `IRM_INSTALL/install.ps1` parses cleanly under the Windows PowerShell
  parser, and its checksum-matching, architecture detection and install-path
  resolution were exercised directly. Both scripts are ASCII-only so Windows
  PowerShell 5.1 cannot misread them as ANSI.
- Both checksum parsers were executed against the real release formats and
  adversarial ones (one space, two spaces, tabs, CRLF, surrounding
  whitespace, `*` binary-mode marker, uppercase digest, truncated/non-hex
  digests, comment headers, absent entry, prefix/suffix look-alikes). A
  correct digest verifies; a wrong digest, a missing entry and a host with no
  SHA256 tool all refuse with a non-zero exit.
- The startup dashboard and the task flow were rendered directly at 40, 64,
  80, 88, 96 and 120 columns and on a legacy (raster-font) console; no line
  overflows its terminal and the border never breaks.
- The installers' version constants are asserted equal to
  `seedcode.__version__`, so the release version cannot drift.
- Both parsers were also run against the **live** `v6.2.5
  SHA256SUMS.txt` fetched from GitHub Releases: each resolves
  `SeedCode-CLI-6.2.5-windows-x64.exe` → `75b1e486…` and
  `SeedCode-CLI-Setup-6.2.5.exe` → `ee92ba58…` — the exact digests the release
  publishes.
- `IRM_INSTALL/install.ps1` was executed end-to-end on Windows against the
  real `v6.2.5` release: it downloaded the 83.4 MB
  `SeedCode-CLI-6.2.5-windows-x64.exe`, reported `SHA256 verified
  (75b1e4869085...)`, installed it, and its own final check printed
  `seedcode --version  ->  Seed Code CLI 6.2.5`. (Run into a temporary
  install directory with `-NoPathUpdate`, then removed.)
- The Windows side of the original bug was reproduced against the **deployed**
  installer: on the same machine and the same release,
  `irm https://seedcode-cli.vercel.app/install.ps1 | iex` still fails with
  `No SHA256 checksum for SeedCode-CLI-6.2.5-windows-x64.exe`. Root cause:
  the old `Get-ExpectedHash` declared `[string] $SumsText`, so PowerShell 5.1
  coerced the `byte[]` that GitHub's octet-stream response produces into the
  literal text `System.Byte[]` and no line could ever match. The fixed parser
  drops the type constraint and decodes the bytes.

## Finishing the release (owner action required)

Everything the release needs is now built and staged in
`dist/release/6.2.5/`. Two steps still require credentials this environment
does not have, so they are **prepared but not executed**:

1. **Upload the assets to the existing `v6.2.5` GitHub release.** Do not
   create a v6.2.6 and do not delete historical releases; replace the asset
   set in place:

   ```bash
   gh release upload v6.2.5 \
     dist/release/6.2.5/SeedCode-CLI-6.2.5-windows-x64.exe \
     dist/release/6.2.5/SeedCode-CLI-Setup-6.2.5.exe \
     dist/release/6.2.5/seedcode_cli-6.2.5-py3-none-any.whl \
     dist/release/6.2.5/seedcode_cli-6.2.5.tar.gz \
     dist/release/6.2.5/SHA256SUMS.txt \
     --clobber
   ```

   Uploading the regenerated `SHA256SUMS.txt` is essential: the currently
   published one has no wheel entry, so `install.sh` would refuse with
   "No SHA256 checksum for …seedcode_cli-6.2.5-py3-none-any.whl" even after
   the wheel is attached.

2. **Re-publish the Vercel deployment** so `/install.ps1` and `/install.sh`
   serve the fixed sources from `IRM_INSTALL/`. The deployment source of
   truth is that directory; with the Vercel CLI authenticated against the
   project that serves `seedcode-cli.vercel.app`:

   ```bash
   cd IRM_INSTALL && npx vercel --prod
   ```

   (If the project is instead wired to this repository, a push of the branch
   it tracks triggers the redeploy.) Then confirm the live files:

   ```bash
   curl -fsSL https://seedcode-cli.vercel.app/install.sh  | head -3
   curl -fsSL https://seedcode-cli.vercel.app/install.ps1 | head -3
   ```

## Known limitations

- Compiled artifacts are built and staged in `dist/release/6.2.5/`:
  `SeedCode-CLI-6.2.5-windows-x64.exe`, `SeedCode-CLI-Setup-6.2.5.exe`,
  `seedcode_cli-6.2.5-py3-none-any.whl`, `seedcode_cli-6.2.5.tar.gz` and a
  `SHA256SUMS.txt` covering all four. The Windows binaries were produced by
  `scripts\windows\build.bat` (PyInstaller + Inno Setup) and byte-match the
  published release; the wheel and sdist were produced by `python -m build`
  and staged by `scripts\windows\stage_release.py`. Every checksum entry was
  generated from the real file and independently re-verified with
  `sha256sum -c`. These files are build output: they are git-ignored and are
  never committed to source control.
- **The published `v6.2.5` GitHub release does not carry the Linux artifact
  yet.** The live release attaches exactly three files —
  `SeedCode-CLI-6.2.5-windows-x64.exe`, `SeedCode-CLI-Setup-6.2.5.exe` and a
  `SHA256SUMS.txt` with no wheel entry (checked against the GitHub Releases
  API on 2026-09-21). The wheel/sdist now exist and are staged locally, so
  the fix is the asset re-upload above. Until that happens, `install.sh` on
  Linux/macOS stops with "Could not download
  …seedcode_cli-6.2.5-py3-none-any.whl" rather than installing unverified
  code. The installer code itself is correct: its checksum parser resolves
  the staged wheel entry, verified directly against
  `dist/release/6.2.5/SHA256SUMS.txt`.
- Live provider calls (chat, `/doctor` connectivity) were not exercised
  against the network here; the HTTP and provider layers are covered by
  offline unit tests instead.
- The compiled Windows binaries were **not rebuilt** in this pass: the
  existing `SeedCode-CLI-Setup-6.2.5.exe` and
  `SeedCode-CLI-6.2.5-windows-x64.exe` byte-match the published release
  (identical SHA256), so rebuilding them would only change the hashes and
  force a re-upload. The full `scripts\windows\build.bat` pipeline was
  therefore not re-run (Inno Setup IS installed here); the release-state
  artifacts were re-verified instead.
- The Vercel delivery of the installers was not executed in this environment
  (no deployment credentials, no Vercel project config in the repository).
- **The production installers at `https://seedcode-cli.vercel.app` are still
  the pre-fix sources.** As of 2026-09-21, `GET /install.sh` and
  `GET /install.ps1` serve the old scripts: the Linux one still bypasses
  verification when no SHA256 tool is found and still picks the release asset
  with a substring match, and the Windows one still fails on the real release
  (see above). **The published one-liners do not work until the Vercel
  deployment is re-published from `IRM_INSTALL/`.** The repository contains no
  Vercel project source or config, so that deploy happens outside this repo.
  **Production installer round-trip: reproduced and failed (fixed script
  verified separately, above).**
