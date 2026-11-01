"""v9.2.0: the agent pipeline really uses the selected workspace.

The crash report said sending a message raised ``NameError: name
'active_workspace' is not defined`` — ``app._make_agent`` referenced the
workspace lookup without importing it. These tests pin the real contract:

* ``app`` exposes ``active_workspace`` (the free name always resolves);
* the engine's permission boundary is bound to the workspace the user chose
  (the process working directory ``/workspace`` changed), not to some
  unconnected module constant;
* after ``set_workspace(...)`` the *next* engine build picks the new folder
  up, so a workspace selection during a session reaches the tools.
"""

from __future__ import annotations

import threading

import pytest

from seedcode import app as app_module
from seedcode.core import agent as agent_module
from seedcode.core.models import AppConfig
from seedcode.tools.permissions import (
    ActionGate,
    PermissionLevel,
    PermissionManager,
)


class _FakeUI:
    """The few presenter-facing surfaces _make_agent touches."""

    is_tui = False

    def __init__(self) -> None:
        self.asks: list[tuple[str, str]] = []

    def confirm_tool_action(self, label: str, description: str):
        self.asks.append((label, description))
        return "y"

    @property
    def console(self):
        return None


class _RecordingEngine:
    """Stands in for AgentEngine: records the permission boundary."""

    last: "_RecordingEngine | None" = None
    last_permissions: PermissionManager | None = None

    def __init__(self, config, permissions, on_event=None, on_step=None):
        self.config = config
        self.permissions = permissions
        self.presenter = None
        _RecordingEngine.last = self
        _RecordingEngine.last_permissions = permissions


@pytest.fixture(name="bound_engine")
def _bound_engine(monkeypatch, tmp_path, in_directory):
    """Patch the engine construction and build under the selected tmpdir."""
    monkeypatch.setattr(app_module, "AgentEngine", _RecordingEngine)
    app_module._make_agent(_FakeUI(), AppConfig())
    engine = _RecordingEngine.last
    yield engine
    _RecordingEngine.last = None
    _RecordingEngine.last_permissions = None


@pytest.fixture(name="in_directory")
def _in_directory(tmp_path, monkeypatch):
    """Chdir into tmp_path for the test (restored afterwards)."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_app_resolves_active_workspace() -> None:
    # Regression for "NameError: name 'active_workspace' is not defined":
    # the free name used by _make_agent resolves against an imported binding,
    # never against a dangling reference.
    assert callable(app_module.active_workspace)


def test_selected_workspace_reaches_the_permission_engine(
    bound_engine, tmp_path, monkeypatch
) -> None:
    from seedcode.workspace import active_workspace, workspace_label

    assert workspace_label() == tmp_path.name
    assert active_workspace() == tmp_path

    engine = _RecordingEngine.last
    assert engine is not None
    assert engine.permissions.workspace == tmp_path
    # The default permission mode is workspace-scoped: the agent reads and
    # writes inside the selected folder only.
    assert engine.permissions.level == PermissionLevel.WORKSPACE


def test_a_workspace_switch_is_picked_up_for_the_next_engine(
    bound_engine, tmp_path, monkeypatch
) -> None:
    other = tmp_path.parent / "other-project"
    other.mkdir(exist_ok=True)

    from seedcode.workspace import set_workspace

    workspace = set_workspace(other)
    assert workspace == other.resolve()

    app_module._make_agent(_FakeUI(), AppConfig())
    engine = _RecordingEngine.last
    assert engine is not None
    assert engine.permissions.workspace == other.resolve()
