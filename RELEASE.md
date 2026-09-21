# Seed Code CLI v6.2.5 — Release Notes

**Version:** 6.2.5 · **Tag:** `v6.2.5`

v6.2.5 is a UI, provider and distribution release. The startup screen lost its
ASCII logo, the built-in **Default** connection became a first-class provider
separate from OpenRouter, and installation moved to the official IRM installer
system.

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
configure `PATH`, and finish by running `seedcode --version`.

## Highlights

### UI

- The large ASCII logo is gone: `seedcode.ui.logo` was removed and nothing
  renders block or box art at startup.
- The startup screen is a borderless five-line header:

  ```text
  Seed Code CLI v6.2.5
  Provider  Default
  Model     nvidia/nemotron-3-super-120b-a12b:free
  Mode      Chat
  Status    ● Ready
  ```

- The `API Key` row appears only for providers that require a key. Default and
  Ollama never show one, and no key is ever displayed in full.

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
  build output were removed from the repository.

## Verification performed

- Full test suite: **721 tests passing** (`python -m pytest`), including new
  suites for the logo-free header (`tests/test_dashboard.py`), the provider ×
  mode matrix (`tests/test_provider_matrix.py`), Default/OpenRouter isolation,
  and terminal streaming/`stderr`/exit-code behaviour.
- `seedcode --version` prints `Seed Code CLI 6.2.5`.
- `IRM_INSTALL/install.sh` passes `bash -n` and its argument validation paths
  were exercised (`--help`, unknown option, malformed version).
- `IRM_INSTALL/install.ps1` parses cleanly under the Windows PowerShell
  parser, and its checksum-matching, architecture detection and install-path
  resolution were exercised directly. Both scripts are ASCII-only so Windows
  PowerShell 5.1 cannot misread them as ANSI.

## Known limitations

- Compiled artifacts (`SeedCode-CLI-Setup-6.2.5.exe`,
  `SeedCode-CLI-6.2.5-windows-x64.exe`, wheel/sdist) are produced by the
  release pipeline (`scripts\windows\build.bat` and `python -m build`). This
  working copy contains no compiled binaries; the previously present ones were
  stale v6.2.0/6.1.5 builds and were removed rather than shipped.
- Live provider calls (chat, `/doctor` connectivity) were not exercised
  against the network here; the HTTP and provider layers are covered by
  offline unit tests instead.
- The Windows installer and the Vercel delivery of the installers were not
  executed in this environment (no Inno Setup toolchain, no deployment
  credentials).
