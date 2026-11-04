# TUI Redesign Report — honest status

Phase 2 / 9 / 12 deliverable. This report records what the SeedCode terminal
interface actually does today, what was verified in pass 2, and what the
mandated full redesign has **not yet** delivered. No redesign work is
claimed that was not done.

## Correcting the mission's starting assumption

The prompt states "the current UI is not sufficiently similar to OpenCode's
terminal experience". The codebase tells a more mixed story: SeedCode's TUI
is an existing, substantial implementation — a Textual-based interface, not
a stub:

* `seedcode/ui/tui.py` — 2,268 lines: `ChatTUI` application shell,
  `TuiStreamRenderer` (live streaming), `_ContentControl` (conversation
  content), `_AutocompleteControl` (slash-command/tool/file suggestions),
  `_PlanControl` (plan view), `_ThinkingControl` (reasoning indicator).
* Supporting modules (7,600 lines total in `seedcode/ui/`): `header.py`,
  `codemode_header.py`, `statusbar.py`, `layout.py`, `dashboard.py`,
  `session_view.py` (281), `taskpanel.py` (209), `tasks.py` (705),
  `dialog.py`, `menu.py`, `fuzzy.py` (fuzzy suggestion matching),
  `theme.py` (208 — theme management), `state.py` (559 — status/state
  management), `badges.py`, `briefing.py`, `content.py`, `textbox.py`,
  `tree.py`.

Verified present against the Phase-2 requirements:

| Requirement | Verified component |
| --- | --- |
| Header / status / footer shortcuts | `header.py`, `codemode_header.py`, `statusbar.py` |
| Conversation + streaming + markdown rendering | `TuiStreamRenderer`, `_ContentControl`, `_markdown_lines` (rich-markdown line renderer) |
| Tool execution summaries | `tool_start` / `tool_done` observer payloads (with `call_id`, `duration_ms`, `diff`, real exit codes as of pass 2) |
| Activity from real execution events only | `_notify`/`on_step` payloads originate in the engine's execution path; the pass-2 call-state layer feeds real per-call status/duration, not fabricated activity |
| Slash autocomplete + fuzzy matching | `_AutocompleteControl`, `fuzzy.py` |
| Task view | `taskpanel.py`, `tasks.py`, `_PlanControl` |
| Dialogs/menus (agent selection, permission approval, themes) | `dialog.py`, `menu.py` + `/agents` command (pass 1) |
| Theme management | `theme.py` + `/theme` |
| Windows compatibility | Textual (py `textual` dep) — cross-platform terminal stack; no TS/Bune runtime |

## What pass 2 changed in the TUI

Nothing structural. The pass-2 engine changes improve what the UI *can*
honestly display: every `tool_done` payload now carries `call_id` and
measured `duration_ms`, deduplication events surface as their own narration,
and subagent delegation emits `[subagent:<profile>]` prefixed activity from
the child engine. The UI consumes these through the existing observer
pipeline; no TUI component code was rewritten in pass 2.

## What the mandated rebuild has NOT delivered yet (honest list)

1. **A full visual recomposition of the main layout** in OpenCode's current
   proportions and spacing. The interface works at different terminal sizes
   but was not rebuilt to match OpenCode's exact visual grammar.
2. **A side-by-side screenshot comparison** (Phase 9): no screenshots were
   captured in pass 2. Capturing OpenCode's TUI requires running their Bun
   app; this remains the concrete next step for the visual-verification
   phase, including permission-dialog and agent-switching comparisons.
3. **Collapsible/compact activity sections** as an interaction pattern.
4. **Input-history navigation parity** deep-dive: `textbox.py` implements
   multiline input with suggestion filtering; paste handling and long-input
   rendering were not re-verified against OpenCode behaviour in pass 2.
5. **Widget-level parity** with `@opencode/tui` internals (their TUI is a
   separate TypeScript package). Reusing that package directly inside Python
   is not technically feasible (different runtime); the Python-native option
   remains Textual, which is already in use.

## Recommendation for the next pass

The engine-side gaps that fed the UI honestly (call state, subagents, model
routing) are now in place; the remaining work is presentation-layer:
screenshot the two applications, diff their interaction flows (permission
dialog, agent switcher, session navigation, error rendering), then rebuild
the seedcode/ui/* visual layer section by section with real event payloads
driving every displayed state.
