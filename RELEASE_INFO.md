# Seed Code CLI v10.2.1 — Release Notes

**Version:** 10.2.1 · **Tag:** `v10.2.1` · **Release date:** 2026-11-04
**Release page:** https://github.com/Alshahriar-07/seedcode-cli/releases/tag/v10.2.1

v10.2.1 is the execution-reliability release. The model still makes the
decisions; SeedCode now owns the execution rules that keep a turn honest:
model-aware prompt/step budgets, a strict tool-call protocol with validation,
automatic continuation of truncated responses, a completion gate backed by
real evidence, and privacy-safe failover. The interface was also rebuilt
around a pure-black / Seed-Green identity with a full-width conversation and
an inline task plan.

## What's new in v10.2.1

### Agent execution reliability

- **Model-aware execution profiles** (`seedcode/core/model_profiles.py`):
  `small`, `standard` and `advanced` profiles control prompt style, step and
  tool-call budgets, output caps, history compaction and the preferred tool
  protocol. Resolution uses an explicit `prompt_profile`, then a user
  `model_profiles` map (exact names or globs), then local-provider parameter
  metadata; it never relies on a hardcoded model-name list. Local models with
  a declared small parameter size get the compact profile; everything else
  keeps the standard behaviour.
- **Compact small-model context.** A compact profile receives short,
  sequential instructions with explicit completion rules instead of the full
  coding-agent preamble. Standard and advanced models keep the existing,
  detailed context.
- **Text tool protocol with `TOOL_CALL:` markers.** Local models that cannot
  use native function calling emit:

  ```text
  TOOL_CALL: {"name": "list_dir", "arguments": {"path": "src/"}}
  ```

  The parser also accepts the legacy fenced ```` ```tool ```` block and
  OpenAI-style `name`/`arguments` JSON. A marker inside a documentation/code
  fence is treated as an example, never executed.
- **Strict pre-execution validation.** Tool name, required arguments and
  integer argument types are checked before dedupe, permission checks or
  execution. Malformed JSON is reported to the model and never executed.
- **No raw tool JSON in the transcript.** Recognized native calls and text
  markers are removed from the assistant text shown to the user.
- **Automatic response continuation.** Providers now report their terminal
  finish reason (`FinishEvent`). When generation stops at the output-token
  limit, SeedCode preserves the generated prefix, asks the model to continue
  exactly where it stopped, and returns the concatenated answer. Continuation
  is bounded by the active profile. Text-protocol replies are also inspected
  for an unterminated fence or a call marker with no complete payload.
- **Completion requires evidence.** A reply that claims completion after a
  failed tool call is sent back for one verification step (bounded once per
  turn) instead of being accepted. The persistent session's existing
  evidence-based task verification is unchanged.
- **Duplicate-call protection** (carried from v10.1.0) still replays a
  previously answered provider call id rather than executing it twice.

### Provider and model recovery

- **Bounded exponential backoff with jitter** for transient failures:
  approximately 1s, 3s, then 7s, capped at 30s. A provider's `Retry-After`
  hint is still honoured within that cap, and retries remain bounded.
- **Model failover** through the new `fallback_models` configuration: when the
  active model fails transiently, the next configured model on the same
  provider is tried before any provider switch, with the task, history and
  context preserved.
- **Model management screen (`/models`).** Switch from the live catalogue, use
  a custom model id when a gateway exposes no catalogue, maintain the ordered
  fallback list, clear the active model, and choose or pin an execution
  profile (globally or per model). Every action persists to `config.json`.
- **Provider management screen (`/provider manage`).** Select a provider, run
  a real connection test, inspect every visible provider's status/model/key
  state, toggle automatic failover, manage custom providers, and manage the
  active API key. `/provider test` and `/provider failover on|off` are direct
  shortcuts; the failover toggle updates the live engine immediately.
- **Local-to-cloud privacy gate.** A selected local provider (Ollama) is never
  silently moved to a cloud provider. Crossing that boundary requires the
  explicit `allow_local_to_cloud` opt-in (default `false`); the switch is
  announced when it happens.
- Provider failover remains opt-in through `auto_failover`.

### Terminal interface

- **Pure black background and Seed Green identity.** The Seed palette now uses
  `#22C55E` (signature), `#16A34A` (secondary), `#4ADE80` (bright),
  `#FAFAFA` (primary text), `#A1A1AA` / `#71717A` (secondary/muted),
  `#27272A` (border), `#EF4444` (error), `#F59E0B` (warning) and `#3B82F6`
  (information). The prompt_toolkit base style sets a black background.
