"""v10.3.0 startup control-flow tests.

The reported bug: running ``seedcode`` still opened the old selection menu
instead of the redesigned intro screen. Two legacy paths caused it:

* the bundled TypeScript program (a pre-session workspace/provider/model menu)
  was launched before the Python TUI whenever ``seedcode/tui/dist/index.cjs``
  existed;
* ``ensure_workspace(interactive=True)`` asked for a workspace again.

These tests pin the corrected flow: no menu, no wizard, cwd as the workspace,
the persistent TUI entry point, and the legacy menu only on explicit request.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run_main(monkeypatch, tmp_path, *, no_tui: bool):
    """Run the real ``cli.main`` with only the long-lived UI replaced."""
    from seedcode import app, cli
    from seedcode.utils import shortcuts
    import seedcode.workspace as workspace_module

    monkeypatch.setenv("SEEDCODE_WORKSPACE", str(tmp_path))
    if no_tui:
        monkeypatch.setenv("SEEDCODE_NO_TUI", "1")
    else:
        monkeypatch.delenv("SEEDCODE_NO_TUI", raising=False)
        monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr(sys, "argv", ["seedcode"])
    monkeypatch.setattr(shortcuts, "first_run_shortcuts", lambda: [])
    monkeypatch.setattr(cli, "_launch_typescript_tui", lambda _log: False)

    calls: dict[str, object] = {}

    def fake_ensure_workspace(**kwargs):
        calls.update(kwargs)
        return tmp_path

    monkeypatch.setattr(workspace_module, "ensure_workspace", fake_ensure_workspace)

    started: list[str] = []
    monkeypatch.setattr(app, "run", lambda _ui: started.append("console"))
    monkeypatch.setattr(app, "run_persistent", lambda: started.append("persistent"))

    before = os.getcwd()
    try:
        cli.main()
    finally:
        os.chdir(before)
    return calls, started


def test_normal_startup_never_prompts_for_a_workspace(monkeypatch, tmp_path) -> None:
    calls, started = _run_main(monkeypatch, tmp_path, no_tui=True)
    assert calls.get("interactive") is False  # the picker is never opened
    assert started == ["console"]  # piped host keeps the sequential console


def test_interactive_startup_goes_straight_to_the_persistent_tui(
    monkeypatch, tmp_path
) -> None:
    calls, started = _run_main(monkeypatch, tmp_path, no_tui=False)
    assert calls.get("interactive") is False
    assert started == ["persistent"]


def test_legacy_typescript_menu_is_off_by_default(monkeypatch, tmp_path) -> None:
    from seedcode import cli

    log = logging.getLogger("test.startup")
    script = tmp_path / "index.cjs"
    script.write_text("// bundled legacy menu\n", encoding="utf-8")

    monkeypatch.setenv("SEEDCODE_TUI_SCRIPT", str(script))
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr(cli.shutil, "which", lambda _name: "node")
    monkeypatch.delenv("SEEDCODE_LEGACY_TS_TUI", raising=False)
    monkeypatch.delenv("SEEDCODE_FORCE_PYTHON_TUI", raising=False)

    ran: list[object] = []
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: ran.append(a))

    assert cli._launch_typescript_tui(log) is False
    assert ran == []  # the menu process was never even started


def test_legacy_typescript_menu_still_works_when_explicitly_requested(
    monkeypatch, tmp_path
) -> None:
    from seedcode import cli

    log = logging.getLogger("test.startup")
    script = tmp_path / "index.cjs"
    script.write_text("// bundled legacy menu\n", encoding="utf-8")

    monkeypatch.setenv("SEEDCODE_TUI_SCRIPT", str(script))
    monkeypatch.setenv("SEEDCODE_LEGACY_TS_TUI", "1")
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr(cli.shutil, "which", lambda _name: "node")

    class _Completed:
        returncode = 0

    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: _Completed())
    assert cli._launch_typescript_tui(log) is True


def test_the_persistent_entry_shows_the_intro_and_skips_the_wizard() -> None:
    source = (ROOT / "seedcode" / "app.py").read_text(encoding="utf-8")
    persistent = source[source.index("def run_persistent") : source.index("def run(ui: UI)")]
    assert "tui.banner_text()" in persistent  # the intro really is shown
    assert "_guided_setup(" not in persistent  # no blocking first-run wizard
    assert "workspace_root()" in persistent  # cwd is the workspace
    assert "select_workspace(" not in persistent


def test_no_other_startup_path_opens_a_workspace_picker() -> None:
    for name in ("cli.py", "app.py"):
        source = (ROOT / "seedcode" / name).read_text(encoding="utf-8")
        assert "select_workspace(" not in source, name
        assert "interactive=True" not in source, name


def test_saved_provider_and_model_are_restored_into_the_ui_state(tmp_path) -> None:
    """Restoration is a plain config read — no selector is involved."""
    from seedcode.core.models import AppConfig
    from seedcode.ui.state import AppState

    # A local provider needs no key, so this is a genuinely ready saved config.
    config = AppConfig()
    config.provider = "ollama"
    config.model = "qwen2.5-coder:7b"

    state = AppState.from_config(config, workspace=str(tmp_path))
    assert state.model == "qwen2.5-coder:7b"
    assert "Ollama" in state.provider  # a real label for the saved provider
