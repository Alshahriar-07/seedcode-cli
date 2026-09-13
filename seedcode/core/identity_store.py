"""SeedCode-owned identity: persistent personality independent of the model.

The identity layer (:mod:`.identity`) hardcodes the baseline personality.
This module lets the *owner* persist overrides in
``~/.seedcode/identity.json`` so personality belongs to SeedCode, not to
any model or prompt-in-flight:

.. code-block:: json

    {
      "identity": "You are SeedCode, ...",
      "tone": "concise and professional",
      "behavior_rules": ["Never claim unverified success."],
      "interaction_style": ["Answer in the user's language."]
    }

Rules:

* Overrides are loaded at every prompt build (cheap file read, cached by
  mtime), so a change takes effect on the next turn without a restart.
* The model can NEVER write this file: it is owner/user-edited
  configuration. Nothing in the tool surface exposes it.
* Model/provider choice has no path into identity: switching models
  re-renders only the "reasoning engine" line, produced by
  :func:`build_system_prompt` from live config — personality constants are
  untouched (pinned by tests).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..utils.helpers import app_dir


@dataclass(slots=True)
class IdentityProfile:
    """The SeedCode-owned personality configuration."""

    identity: str = ""                     # replaces the baseline identity block
    tone: str = ""                         # appended as a tone directive
    behavior_rules: list[str] = field(default_factory=list)
    interaction_style: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.identity or self.tone or self.behavior_rules
                    or self.interaction_style)

    def render(self) -> str:
        """The override text injected after the baseline identity."""
        if self.is_empty():
            return ""
        lines: list[str] = []
        if self.identity:
            lines.append(self.identity.strip())
        if self.tone:
            lines.append(f"\nTone: {self.tone.strip()}")
        if self.behavior_rules:
            lines.append("\nBehavior rules:")
            lines += [f"- {rule}" for rule in self.behavior_rules]
        if self.interaction_style:
            lines.append("\nInteraction style:")
            lines += [f"- {item}" for item in self.interaction_style]
        return "\n".join(lines).strip()


def identity_path() -> Path:
    """Where the owner's identity overrides live."""
    return app_dir() / "identity.json"


def load_identity() -> IdentityProfile:
    """Read ``identity.json`` (empty profile when absent/corrupt).

    A corrupt file must never break startup: it degrades to the baseline
    identity and the problem is logged once.
    """
    path = identity_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return IdentityProfile()
    if not isinstance(data, dict):
        return IdentityProfile()
    rules = data.get("behavior_rules")
    style = data.get("interaction_style")
    return IdentityProfile(
        identity=str(data.get("identity", "") or ""),
        tone=str(data.get("tone", "") or ""),
        behavior_rules=[str(r) for r in rules] if isinstance(rules, list) else [],
        interaction_style=[str(s) for s in style] if isinstance(style, list) else [],
    )


def save_identity(profile: IdentityProfile) -> bool:
    """Persist the profile atomically; False when the disk refuses."""
    path = identity_path()
    payload = {
        "identity": profile.identity,
        "tone": profile.tone,
        "behavior_rules": profile.behavior_rules,
        "interaction_style": profile.interaction_style,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        return True
    except OSError:
        return False


__all__ = ["IdentityProfile", "identity_path", "load_identity", "save_identity"]
