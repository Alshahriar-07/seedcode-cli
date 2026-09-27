"""YouTube music player: /youtube, /play, /music.

/play <query>   — open YouTube and search/play a song or video.
/youtube <query> — open YouTube and search for a song or video.
/music <query>  — alias for /youtube.

Uses the Computer Engine's YouTube skills to search, open, and play content
automatically — no UI clicks needed.
"""

from __future__ import annotations

from . import CommandContext, CommandResult, command


@command("play", "Play a song or video on YouTube. Usage: /play <query>")
def _play(ctx: CommandContext, arg: str) -> CommandResult:
    if not arg.strip():
        ctx.ui.error("Please provide a song or video query.")
        ctx.ui.dim("Example: /play 'Never Gonna Give You Up'")
        return CommandResult()
    return _youtube_action(ctx, "play", arg)


@command("youtube", "Search YouTube and play a song or video. Usage: /youtube <query>")
def _youtube(ctx: CommandContext, arg: str) -> CommandResult:
    if not arg.strip():
        ctx.ui.error("Please provide a search query.")
        ctx.ui.dim("Example: /youtube 'Never Gonna Give You Up'")
        return CommandResult()
    return _youtube_action(ctx, "play", arg)


@command("music", "Alias for /youtube. Search and play music on YouTube. Usage: /music <query>")
def _music(ctx: CommandContext, arg: str) -> CommandResult:
    if not arg.strip():
        ctx.ui.error("Please provide a music query.")
        ctx.ui.dim("Example: /music 'Never Gonna Give You Up'")
        return CommandResult()
    return _youtube_action(ctx, "play", arg)


def _youtube_action(ctx: CommandContext, action: str, query: str) -> CommandResult:
    """Generic helper to run a YouTube skill from the computer engine."""
    # Check if the Computer Engine is available (desktop capability)
    from ..computer import is_available

    ok, reason = is_available()
    if not ok:
        ctx.ui.error(f"Desktop automation not available: {reason}")
        ctx.ui.dim("Ensure Seed Code has permission to control your computer.")
        return CommandResult()

    # Use the appropriate skill
    from ..computer.skills import get_engine

    engine = get_engine()
    if action == "play":
        # youtube_play will search, open, and start playback
        with ctx.ui.thinking(f"Searching and playing: '{query}'"):
            engine.youtube_play(query)
    else:
        # Fallback to generic youtube search
        with ctx.ui.thinking(f"Searching YouTube: '{query}'"):
            engine.youtube_search(query)

    ctx.ui.success(f"YouTube action completed: {query}")
    return CommandResult()