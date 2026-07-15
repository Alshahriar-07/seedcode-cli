# Seed Code Memory

## Current Version

v0.1.0

## Completed

✔ Phase 1 — Project setup, folder structure, config system, CLI launcher
✔ Phase 2 — API key onboarding, validation, secure config saving
✔ Phase 3 — OpenRouter connection, streaming, basic chat
✔ Phase 4 — Conversation history, context memory, markdown rendering
✔ Phase 5 — Command system (/help /model /clear /reset /history /config /about /version /exit)
✔ Phase 6 — Rich UI (ASCII banner, Seed Green theme, spinner, panels, syntax highlight)
✔ Phase 7 (partial) — Config manager + runtime model switching (/model)
✔ Restructure — reorganized into the layered package tree from the updated
  Architecture.md. No behaviour changed; all existing code was relocated/split
  and imports rewired. Verified: full import graph loads (no cycles), all 9
  commands register, banner renders, 8/8 pytest pass.
    - core/    chat.py (engine), client.py (OpenRouter client + validate_key,
               absorbed old validator.py), streaming.py (stream iterator),
               models.py (Pydantic AppConfig + Message)
    - config/  manager.py (load/save), defaults.py (ENV_KEY, CONFIG_FILENAME)
    - commands/ __init__.py (framework + registry) with handlers grouped as
               help.py (help/model/version), clear.py (clear/reset),
               history.py (history/config), about.py (about/exit)
    - memory/  storage.py (HistoryStore), manager.py (list/load sessions)
    - ui/      theme.py, banner.py, renderer.py, prompts.py, __init__.py (UI)
    - utils/   helpers.py (paths/time), logger.py (silent NullHandler)
    - cli.py thinned to entry point; app.py added as application controller;
      __main__.py added for `python -m seedcode`.
    - Root: added data/ (config.json, history/chats.json placeholders),
      docs/screenshots/, requirements.txt, install.sh. Tests split into
      test_config.py / test_client.py / test_memory.py.
    - Removed: old flat modules, unused assets/logo.txt, prior config//history/
      scaffold folders (superseded by the package layout).

✔ Installation system v2 — per-platform scripts/ tree, batch-script based
  (supersedes the earlier compiled-EXE tooling, which was removed on request).
  Every script was EXECUTED end-to-end on this machine, not just written:
    - scripts/windows/install.bat  — finds Python 3.12+ (py -3.13/-3.12/-3,
      python, python3; opens python.org if missing), upgrades pip, installs
      requirements.txt, pip install -e ., verifies import + `where seedcode`.
      VERIFIED: exit 0, "Seed Code 0.1.0 imports cleanly".
    - scripts/windows/build.bat    — ensures PyInstaller + deps, builds
      one-file dist\seedcode.exe (--collect-all rich/prompt_toolkit,
      --collect-submodules pydantic/openai). VERIFIED: exit 0, produced a
      real 20 MB PE binary at dist/seedcode.exe.
    - scripts/windows/setup.iss    — Inno Setup 6 script: Program Files,
      system PATH add/remove, Start Menu + optional Desktop shortcuts, full
      uninstaller. VERIFIED: compiled with ISCC 6 -> SeedCodeSetup.exe (~21 MB)
      built successfully (Output/ dir cleaned; rebuild with `iscc setup.iss`).
      Gotcha: Pascal-style { } comments break inside [Code] when they contain
      literal {app}; use // comments there.
    - scripts/windows/uninstall.bat — PowerShell-based user-PATH cleanup,
      pip uninstall -y seedcode, confirmed deletion of ~/.seedcode
      (/keepdata flag skips it). VERIFIED: exit 0, package removed cleanly,
      then reinstalled via install.bat.
    - scripts/windows/README.md   — usage, exit codes, packaging steps.
    - scripts/linux/install.sh + scripts/macos/install.sh — unchanged from v1
      (bash, venv-isolated, global launcher; bash -n validated).
  Exit codes: 0 ok, 1 pip/build failure, 2 no Python 3.12+, 3 not in repo.
  All Windows scripts log to %USERPROFILE%\.seedcode\logs\.
  Inno Setup 6 is installed at ~\AppData\Local\Programs\Inno Setup 6\ISCC.exe
  (not on PATH).

