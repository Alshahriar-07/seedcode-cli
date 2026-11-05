"""The ``use_skill`` tool: load one skill's full instructions on demand."""

from __future__ import annotations

from ..core.skills import SkillError, load_skill
from .base import ToolResult, register


@register(
    "use_skill",
    "Load the full instructions for one project or global skill named in the "
    "AVAILABLE SKILLS catalog. Skills are instructions, not executable tools; "
    "call this when its description matches the current work, then follow it.",
    {
        "name": "Skill name exactly as listed in the AVAILABLE SKILLS catalog.",
    },
    mutates=False,
)
def use_skill(permissions, args) -> ToolResult:
    name = str(args.get("name") or "").strip()
    try:
        body = load_skill(permissions.workspace, name)
    except SkillError as exc:
        # A bad name is a normal, correctable tool failure: report it as a
        # result the model can read, not an exception that ends the step.
        return ToolResult(False, str(exc))
    return ToolResult(True, body)
