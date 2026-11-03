# Seed Code CLI v9.3.1 — Technical Report

**Version:** 9.3.1 · **Built:** 2026-11-02 · **Platform:** Windows 11 x64
**Baseline commit:** `2084b89` (v9.2.1)
**Artifacts:** `dist/release/v9.3.1/`

## 1. Scope and approach

All twelve update-plan documents were read, then the repository was audited
before editing. The plan describes a target architecture (orchestrator,
hierarchical TODO, session context, tool pipeline, UI, providers, Ollama
lifecycle, installers) that the repository **already implements** in
`core/session.py`, `core/tasks.py`, `core/agent.py`, `tools/`, `ui/`,
`core/providers/` and `scripts/`. This release therefore repairs the reported
regression and synchronises the release, rather than duplicating working
subsystems. Every claim below is backed by code inspection, tests, or a build
that was actually run.

## 2. Bug discovered and fixed

### `NameError: name 'ensure_workspace' is not defined`

- **Symptom:** every launch channel (source, pip, EXE) crashed before the UI.
- **Root cause:** `seedcode/cli.py` called `ensure_workspace(...)` and read
  `terminal.plain` but imported neither. `ensure_workspace` is really defined in
  `seedcode/workspace.py:283`; terminal state comes from
  `seedcode/utils/terminal_env.py::detect_terminal`.
- **Fix:** `main()` now imports `ensure_workspace` from `.workspace` and
  `detect_terminal` from `.utils.terminal_env`, takes one `TerminalEnv` snapshot
  after logging setup, logs it, and uses that snapshot for both the plain-mode
  and persistent-TUI decisions. No stub, no behavior change to workspace
  selection, and the terminal is detected once so the two decisions cannot
  disagree.
- **Verification:** the real `main()` is executed in a new regression test with
  only `seedcode.app.run` replaced; it selects `SEEDCODE_WORKSPACE`, builds the
  UI and starts the app. `python -m seedcode --version` and the rebuilt
  `dist/seedcode.exe --version` both report `Seed Code CLI 9.3.1`.

## 3. Agent, TODO, session and tool architecture (audited, preserved)

- `core/session.py::CodeSession` runs plan → execute one subtask → verify →
  persist → next, with resume (`CodeSession.resume`, line 972) and final
  verification; `core/tasks.py::TaskGraph` holds parent/child tasks, states
  (`BLOCKED`, `FAILED`, `RECOVERING`, …), evidence and retry counts.
- Tool execution flows through the registry in `tools/__init__.py` with
  permission gating, workspace boundaries, real filesystem/shell handlers and
  structured `ToolResult`s; `core/agent.py` parses both native provider tool
  calls and the text protocol and feeds results back into the next model turn.
- Session state is checkpointed under the workspace (`.seedcode/`) and restored
  on resume.
- UI streams chat/activity on the left, the backend-driven plan panel on the
  right, and a model-exposed thinking panel above the input.

No changes were needed in these subsystems for this release; their existing
tests all still pass. No requirement was marked complete on the strength of this
paragraph alone — see §5 for executed tests.

## 4. Version and packaging changes

`9.3.1` is the single source of truth in `seedcode/__init__.py`; consumers
updated together: `pyproject.toml` (dynamic via hatch), `npm/package.json`,
`IRM_INSTALL/install.ps1`, `install.sh`, `RELEASE_INFO.txt`,
`scripts/windows/setup.iss`, `README.md`, `CHANGELOG.md`, `RELEASE.md`,
`LICENSE`, `scripts/windows/README.md`, and the version-assertion tests.

Built and verified:

| Artifact | Size (bytes) | Verification |
|---|---:|---|
| `seedcode_cli-9.3.1-py3-none-any.whl` | 342,533 | installed into a clean venv; `seedcode --version` and `python -m seedcode --version` → 9.3.1 |
| `seedcode_cli-9.3.1.tar.gz` | 295,854 | installed into a clean venv; console script → 9.3.1 |
| `seedcode-cli-9.3.1.tgz` | 6,432 | `npm pack`; contents are bin/lib/package.json/README/LICENSE |
| `SeedCode-CLI-9.3.1-windows-x64.exe` | 43,995,025 | PyInstaller 6.21 one-file build; `--version` → 9.3.1; `--help` renders |

