"""/agents — view and switch the active agent profile.

Seed Code runs ONE unified agent engine; a profile is a persona + capability
preset over it, not a mode switch: it appends focused instructions, restricts
which tools the profile's turns may call (really enforced — a call outside
the whitelist is refused), and can cap the per-turn step budget.

* ``/agents``            — list every selectable profile (built-ins + any
                           defined in ``~/.seedcode/agents.json`` or
                           ``.seedcode/agents.json``).
* ``/agents build``      — switch now (applies to this session).
* ``/agents <id>``       — project-defined ids (in ``agents.json``) apply as
                           custom agents.

``/agent`` itself is NOT reused: it was retired in v9.2.0 as an on/off mode
switch, and reusing the name would contradict that decision (the retired-
command tests pin it). "agents" states clearly that this lists/switches
profiles of the one engine.

The built-ins: ``build`` (default, behaviour-neutral), ``plan`` and
``explore`` (read-only, inspection tools only) and ``general`` (full tools).
"""

from __future__ import annotations

from ..core.agents import AgentRegistry, describe as describe_profile
from . import CommandContext, CommandResult, command


def _registry_for(engine) -> AgentRegistry:
    workspace = getattr(getattr(engine, "permissions", None), "workspace", None)
    return AgentRegistry(workspace)


def _print_list(ui, engine, registry: AgentRegistry) -> None:
    active = getattr(engine, "profile", None)
    for profile in registry.list():
        marker = "→" if (active is not None and profile.id == active.id) else " "
        ui.print(f"  {marker} {describe_profile(profile)}")
    ui.dim(
        "Switch with /agents <id>; 'plan' and 'explore' are read-only. "
        "Define custom agents in .seedcode/agents.json."
    )
    if getattr(engine, "set_agent", None) is None:
        ui.dim("This session's engine does not support switching.")


@command("agents", "Switch agent profile (/agents build|plan|explore|general)")
def _agent(ctx: CommandContext, arg: str) -> CommandResult:
    engine = ctx.engine
    ui = ctx.ui
    set_agent = getattr(engine, "set_agent", None)

    if not arg.strip():
        _print_list(ui, engine, _registry_for(engine))
        return CommandResult()

    wanted = arg.strip().lower()
    registry = _registry_for(engine)
    if registry.get(wanted) is None:
        ids = ", ".join(a.id for a in registry.list())
        ui.warning(f"Unknown agent '{arg.strip()}'. Available: {ids}")
        return CommandResult()
    if set_agent is None:
        ui.warning("This engine has no agent-profile support; nothing was switched.")
        return CommandResult()
    profile = set_agent(wanted)
    ctx.config.agent_profile = profile.id
    from ..config.manager import save_config

    save_config(ctx.config)
    state = getattr(ui, "state", None) or getattr(
        getattr(ui, "tui", None), "state", None
    )
    if state is not None:
        state.update(agent_state=profile.name)
    ui.success(f"Agent profile set to {profile.name} ({profile.id}).")
    if profile.tools is not None:
        ui.dim(
            "Restriction active: only "
            + ", ".join(sorted(profile.tools))
            + " may run in these turns."
        )
    return CommandResult()
