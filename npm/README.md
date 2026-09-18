# seedcode-cli (npm launcher)

Installs the `seedcode` command via npm. On Windows the launcher downloads
the official self-contained `SeedCode-CLI-6.2.0-windows-x64.exe` from the
[GitHub release](https://github.com/Alshahriar-07/seedcode-cli/releases),
verifies its SHA256 against the release checksums, caches it under
`~/.seedcode/npm/`, and runs it — Python is never required.

```bash
npm install -g seedcode-cli
seedcode
```

- Windows x64: supported (standalone binary, SHA256-verified download).
- Linux / macOS: not published as a prebuilt binary yet — the launcher
  points to `python -m pip install seedcode-cli`.

The launcher forwards every argument to the real binary
(`seedcode --version`, `seedcode --help`, and the interactive app all work).
A pip or Windows-installer install of Seed Code already on `PATH` is used
directly by the native command; the npm launcher is only needed when npm is
your preferred package manager.
