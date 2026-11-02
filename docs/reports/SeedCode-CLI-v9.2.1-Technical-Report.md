# Seed Code CLI v9.2.1 — Technical Report

**Version:** 9.2.1 · **Tag:** v9.2.1 · **Built:** 2026-11-02 · **Platform:** Windows 11 x64
**Repository:** https://github.com/Alshahriar-07/seedcode-cli

---

## 1. What was actually broken in v9.2.0

The audit came first, and it changed the plan. v9.2.0's execution architecture was
**not** broken: the agent loop, native tool calling with a text-protocol fallback,
tool-result feedback, real filesystem and subprocess tools, model-exposed thinking,
and the evidence-verified task graph all existed and worked. The pre-change suite was
green at **813 passing tests**, and a live Ollama turn already produced real tool calls.

The genuine failures were narrower but real:

| Reported symptom | Root cause found |
| --- | --- |
| Model "returns code instead of using tools" for some task types | The tool *surface* was incomplete. `run_tests`, `copy_file`, `create_file`, `get_command_output`, `check_exit_code`, `inspect_process`, `list_directory`, `search_files`, `search_content` were never registered. A model that asked for one was told `Unknown tool '...'`, so it fell back to describing the work in prose. |
| Thinking panel only useful on Ollama | Only the Ollama adapter emitted `ThinkingDelta`. The OpenAI-compatible path (`openrouter.py`, and therefore Default/Custom) discarded the provider's `reasoning` deltas, so cloud reasoning never reached the panel. |
| Exit status not inspectable | `run_command` printed the exit code inside display text; nothing structured carried it, so no later tool could ask "what was the exit code?" or "what did that command output?" |
| No way to run the suite from the agent | No dedicated test runner: the model had to guess a command and could not distinguish a real suite result from a build. |

Everything else the previous brief accused the product of — fake tools, decorative TODO
panel, fabricated thinking, fake streaming, silent provider switching — was checked
against the code and the tests and was **already implemented correctly**. This report
describes what changed, and only what was verified.

## 2. Architecture (verified, unchanged)

```
USER REQUEST
  → intent classification (deterministic, model-free)
  → model request (native tools OR text protocol fallback)
  → response type check
      ├── text            → rendered incrementally, also scanned for text-protocol calls
      └── tool call(s)    → validate name + args → real execution → real result
                          → result returned to the model → model continues
  → final answer only when no tool call remains
  → completion decided from recorded evidence, never from the model's claim
```

Key properties, each covered by tests: multiple sequential calls; multiple calls per
step (bounded per step, with the deferred ones answered so strict APIs stay valid);
tool failures returned as results; parallel execution only for read-only batches; a
bounded step budget; history compaction that never splits a tool call from its result.

## 3. Tools added in v9.2.1 (all real)

### File tools

| Tool | Behaviour |
| --- | --- |
| `create_file` | Creates a new file and writes the content; **fails** if the path already exists. Reuses the verified write path (read-back comparison, diff). |
| `copy_file` | `shutil.copy2` then a byte comparison of source and destination; refuses an existing destination unless `overwrite: true`. |

### Terminal tools

`run_command` now records every execution in a session command registry and accepts
`background: true`. On top of that registry:

| Tool | Behaviour |
| --- | --- |
| `run_tests` | Auto-detects pytest / npm / cargo / go from the workspace (or takes an explicit command) and returns the real output and exit code. |
| `get_command_output` | Returns the captured output of the latest command, or the one named by `id`. |
| `check_exit_code` | Returns the real exit code; a still-running command truthfully has none. |
| `inspect_process` | Lists session commands with state (running/finished), exit code, age, kind and command line. |

`ToolResult` gained an optional `exit_code` field so the real process status travels
structurally rather than being parsed out of prose.

### Contract aliases

`list_directory`, `search_files` and `search_content` are registered through a new
`register_alias()` helper that **reuses the target tool's runner, arguments, JSON-schema
types and mutation flag**. An alias therefore cannot drift from the tool it names, and
tests assert runner identity.

### Existing tools (unchanged, still real)

`read_file`, `write_file`, `edit_file`, `append_file`, `insert_in_file`, `delete_file`,
`rename_file`, `move_file`, `list_dir`, `create_directory`, `delete_directory`,
`project_index`, `find_files`, `search_text`, `git`, `web_search`, `web_fetch`.
Registry total: **27 tools**.

## 4. Live thinking, from the provider only

- **Ollama:** `think` is requested only when `GET /api/show` reports the `thinking`
  capability; a runtime refusal degrades gracefully (cached off, request retried without
  it). `message.thinking` becomes `ThinkingDelta`.
- **OpenAI-compatible (new in 9.2.1):** `delta.reasoning`, `delta.reasoning_content` and
  `reasoning_details[].text` become `ThinkingDelta`. Answer text is untouched.
- **Never fabricated:** no channel → no deltas → no invented thoughts. The agent relays
  `ThinkingDelta` as an `on_event("thinking", ...)` kind; it is never counted as answer
  content (asserted by the existing thinking tests and the new reasoning parser tests).

## 5. Streaming

The agent consumes provider deltas as they arrive; the Ollama NDJSON stream was
exercised live. Native tool calls are emitted as complete `ToolCallEvent`s after their
argument JSON finishes, so a tool call is never rendered as answer text. Text deltas
are forwarded immediately; the UI batches redraws.

## 6. TODO tracking

