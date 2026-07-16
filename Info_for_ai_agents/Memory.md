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

✔ Multi-provider AI backend (real, no mocks) — three backends behind one
  Provider interface (core/providers/): OpenRouter, AeroLink, Ollama.
    - core/providers/base.py — Provider ABC (validate_key, list_models,
      stream_chat), ModelInfo, ValidationResult, ProviderError(transient=)
      so the engine knows what is worth retrying.
    - openrouter.py — OpenAI SDK pointed at https://openrouter.ai/api/v1,
      streaming chat completions; /model lists ONLY free models (prompt AND
      completion pricing == 0 from GET /models); key validated via GET /key
      (sk-or- prefix checked offline first).
    - aerolink.py — AeroLink (https://aerolink.lat) is an Anthropic-compatible
      gateway at https://capi.aerolink.lat (per Info_for_ai_agents AeroLink
      docs: ANTHROPIC_BASE_URL=https://capi.aerolink.lat/). Speaks the
      Messages API over httpx: POST /v1/messages with SSE streaming
      (content_block_delta/text_delta), GET /v1/models for the catalogue.
      If the gateway doesn't proxy /v1/models, key is accepted provisionally
      and `/model <id>` sets a typed model id directly.
    - ollama.py — native local API: GET /api/tags (detect + installed models,
      with sizes), POST /api/chat NDJSON streaming. No key; host configurable
      via config.ollama_host (default http://localhost:11434, editable with
      /settings ollama_host ...). Friendly errors for "not running" /
      "model not installed".
    - Engine: core/chat.py streams via get_provider(config.provider) using
      config.model on EVERY request; retries transient failures (2x, backoff)
      only before the first token has streamed. No model is ever hardcoded —
      AppConfig.model defaults to "".
    - Config: AppConfig gained api_keys{provider->key} and models{provider->
      last model} (switching providers restores the last model used with it);
      legacy v0.x configs (flat api_key, display-name provider) migrate via a
      model_validator. Env overrides: OPENROUTER_API_KEY / AEROLINK_API_KEY
      (ENV_KEYS dict in config/defaults.py).
    - Commands: commands/provider.py adds /provider (numbered menu or
      `/provider <name>`; collects+validates the key on first use; detects
      Ollama) and the new /model (fetches the live list, pick by number,
      exact id, or filter substring; `/model <text>` selects directly).
      /settings edits username/stream/ollama_host (model removed — /model
      owns it). /config shows per-provider masked keys + ollama host.
    - Onboarding (app.py) reuses select_provider/select_model, so first-run
      setup == /provider + /model. Cancelling is allowed; banner shows
      "Setup needed" until config.is_configured().
    - core/client.py is a deprecated shim delegating to OpenRouterProvider
      (kept so nothing external breaks); core/__init__ re-exports the
      provider registry.
    - Tests rewritten for the provider registry + per-provider config
      (test_client.py, test_config.py, test_memory.py).

✔ Provider management v2 — per-provider config isolation + /apikey.
    - Config schema (config.json) is now NESTED per provider:
        active_provider: openrouter | aerolink | ollama
        providers.openrouter: {api_key, model}
        providers.aerolink:   {api_key, model}
        providers.ollama:     {api_key(unused), model}
      Switching providers changes ONLY active_provider — every provider keeps
      its own key + model, so nothing is ever overwritten and switching back
      restores instantly. Older formats migrate automatically on load
      (v0.x flat api_key; v1.x provider/model + api_keys{}/models{} maps).
    - core/models.py: ProviderConfig model + AppConfig.active_provider/
      providers. Compatibility properties keep call sites unchanged:
      config.provider and config.model read/write the ACTIVE provider's slot
      (pydantic v2 property setters). remember_model()/recall_model() kept as
      no-op/alias for compatibility.
    - New command /apikey (alias /key): edit the API key for the ACTIVE
      provider — shows the current masked key, validates before saving;
      `/apikey <key>` sets it inline. Ollama reports "no key needed".
    - /config now lists every provider's masked key AND saved model.
    - /provider no longer copies models around: the switched-to provider's
      own saved model is simply active again (commands/provider.py
      simplified; key entry extracted into _collect_key, reused by /apikey).
    - Provider rules unchanged and real (no mocks): OpenRouter = free models
      only from live GET /models; AeroLink = dynamic GET /v1/models (typed id
      fallback); Ollama = installed models from GET /api/tags.
    - Tests: nested-shape round-trip, v1.x migration, switch-isolation
      (keys/models never clobbered), /apikey registration.

✔ Windows stability pass (2026-07) — reliability/logging/UX hardening:
    - cli.py: UTF-8 reconfigure of stdout/stderr (errors=replace) so redirected
      output can't UnicodeEncodeError; heavy imports deferred so --version is
      instant; fatal errors logged with traceback.
    - utils/logger.py: real rotating file log ~/.seedcode/logs/seedcode.log
      (512KB x2, INFO; SEEDCODE_DEBUG=1 -> DEBUG). No keys/content logged.
    - app.py: Ctrl+C mid-stream cancels only the response (partial reply kept);
      command dispatch guarded so no handler can kill the REPL; failed or
      empty turns drop the dangling user message (keeps transcript
      alternating for strict Messages APIs).
    - config/manager.py: save_config is best-effort (logs, never crashes);
      load/fallback logged. utils/helpers.py: app_dir falls back to temp dir
      if home is unwritable.
    - openrouter.py: explicit timeouts (20s connect/180s read), max_tokens
      always sent (clamped 1..4096, :free models capped at 1024 —
      DEFAULT_MAX_TOKENS in core/models.py; fixes free-tier HTTP 402),
      friendly 402/404/5xx messages (5xx transient -> auto-retry).
    - ui: legacy-conhost fallbacks (ASCII logo + [OK]/[X] marks); banner is
      the branded screen only (no provider/model/version lines).
    - /settings gained max_tokens (int, >=1); /config shows it.

## In Progress

Nothing — installation system verified by real runs (install → build →
uninstall → reinstall all exit 0; dist/seedcode.exe and SeedCodeSetup.exe
both compiled successfully).

## Next

Phase 8 — Testing, packaging, Windows executable, pip install
Phase 9 — Public release (docs, GitHub, PyPI)

## Notes

Providers (3, via core/providers registry — never hardcode models):
  openrouter — OpenAI-SDK compatible, base_url https://openrouter.ai/api/v1,
               free models only in /model
  aerolink   — Anthropic Messages API gateway, base https://capi.aerolink.lat
  ollama     — local, native API, no key, host in config.ollama_host
Python 3.12+, Rich + Prompt Toolkit + Pydantic + httpx + OpenAI SDK.

Config + history stored under ~/.seedcode (cross-platform, owner-only perms).
Config shape: active_provider + providers.{openrouter,aerolink,ollama}
  = {api_key, model} each (older formats auto-migrate on load).
OPENROUTER_API_KEY / AEROLINK_API_KEY env vars override stored keys.
Commands: /help /provider /apikey /model /clear /reset /history /config
  /settings /about /version /exit.

Accent color: followed the PROJECT VISION (Soft Green) over Design.md's cyan,
for a cohesive green identity. Cyan reserved for rare emphasis.

Module map (post-restructure):
  cli.py                entry point (thin) -> app.run
  app.py                application controller: onboarding + REPL
  __main__.py           `python -m seedcode`
  core/chat.py          ChatEngine + ChatError (provider-routed, retries)
  core/providers/       base.py (Provider ABC) + openrouter/aerolink/ollama
                        + __init__.py registry (PROVIDERS, get_provider)
  core/client.py        deprecated shim -> providers.openrouter
  core/streaming.py     raw stream iterator (iter_stream)
  core/models.py        Pydantic AppConfig + Message
  config/manager.py     load/save AppConfig (env key overrides)
  config/defaults.py    ENV_KEYS, CONFIG_FILENAME
  commands/             registry/dispatch + help/clear/history/about/provider
  memory/storage.py     HistoryStore (per-session JSON)
  memory/manager.py     list_sessions / load_session
  ui/                   theme, banner, renderer, prompts, UI class
  utils/helpers.py      paths + timestamps (temp-dir fallback)
  utils/logger.py       rotating file log ~/.seedcode/logs/seedcode.log
                        (terminal always silent; SEEDCODE_DEBUG=1 -> DEBUG)