Real SHA-256 hashes are in `dist/release/v9.3.1/SHA256SUMS.txt` and
`BUILD-MANIFEST.json`; they were computed from the staged files after the build.

## 5. Tests

- Baseline before changes: `python -m pytest -q` → **833 passed in 79.90s**.
- Final after changes: `python -m pytest -q` → **846 passed in 76.29s**, 0 failed
  (this includes the 6 installer-UI tests from §10).
- Added `tests/test_v931_startup.py`:
  1. executes the real `main()` with a workspace and asserts the app starts;
  2. guards the two imports and the snapshot ordering;
  3. verifies `python -m seedcode --version` == `Seed Code CLI 9.3.1`;
  4. verifies version agreement across package, npm and both IRM installers and
     the Setup script.
- Added `tests/test_v931_npm.py` (3 tests, real Node modules): launcher version,
  artifact filename/URL/checksum-name agreement, and packaged file manifest.
- Ran `tests/test_v710_e2e_todo_app.py`: **4 passed** with a real `AgentEngine`,
  the real tool registry, real file writes/edits and real subprocess test runs
  (no model or tool mocks).
- Existing version assertions were updated to 9.3.1 (the installer boundary
  tests now use 9.3.1 examples). No test was deleted, skipped or weakened.

## 6. Completion phase (Setup EXE, npm launcher, installation tests)

Inno Setup 6 was found at `%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe` (my
first pass had only checked Program Files). The Setup EXE was therefore built
for real, not placeholder-renamed:

- Compiled with `ISCC /DAppVersionFromBuild=9.3.1 /DOutputBaseName=SeedCode-CLI-Setup-9.3.1 setup.iss`.
- `FileVersion` and `ProductVersion` both read **9.3.1**; company Eagox Studio.
- Fresh silent install (`/VERYSILENT /DIR=<temp> /LOG=...`) exited 0, and the
  installer's own probes logged `Exe verification passed: Seed Code CLI 9.3.1`
  and `PATH verification passed: Seed Code CLI 9.3.1`.
- Verified during install: installed exe reports 9.3.1 and renders `--help`;
  machine PATH gained the install directory; Start Menu group shortcut and
  public Desktop shortcut were created.
- Verified after silent uninstall: install directory removed, machine PATH
  entry removed, both new shortcuts removed, a pre-existing Seed Code Start
  Menu shortcut (predating this build) untouched, and a `~/.seedcode` canary
  file preserved.
- Fixed in this phase: the uninstaller now skips the user-data prompt entirely
  on `/VERYSILENT` and keeps the data unconditionally, so a silent uninstall
  can never delete settings/API keys/history.

An npm launcher bug found and fixed in this phase: `npm/lib/download.js`
passed the artifact descriptor object to `assetName(version, platform)`, which
threw `no prebuilt asset for undefined` before any download. Cache name,
checksum lookup and URL now come from one `artifactFileName()` helper, covered
by `tests/test_v931_npm.py`. A clean `npm install <tarball>` then ran the
launcher as `seedcode-cli (npm launcher) v9.3.1`. (A full download still needs
a published GitHub release; publishing is not authorized.)

## 7. Final artifact inventory (`dist/release/v9.3.1/`)

| Artifact | Size (bytes) | SHA-256 (prefix) |
|---|---:|---|
| `seedcode_cli-9.3.1-py3-none-any.whl` | 342,533 | `a0c9e0c4…` |
| `seedcode_cli-9.3.1.tar.gz` | 295,854 | `6574bfad…` |
| `seedcode-cli-9.3.1.tgz` | 6,620 | `7f69bb70…` |
| `SeedCode-CLI-9.3.1-windows-x64.exe` | 43,995,025 | `d65a57c1…` |
| `SeedCode-CLI-Setup-9.3.1.exe` | 45,441,897 | `adb1adae…` |

