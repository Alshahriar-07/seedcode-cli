"""Model-aware execution profiles (v10.2.0).

SeedCode must not depend on the intelligence of the selected model. A local
Ollama model with a small context window needs different execution rules from a
large cloud coding model: a shorter prompt, fewer tool calls per step, a
bounded output budget and an explicit text tool protocol. A strong model should
not be slowed down or restricted by those same rules.

This module is the single place where those differences are expressed. A
:class:`ModelProfile` is a small, immutable bundle of execution settings; the
agent engine reads it for prompt style, step/call budgets, output caps,
history compaction and the preferred tool protocol.

Resolution is deliberately *not* a hardcoded model-name list:

1. an explicit user setting (``prompt_profile``) always wins;
2. a user model map (``model_profiles``) may pin models/globs;
3. for a local provider, the model's declared parameter size is metadata the
   user already downloaded — a small model gets the compact profile;
4. otherwise the standard profile is used.

Users can therefore correct a wrong guess without waiting for a code change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fnmatch import fnmatch


@dataclass(frozen=True, slots=True)
class ModelProfile:
    """Execution settings for one model-intelligence tier.

    ``max_output_tokens`` is an *additional* cap applied on top of the user's
    configured budget (0 means "no extra cap"), so a small local model is never
    asked for more output than it can use while an advanced model keeps the
    user's full budget.
    """

    id: str
    label: str
    description: str
    #: ``compact`` (short prompt), ``standard`` or ``full`` (detailed).
    prompt_style: str = "standard"
    #: ``auto`` (native when the provider supports it), ``native`` or ``text``.
    tool_protocol: str = "auto"
    #: Per-turn step ceiling used when neither the caller nor a profile sets one.
    max_steps: int = 15
    #: Tool calls executed from a single model step.
    max_calls_per_step: int = 8
    #: Extra completion-token cap (0 = keep the configured budget).
    max_output_tokens: int = 0
    #: Raw history size that triggers compaction.
    history_compact_at: int = 60
    #: Recent messages kept verbatim after compaction.
    keep_recent: int = 30
    #: Bounded automatic continuations after an output-limit stop.
    continuation_limit: int = 2
    #: Whether independent read-only calls in one step may run in parallel.
    parallel_reads: bool = True


#: The built-in tiers. ``standard`` deliberately reproduces the pre-10.2.0
#: behaviour so an existing session sees no regression.
PROFILES: dict[str, ModelProfile] = {
    "small": ModelProfile(
        id="small",
        label="Small / local",
        description=(
            "Compact execution for small local models: short instructions, "
            "explicit text tool protocol, few calls per step, bounded output."
        ),
        prompt_style="compact",
        tool_protocol="text",
        max_steps=24,
        max_calls_per_step=3,
        max_output_tokens=4096,
        history_compact_at=24,
        keep_recent=12,
        continuation_limit=3,
        parallel_reads=False,
    ),
    "standard": ModelProfile(
        id="standard",
        label="Standard",
        description=(
            "General-purpose coding models: native tools when available, "
            "normal step and call budgets."
        ),
        prompt_style="standard",
        tool_protocol="auto",
        max_steps=15,
        max_calls_per_step=8,
        max_output_tokens=0,
        history_compact_at=60,
        keep_recent=30,
        continuation_limit=2,
        parallel_reads=True,
    ),
    "advanced": ModelProfile(
        id="advanced",
        label="Advanced",
        description=(
            "Strong coding/reasoning models: full context instructions, more "
            "steps, native tool orchestration."
        ),
        prompt_style="full",
        tool_protocol="auto",
        max_steps=30,
        max_calls_per_step=8,
        max_output_tokens=0,
        history_compact_at=80,
        keep_recent=40,
        continuation_limit=2,
        parallel_reads=True,
    ),
}

DEFAULT_PROFILE_ID = "standard"
#: Values accepted by the ``prompt_profile`` config field.
PROFILE_CHOICES = ("auto", "small", "standard", "advanced")

#: ``7b``, ``7.6b``, ``1.5b``, and MoE ``8x7b``.
_SIZE_RE = re.compile(r"(\d+)\s*x\s*(\d+(?:\.\d+)?)\s*b\b|(\d+(?:\.\d+)?)\s*b\b")


def _parameter_billions(model: str) -> float | None:
    """The model's declared parameter count in billions, when it says so.

    Ollama tags encode this (``qwen3:4b``, ``llama3.1:70b``); an MoE tag such
    as ``8x7b`` is multiplied to its total. Anything else returns ``None`` and
    the caller falls back to the standard profile rather than guessing.
    """
    text = (model or "").strip().lower()
    if not text:
        return None
    match = _SIZE_RE.search(text)
    if match is None:
        return None
    try:
        if match.group(1) and match.group(2):
            return float(match.group(1)) * float(match.group(2))
        return float(match.group(3))
    except (TypeError, ValueError):
        return None


def _provider_local(config, provider_id: str | None) -> bool:
    """Whether the active provider runs on the user's machine."""
    try:
        from .providers import get_provider

        return bool(getattr(get_provider(provider_id or config.provider), "local", False))
    except Exception:
        return False


def profile_for(
    config,
    *,
    provider_id: str | None = None,
    model: str | None = None,
) -> ModelProfile:
    """Resolve the execution profile for ``config`` (never raises)."""
    explicit = str(getattr(config, "prompt_profile", "auto") or "auto").strip().lower()
    if explicit in PROFILES:
        return PROFILES[explicit]

    selected = (model or getattr(config, "model", "") or "").strip()
    mapping = getattr(config, "model_profiles", None) or {}
    if selected and isinstance(mapping, dict):
        lowered = selected.lower()
        for pattern, profile_id in mapping.items():
            pid = str(profile_id or "").strip().lower()
            if pid not in PROFILES:
                continue
            key = str(pattern or "").strip().lower()
            if not key:
                continue
            if key == lowered or fnmatch(lowered, key):
                return PROFILES[pid]

    if _provider_local(config, provider_id):
        size = _parameter_billions(selected)
        # Local models with an unknown size get the compact profile: on a
        # local server the safe default is the one that cannot overflow a
        # small context window. An explicit setting or map always overrides.
        if size is None or size <= 9:
            return PROFILES["small"]
        if size <= 30:
            return PROFILES["standard"]
        return PROFILES["advanced"]
    return PROFILES[DEFAULT_PROFILE_ID]


def output_token_cap(config) -> int:
    """The additional output cap for the active model (0 = no extra cap)."""
    try:
        return max(0, int(profile_for(config).max_output_tokens))
    except Exception:
        return 0


__all__ = [
    "DEFAULT_PROFILE_ID",
    "PROFILES",
    "PROFILE_CHOICES",
    "ModelProfile",
    "output_token_cap",
    "profile_for",
]
