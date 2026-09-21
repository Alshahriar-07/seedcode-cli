# seedcode-cli (npm launcher)

> **Not an official installation method.** Seed Code CLI is installed with the
> official IRM installer system — see the
> [main README](https://github.com/Alshahriar-07/seedcode-cli#installation)
> and [GitHub Releases](https://github.com/Alshahriar-07/seedcode-cli/releases).

Seed Code CLI is distributed through the IRM installer system and GitHub
Releases:

- **Windows:** `irm https://seedcode-cli.vercel.app/install.ps1 | iex`
- **Linux:** `curl -fsSL https://seedcode-cli.vercel.app/install.sh | bash`

## What this package is

A compatibility launcher retained for users who already manage tooling through
npm. It is a thin wrapper around the official release: on Windows it downloads
the official `SeedCode-CLI-6.2.5-windows-x64.exe` from the GitHub release,
verifies its SHA256 against the release's `SHA256SUMS.txt`, caches it under
`~/.seedcode/npm/`, and runs it — Python is never required. On Linux and
macOS it defers to the official installer rather than shipping a binary.

If Seed Code is already on `PATH` (installed by the Windows installer or the
IRM installer), the native `seedcode` command is used directly and this
launcher is unnecessary.

The launcher forwards every argument to the real binary
(`seedcode --version`, `seedcode --help`, and the interactive app all work).
