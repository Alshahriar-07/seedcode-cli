from __future__ import annotations

import os
from pathlib import Path

from seedcode.core.models import AppConfig
from seedcode.tui_backend import TuiBackend
from seedcode.workspace import menu_lines, parse_choice, select_workspace


def test_workspace_list_returns_real_directories(tmp_path: Path) -> None:
    (tmp_path / "project").mkdir()
    (tmp_path / "notes.txt").write_text("not a directory", encoding="utf-8")
    backend = TuiBackend(config=AppConfig(), workspace=tmp_path)

    response = backend.handle(
        {"id": 1, "method": "workspace.list", "params": {"path": str(tmp_path)}}
    )

    assert response["ok"] is True
    assert response["result"]["path"] == str(tmp_path.resolve())
    assert [entry["name"] for entry in response["result"]["entries"]] == ["project"]


def test_workspace_select_changes_the_backend_working_directory(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "selected"
    target.mkdir()
    monkeypatch.chdir(tmp_path)
    persisted = []
    backend = TuiBackend(config=AppConfig(), workspace=tmp_path, persist=persisted.append)

    response = backend.handle(
        {"id": 2, "method": "workspace.select", "params": {"path": str(target)}}
    )

    assert response["ok"] is True
    assert Path.cwd() == target.resolve()
    assert Path(backend.workspace) == target.resolve()
    assert persisted == [backend.config]


def test_backend_rejects_unknown_methods_without_raising() -> None:
    backend = TuiBackend(config=AppConfig(), workspace=Path.cwd())

    response = backend.handle({"id": 3, "method": "unknown.call", "params": {}})

    assert response["ok"] is False
    assert "unknown method" in response["error"].lower()


def test_provider_list_never_returns_api_key_material() -> None:
    config = AppConfig(provider="openrouter", model="test-model")
    config.set_api_key("openrouter", "secret-key-do-not-return")
    backend = TuiBackend(config=config, workspace=Path.cwd())

    response = backend.handle({"id": 4, "method": "provider.list", "params": {}})

    assert response["ok"] is True
    assert "secret-key-do-not-return" not in repr(response)
    openrouter = next(item for item in response["result"] if item["id"] == "openrouter")
    assert openrouter["hasKey"] is True


def test_invalid_settings_update_does_not_mutate_configuration() -> None:
    config = AppConfig(stream_idle_timeout=30)
    backend = TuiBackend(config=config, workspace=Path.cwd())

    response = backend.handle({
        "id": 5,
        "method": "settings.update",
        "params": {"streamIdleTimeout": 0},
    })

    assert response["ok"] is False
    assert backend.config.stream_idle_timeout == 30


def test_workspace_fallback_has_no_numbered_choices(tmp_path: Path) -> None:
    lines = menu_lines(tmp_path)
    assert all("1  Current" not in line and "2  Choose" not in line for line in lines)
    assert parse_choice("1", tmp_path) is None
    assert parse_choice("2", tmp_path) is None
    assert parse_choice(str(tmp_path)) == str(tmp_path)
    assert select_workspace(cwd=tmp_path, input_fn=lambda _prompt: "") == tmp_path