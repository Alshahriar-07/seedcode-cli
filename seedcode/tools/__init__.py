"""Tool engine for Seed Code agent mode.

Tools are the actions the agent may take on the user's machine: reading and
writing files, searching, running commands, and using git. Every tool is
registered in a table (mirroring the slash-command registry) and every
execution passes through the :class:`~seedcode.tools.permissions.PermissionManager`
first, so what the agent may touch is decided in exactly one place.
"""

from __future__ import annotations

from .base import (
    TOOL_REGISTRY,
    Tool,
    ToolError,
    ToolResult,
    get_tool,
    register,
    register_alias,
    tool_manifest,
)
from .permissions import (
    PermissionError_,
    PermissionLevel,
    PermissionManager,
    PermissionMode,
)

# Import tool modules for their registration side effects (same pattern as
# seedcode.commands): each module's @register calls populate TOOL_REGISTRY.
from . import filesystem, git, patch, search, terminal, web  # noqa: E402,F401

# v9.2.1: the equivalent tool names the agent contract promises, registered as
# exact aliases of their real implementation (runner, schema and mutation flag
# are reused, so an alias can never drift from the tool it names).
for _alias_name, _alias_target, _alias_description in (
    (
        "list_directory",
        "list_dir",
        "List the real entries of a directory in the workspace.",
    ),
    (
        "search_files",
        "find_files",
        "Find real files in the workspace by glob pattern.",
    ),
    (
        "search_content",
        "search_text",
        "Search real file contents for a regex; returns file:line matches.",
    ),
):
    register_alias(_alias_name, _alias_description, _alias_target)
del _alias_name, _alias_target, _alias_description

__all__ = [
    "PermissionError_",
    "PermissionLevel",
    "PermissionManager",
    "PermissionMode",
    "TOOL_REGISTRY",
    "Tool",
    "ToolError",
    "ToolResult",
    "get_tool",
    "register",
    "tool_manifest",
]