- **Full-width conversation.** The permanent right-side task panel and its
  reserved column are gone; the conversation uses the complete terminal width.
- **Inline task progress.** The live plan renders in the conversation timeline
  as `Task Progress`, with the existing real-state glyphs and verified-work
  progress. It is driven by the task graph, not by UI animation.
- The composer, streaming, permission panel, thinking strip, scrolling and
  resize handling are unchanged.

### Configuration

New `config.json` fields (all optional):

| Field | Default | Purpose |
| --- | --- | --- |
| `prompt_profile` | `"auto"` | `auto`, `small`, `standard` or `advanced`. |
| `model_profiles` | `{}` | Per-model profile overrides (`{"qwen3:4b": "small"}`). |
| `max_agent_steps` | `0` | Explicit per-turn step ceiling (0 = profile). |
| `max_tool_calls_per_step` | `0` | Explicit calls per step (0 = profile). |
| `continue_truncated` | `true` | Continue output-limit responses. |
| `fallback_models` | `[]` | Model failover candidates for the active provider. |
| `allow_local_to_cloud` | `false` | Permit local→cloud failover explicitly. |

## Backend changes

- `seedcode/core/model_profiles.py`: profile definitions and resolution.
- `seedcode/core/agent.py`: compact prompt, marker parser, strict validation,
  visible-text filtering, continuation, completion nudge, profile budgets.
- `seedcode/core/providers/base.py`: `FinishEvent` stream event.
- `seedcode/core/providers/{openrouter,ollama,freemodel,aerolink}.py`: real
  finish reasons for output-limit continuation.
- `seedcode/core/models.py`: new configuration fields and the small-model
  output cap in `effective_max_tokens`.
- `seedcode/core/chat.py`: jittered bounded backoff, model failover and the
  local-to-cloud privacy gate.
- `seedcode/ui/theme.py`, `seedcode/ui/tui.py`: Seed palette, black base
  style, full-width layout and inline task progress.
- Version 10.2.1 across package metadata, npm launcher, IRM installers, Setup
  script and docs.

## Installation

```powershell
# Windows (recommended, per-user, no admin, verifies SHA-256)
irm https://seedcode-cli.vercel.app/install.ps1 | iex
```

```bash
# Linux / macOS
curl -fsSL https://seedcode-cli.vercel.app/install.sh | bash

# Any OS with Python 3.10+
pip install seedcode-cli
```

Verify afterwards:

```bash
seedcode --version    # -> Seed Code CLI 10.2.1
```

## Verification

The full test suite is run with `python -m pytest -q`. New coverage in
`tests/test_v102_agent_reliability.py` locks in profile resolution, the
`TOOL_CALL:` protocol (including fenced examples being ignored), strict
argument validation, raw-JSON filtering, truncated-response continuation,
the completion gate, model/provider failover privacy, the palette and the
inline plan. `tests/test_v102_management.py` covers the `/models` command and
its fallback/profile helpers, the provider failover toggle, and finish-reason
parity for FreeModel Claude/Codex and AeroLink.

## Known limitations

- **No release artifacts were built or published as part of this change.**
  Publishing is a separate, explicit step.
- **The TypeScript terminal UI option was evaluated and not adopted** in this
  release. The Python prompt_toolkit application was retained and extended;
  no second service or IPC layer was introduced.
- **Agent, session, tool, theme, settings, configuration and help screens are
  unchanged**; provider and model management now have dedicated real actions
  (`/provider manage`, `/models`).
- **Cloud providers were not live-tested** in this environment; their request
  paths remain covered by mocked provider tests.
- The inline plan is a live view of the current plan; it is not a separately
  scrollable transcript region.
