# Seed Code — Windows Installation Scripts

Production scripts for installing, building, packaging, and removing Seed Code
on Windows (the primary platform).

| File | Purpose |
|------|---------|
| `install.bat` | Install Seed Code from source: Python 3.12+ check, pip upgrade, dependencies, editable install, verification. |
| `build.bat` | **Complete release pipeline**: PyInstaller → `dist\seedcode.exe`, then Inno Setup → `Release\SeedCodeSetup.exe`. |
| `setup.iss` | Inno Setup script (compiled by `build.bat`): Program Files install, PATH, Python 3.12 bootstrap, shortcuts, verified install, uninstaller. |
| `uninstall.bat` | Remove a source install: PATH cleanup, `pip uninstall`, confirmed user-data deletion. |
| `README.md` | This document. |

---

## 1. Installing (end users)

From the repository root (or anywhere — the script finds the repo relative to
itself):

```bat
scripts\windows\install.bat
```

The installer:

1. Looks for **Python 3.12+** (tries `py -3.13`, `py -3.12`, `py -3`,
   `python`, `python3`). If none qualifies, it **opens
   <https://www.python.org/downloads/>** and exits with code 2.
2. Upgrades `pip`.
3. Installs dependencies from `requirements.txt`.
4. Installs Seed Code in editable mode (`pip install -e .`).
5. Verifies the package imports and reports where the `seedcode` command
   landed. Clear `[SUCCESS]` / `[ERROR]` messages either way.

A full log is appended to `%USERPROFILE%\.seedcode\logs\install.log`.

> If `seedcode` is not recognized immediately, open a **new** terminal —
> PATH changes only apply to fresh sessions.

## 2. Release pipeline (`build.bat`)

```bat
scripts\windows\build.bat
```

Runs the complete, hands-off release pipeline:

1. **Stage 1 — PyInstaller.** Ensures PyInstaller + project dependencies,
   builds the one-file console executable, then smoke-tests it
   (`seedcode.exe --version` must exit 0 or the pipeline stops):
   `dist\seedcode.exe`
2. **Stage 2 — Inno Setup.** Locates `ISCC.exe` (PATH, then the standard
   per-user and Program Files locations) and compiles `setup.iss`:
   `Release\SeedCodeSetup.exe`

Log: `%USERPROFILE%\.seedcode\logs\build.log`. The `build\` work directory is
disposable cache; `dist\` and `Release\` are the outputs.

## 3. The public installer (`Release\SeedCodeSetup.exe`)

A professional Setup Wizard (modern style, license page, directory selection)
that requires **zero configuration** from the user:

- Installs the self-contained `seedcode.exe` into **Program Files**
  (all Python dependencies are already bundled inside the exe at build time).
- **Bootstraps Python 3.12** if missing: downloads the official python.org
  installer and runs it unattended (`/quiet InstallAllUsers=1 PrependPath=1
  Include_pip=1`), then upgrades pip. Best-effort: an offline decline never
  breaks the Seed Code install itself, since the app doesn't need it to run.
- Adds the install directory to the **system PATH** (and broadcasts the
  change, so new CMD/PowerShell/Windows Terminal sessions pick it up).
- Creates **Start Menu** shortcuts and an optional **Desktop** shortcut.
- **Verifies** the install by executing `seedcode --version`; a non-zero exit
  aborts setup with an explicit failure message instead of reporting success.
- Ships a full uninstaller that also reverts the PATH change.

After installation, from any directory in any new terminal:

```
C:\Users\You> seedcode
```

No `cd`, no venv, no `python`, no `pip` — just `seedcode`.

## 4. Uninstalling

```bat
scripts\windows\uninstall.bat
```

Removes any `seedcode` entries from the **user** PATH, uninstalls the Python
package, and asks before deleting user data at `%USERPROFILE%\.seedcode`.
Pass `/keepdata` to skip the user-data step entirely. If Seed Code was
installed via `SeedCodeSetup.exe`, prefer **Settings → Apps** (the Inno
uninstaller also reverts its PATH change).

---

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success. |
| 1 | A pip/PyInstaller/ISCC step failed (see the log). |
| 2 | Python 3.12+ not found. |
| 3 | Script is not inside the Seed Code repository. |
| 4 | Inno Setup 6 (ISCC.exe) not found (build.bat stage 2). |