✔ Windows release pipeline (Phase 8, Windows part) — build.bat is now the
  complete two-stage pipeline; the installer was PROVEN by a real silent
  install/uninstall cycle on this machine:
    - Stage 1: PyInstaller -> dist\seedcode.exe (one-file), smoke-tested with
      --version before packaging (pipeline aborts if it fails).
    - Stage 2: auto-locates ISCC.exe (PATH, %LOCALAPPDATA%\Programs\Inno
      Setup 6, Program Files; exit 4 if absent) and compiles setup.iss
      -> Release\SeedCodeSetup.exe (~21 MB).
    - setup.iss: packages dist\seedcode.exe into Program Files; system PATH
      add/remove (ChangesEnvironment broadcasts WM_SETTINGCHANGE so new
      terminals see it); Start Menu + optional Desktop shortcuts; when Python
      3.12+ is absent, downloads python-3.12.10-amd64.exe from python.org and
      runs it unattended (/quiet InstallAllUsers=1 PrependPath=1 Include_pip=1,
      then pip upgrade) — best-effort: never fails the app install, since the
      packaged exe is fully self-contained (deps baked in at BUILD time; pip
      has nothing to do at INSTALL time); MANDATORY post-install verification
      executes `seedcode --version` and Aborts setup on non-zero exit.
    - cli.py gained --version/-V (prints version, exits before any UI) —
      needed for installer verification; 8/8 tests still pass.
    - __main__.py switched to absolute import (from seedcode.cli import main):
      the relative import broke under PyInstaller (entry script runs as
      top-level __main__ with no parent package).
    - END-TO-END VERIFIED: build.bat exit 0 -> silent install exit 0, setup
      log shows "Verification passed: seedcode --version exit code 0";
      `seedcode --version` ran from C:\Users\alsha, C:\Windows, D:\ and from
      cmd.exe (all exit 0, resolving to C:\Program Files\SeedCode\seedcode.exe);
      Start Menu shortcuts created; silent uninstall removed the directory AND
      reverted the system PATH cleanly.
  Deliverable: Release\SeedCodeSetup.exe — public-release ready except code
  signing (unsigned exes trip SmartScreen until reputation builds).

## In Progress

Nothing — installation system verified by real runs (install → build →
uninstall → reinstall all exit 0; dist/seedcode.exe and SeedCodeSetup.exe
both compiled successfully).

## Next

Phase 8 — Testing, packaging, Windows executable, pip install
Phase 9 — Public release (docs, GitHub, PyPI)

## Notes

Provider: OpenRouter (OpenAI-SDK compatible, base_url = https://openrouter.ai/api/v1)
Default model: z-ai/glm-5.2
Python 3.12+, Rich + Prompt Toolkit + Pydantic + httpx + OpenAI SDK.

Config + history stored under ~/.seedcode (cross-platform, owner-only perms).
OPENROUTER_API_KEY env var overrides the stored key.

Accent color: followed the PROJECT VISION (Soft Green) over Design.md's cyan,
for a cohesive green identity. Cyan reserved for rare emphasis.

Module map (post-restructure):
  cli.py                entry point (thin) -> app.run
  app.py                application controller: onboarding + REPL
  __main__.py           `python -m seedcode`
  core/chat.py          ChatEngine + ChatError
  core/client.py        OpenRouter client factory + validate_key
  core/streaming.py     raw stream iterator (iter_stream)
  core/models.py        Pydantic AppConfig + Message
  config/manager.py     load/save AppConfig
  config/defaults.py    ENV_KEY, CONFIG_FILENAME
  commands/             registry/dispatch + help/clear/history/about handlers
  memory/storage.py     HistoryStore (per-session JSON)
  memory/manager.py     list_sessions / load_session
  ui/                   theme, banner, renderer, prompts, UI class
  utils/helpers.py      paths + timestamps
  utils/logger.py       silent logger (NullHandler)