Full hashes are in `SHA256SUMS.txt` and `BUILD-MANIFEST.json`; `sha256sum -c`
reported **OK** for all five artifacts. `build_status` is `complete` with no
missing artifacts.

## 8. Provider verification (not live)

- **Ollama:** no server answered at `localhost:11434` (HTTP 000). No live model
  discovery, generation, streaming, tool call or retry run was performed.
- **OpenRouter / custom:** no credentials present in the environment. No live
  generation or tool call was performed.
- Provider logic is covered by the existing mocked provider/retry suite, which
  passes in the 840-test run. No live-success claim is made.
- IRM installers were not run end-to-end because no 9.3.1 release is published
  (publishing is not authorized). Their version-verification logic is covered
  by `tests/test_installer_version.py`.

## 9. Remaining limitations

- No live external-provider integration test (no server/credentials available).
- IRM installation against the published release is untested for 9.3.1.
- `scripts/bump.py` remains a broken unused one-line artifact, deliberately not
  rewritten as part of this release.
- The release folder is `dist/release/v9.3.1/` as requested; `stage_release.py`
  writes `dist/release/<version>/` (no `v`) under the repository's convention.

## 10. Installer experience refresh

Both remote installers (`IRM_INSTALL/install.ps1`, `IRM_INSTALL/install.sh`)
now share one monochrome Seed Code UI. Only presentation and stage reporting
changed: download, checksum, install, PATH and version-verification logic is
unchanged and still runs in the same order.

- **Startup:** compact ASCII wordmark, identity line and version, followed by
  `Initializing installation...`. No artificial delay.
- **Seven real stages:** checking system requirements, detecting environment,
  preparing the installation directory, downloading, verifying integrity,
  installing (PowerShell: installing and configuring PATH), verifying
  installation. Progress shows completed stages (`[####....] 4/7`) and never an
  invented percentage.
- **Real progress:** PowerShell keeps its byte-driven HTTP download bar; Bash
  uses curl's own download progress. Spinners wrap real work only (Bash
  pip/pipx installs, PowerShell SHA-256 hashing).
- **Safe fallback:** redirected output, `NO_COLOR`, `--plain` and `TERM=dumb`
  all produce static, escape-free text. Width comes from `COLUMNS`/`tput` with
  an 80-column fallback; below 56 columns the Bash banner drops to one line.
  New Bash code is 3.2-compatible and needs no GNU-only animation tooling.
- **Ctrl+C:** the Bash traps stop a running spinner, restore the cursor, remove
  the temporary directory and exit 130; PowerShell keeps its `finally`
  cleanup and restores the cursor before finishing.
- **Errors:** `Installation failed` plus reason, suggested action and a stable
  code (`DOWNLOAD_FAILED`, `CHECKSUM_MISSING`/`CHECKSUM_MISMATCH`,
  `UNSUPPORTED_PLATFORM`, `MISSING_DEPENDENCY`, `PERMISSION_DENIED`,
  `VERIFY_FAILED`, `ENVIRONMENT_FAILED`). The success screen appears only after
  the installed binary's `--version` check passes.

### Verification actually run

- `bash -n IRM_INSTALL/install.sh` → OK.
- Windows PowerShell 5.1 parsed `install.ps1` with zero errors; its extracted
  `Show-Banner`/`Show-Progress` rendered the banner and `4/7`, and
  `Show-Spinner` returned a real 64-character SHA-256.
- End-to-end Bash install against a fake local release (fake `uname`/`curl`,
  real checksum/install/version logic): exit 0, all seven stages, the installed
  binary reported `Seed Code CLI 9.3.1`, and redirected output contained no ANSI.
- Bad-checksum run: exit 1, `Checksum mismatch`, an actionable suggestion and
  `Error code: CHECKSUM_MISMATCH`; nothing was installed.
- `tests/test_v931_installer_ui.py` (6 tests) passes together with the existing
  installer checksum and version suites. Full-suite result is in §5.
- **Not tested:** native Linux/macOS (no host available — the Bash flow ran
  under Windows Git Bash with a simulated Linux release) and PowerShell 7
  (`pwsh` is not installed). No success is claimed for those environments.