`TaskGraph` holds real `Task` states (`pending`, `running`, `verifying`, `blocked`,
`recovering`, `failed`, `completed`, `cancelled`, `skipped`). The right-side TASK PLAN
panel renders `graph.checklist()` — `○ ● ✓ ✗ ↻ –` plus an `N / M completed` counter.
Completion requires **evidence**: files changed or a command that succeeded, with no
unresolved error; a failing test run always fails the task; skipped tasks never count as
completed. A model saying "done" moves nothing.

## 7. Verification evidence (actual output)

Full suite:

```
833 passed in 75.02s (1:15)
```

New regression tests — `tests/test_v921_agent_tools.py` (12 tests) and `tests/test_v921_shortcuts.py` (6 tests), all using real files and real
subprocesses; no mocked execution path):

```
run_command  -> real exit_code 3; check_exit_code reports "code 3";
                get_command_output contains "hello-v921"
run_tests    -> "1 passed" (exit 0) on a passing suite;
                "1 failed" (non-zero exit) on a failing suite
background   -> tracked until exit, then "exited with code 0", output "bg-ok"
create_file  -> content on disk; second call refused ("already exists")
copy_file    -> byte-identical copy; existing destination refused without overwrite
```

Live Ollama, native tool calling (qwen2.5-coder:7b, 54.4 s):

```
call   : create_file({"path": "hello.py", "content": "print('Hello World')"})
result : Wrote 20 chars to .../hello.py (verified)
file   : exists, content = print('Hello World')
native : True
```

Live Ollama thinking (qwen3:4b):

```
CAPABILITY_THINKING: True
EVENT_KINDS_HEAD: ['ThinkingDelta', 'ThinkingDelta', ...]
THINKING_CHARS: 139
```

## 8. Build and artifacts

Built by the project's own pipeline (`scripts/windows/build.bat`: test gate → branding →
PyInstaller → Inno Setup → staging), plus `python -m build` and `npm pack`:

| Artifact | Size (bytes) | SHA256 |
| --- | ---: | --- |
| `SeedCode-CLI-9.2.1-windows-x64.exe` | 43,996,688 | `387e4019…dbf55318` |
| `SeedCode-CLI-Setup-9.2.1.exe` | 45,442,473 | `c3409029…ab0036fa` |
| `seedcode_cli-9.2.1-py3-none-any.whl` | 342,319 | `f5b6d43e…41a56f85` |
| `seedcode_cli-9.2.1.tar.gz` | 294,275 | `b1af7d7b…26bb4424` |
| `seedcode-cli-9.2.1.tgz` | 6,400 | `7bff6655…d2d8e92c` |

`sha256sum -c SHA256SUMS.txt` → **5/5 OK**.

- `dist/seedcode.exe --version` → `Seed Code CLI 9.2.1` (exit 0)
- Installer `FileVersion` / `ProductVersion` → `9.2.1`, company `Eagox Studio`
- Wheel `METADATA` → `Name: seedcode-cli`, `Version: 9.2.1`
- Seed Code icon verified inside both binaries by `build_assets.py --verify-exe`

### Desktop shortcuts on every install channel

Neither terminal channel left a desktop icon: `install.ps1` copied the exe and
updated `PATH` but never called a shortcut API, and `pip install` has no
post-install hook. v9.2.1 adds `seedcode/utils/shortcuts.py`, which writes real
`.lnk` files through the Windows Script Host COM object (no dependency), using
`[Environment]::GetFolderPath` so a OneDrive-redirected Desktop resolves
correctly. `install.ps1` creates Desktop + Start Menu shortcuts by default
(`-NoShortcuts` opts out), the Setup installer's `desktopicon` task is now
checked by default, and a pip install creates the shortcuts once on its first
interactive launch (marker-guarded, so a deleted shortcut is never resurrected).
`seedcode --install-shortcuts` recreates them explicitly.

Six regression tests cover real `.lnk` creation in a temp directory, the
one-time marker, `-NoShortcuts`/`SEEDCODE_NO_SHORTCUTS` opt-outs, the launcher
target selection, and the installer/Setup wiring.

## 9. Documentation updated

`README.md`, `CHANGELOG.md` (new 9.2.1 section), `RELEASE.md`,
`IRM_INSTALL/RELEASE_INFO.txt`, installer scripts (`install.sh`, `install.ps1`),
`scripts/windows/README.md`, `scripts/windows/setup.iss`, and this report set
(Markdown, TXT, DOCX, `BUILD-MANIFEST.json`).

The `LICENSE` was flattened to plain text — the box-drawing rules, `✓`/`✗`
bullets and other decorative glyphs are gone, and the version line reads
9.2.1. `setup.iss` embeds it as `LicenseFile`, so the installer's license
page shows the plain text; the wheel, sdist and npm tarball were rebuilt so
every artifact carries the same license.

## 10. Known limitations

1. The installer was compiled and version/icon/checksum-verified but **not executed**,
   because installing writes to Program Files and the system PATH.
2. Live provider testing covered the local Ollama server only; cloud reasoning parsing
   is unit-tested against the documented OpenRouter delta shapes.
3. `install.sh` / `install.ps1` were not run against published assets — no GitHub
   release was published (publishing requires explicit authorization).
4. Only Windows x64 binaries were built here.
5. A plain Agent turn without a persistent session has a single-task plan, so the TASK
   PLAN panel shows one row.
6. Nothing was committed or pushed: 18 modified files plus one new test file are in the
   working tree.

---

*Seed Code CLI — Plant ideas. Grow code.*
