# File-Tool Audit — Seed Code CLI

Scope: the entire file-operation path — tool registry, schemas, executors,
permission gate, workspace resolution, agent tool-calling loop, session
execution pipeline, event integration, and error/verification handling.

## 1. Discovered file tools (single registry `seedcode/tools/base.py`)

| Tool | Module | Notes |
|---|---|---|
| `read_file` | filesystem.py | optional line range, numbered output, 256 KB cap without range |
| `write_file` | filesystem.py | creates parents, read-back verification, diff, surrogate repair |
| `create_file` | filesystem.py | refuses to clobber an existing file |
| `copy_file` | filesystem.py | byte-compare verification |
| `delete_file` | filesystem.py | refuses directories, delete confirmation gate, verifies absence |
| `list_dir` (+alias `list_directory`) | filesystem.py | |
| `append_file` | filesystem.py | encoding/newline preservation via textio, verified |
| `create_directory` | filesystem.py | refuses to stomp a file |
| `delete_directory` | filesystem.py | refuses workspace root/parents, recursive needs explicit flag + confirmation |
| `rename_file` | filesystem.py | bare-name only, refuses overwrite, verified |
| `move_file` | filesystem.py | cross-drive safe, refuses overwrite, verified |
| `project_index` | filesystem.py | capped tree, skips caches/hidden |
| `find_files` (+alias `search_files`) | search.py | glob over names or posix paths, 100-match cap |
| `search_text` (+alias `search_content`) | search.py | regex over contents, `file:line:` output, binary sniff, size cap, glob name filter |
| `edit_file` | patch.py | exact single-occurrence replacement, ambiguity rejected, full read-back verification |
| `insert_in_file` | patch.py | before/after an exactly-once anchor, verified |
| `run_command`, `git`, `web_search`, `web_fetch` | terminal.py / web.py | out of scope here |

## 2. What is already correct (not rebuilt)

* One registry is the source of truth for both the native JSON schemas and the
  text-protocol manifest; aliases are exact clones (never drift).
* Every tool passes `PermissionManager` (read_only / workspace / full_system +
  per-category confirmation gate); `resolve()` anchors relative paths at the
  workspace and `check_write` blocks traversal outside it; `delete_directory`
  additionally refuses the workspace root and its parents.
* Writes, appends, edits, copies, deletes, renames and moves all verify the
  filesystem afterwards and report honest failure on mismatch; diffs are
  generated from real before/after text.
* Existing-file edits preserve encoding (utf-8-sig / utf-8 / latin-1) and
  dominant newline via `textio`.
* The agent loop executes calls through the registry, returns structured
  results (`[OK]/[ERROR]`, call ids preserved), defers surplus calls with an
  explicit "(not executed)" result, bounds steps (15 / 60 in Code Mode), bounds
  consecutive failures (3) and calls per step (8), and feeds malformed-call
  parse errors back to the model. Mutating steps run sequential; read-only
  steps may run parallel. `TurnEvidence` records files changed, commands run,
  tests, inspections and errors, and `verify_task` gates completion on that
  observed evidence — a printed claim alone can never complete a task.

## 3. Problems found

| id | Area | Finding |
|---|---|---|
| R1 | read | A binary file is decoded with `errors="replace"` and dumped into the model context as mojibake; no binary guard. |
| R2 | read | Encoding is unreported for a full-range read (a latin-1 file silently renders as replacement chars) — acceptable for context use, but the binary case must refuse instead of degrade. |
| W1 | write | `write_file` (and the shared edit-path write) is non-atomic: a crash/interrupt mid-write can leave a truncated file. |
| W2 | write | A binary file can be silently overwritten with text, corrupting it. |
| S1 | search | `search_text` has no case-insensitive flag and no exclude patterns; the glob filter matches file *names* only. |
| S2 | search | The walker ignores `.gitignore` contents (only a fixed directory-name set and hidden entries). |
| S3 | search | Discovered during verification: the `glob` filter was not path-aware — `fnmatch` lets `*` cross separators, so `src/*.py` also matched `src/gen/x.py`. Fixed by reusing `_glob_to_regex` for path globs (one-level semantics; `src/**/*.py` for recursive). |
| E1 | edit | No way to replace several independent occurrences in one call (N calls today) — an explicit opt-in replaces N safely while keeping the default exactly-one rule. |
| L1 | loop/limits | Text-protocol duplicate blocks each execute (bounded by steps/call caps) — noted, intentional. |
| L2 | line-range edits | No dedicated line-range patch operator; the agent is expected to read the numbered range then use `edit_file`/`write_file`. Deliberate exact-match design (see patch.py module docstring — model-generated diff appliers drift and burn the step budget). |
| F1 | enforcement | A *research/explanation* task legitimately performing only read-only tool calls can never be verified: `TurnEvidence.any_success` counts only file writes and commands, so `verify_task`'s progress backstop fails it, the repair loop fires and the session ends failed/blocked. Violates "enforcement must understand task intent". |
| F2 | enforcement | Code printed in chat then claimed as implemented is already rejected (unmet "no concrete progress" → repair instruction naming the unmet criteria), but the prompt nowhere states rules B/D/H ("code belongs in files", "a printed call is not an executed tool call", "chat is not file output"). |
| P1 | perf | `search_text` re-reads every file per call with no per-file stat caching — acceptable at current scale; noted. |

## 4. Actions taken (see the delivery report for results)

1. `read_file`: refuse probable binaries (NUL probe) with actionable guidance.
2. `write_file`: refuse to overwrite a probable binary with text; write
   atomically (temp file → flush → fsync → `os.replace`).
3. All edit-path writes (`textio.write_text_file`) become atomic + fsynced.
4. `search_text`: optional `ignore_case` and `exclude` args; glob may carry a
   `/` to match relative paths; minimal `.gitignore` honoring in the walker
   (name/path/dir patterns, negations skipped).
5. `edit_file`: optional explicit `replace_all`.
6. `verify_task` gains a `read_only` intent switch; the session classifies a
   task as read-only only when it has no modify verbs and at least one research
   verb — then real read-only inspection evidence (with no errors) can satisfy
   the progress backstop. Mutating intent is the default: enforcement stays as
   strict as before for anything ambiguous.
7. Prompt: explicit Rule B/D/H lines injected into the Code Mode preamble and
   native/text-protocol instructions.
