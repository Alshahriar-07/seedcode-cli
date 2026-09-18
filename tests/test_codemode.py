"""Tests for v6.2.0 Code Mode: .seedcode memory, incremental index, commands."""

from __future__ import annotations

from pathlib import Path

import pytest

from seedcode.codemode import (
    SEEDCODE_DIRNAME,
    SeedcodeStore,
    assert_no_secrets,
)
from seedcode.codemode_state import (
    codemode_state,
    disable,
    enable,
    reset,
)


@pytest.fixture(autouse=True)
def _clean_state():
    reset()
    yield
    reset()


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth.py").write_text(
        "def login(user):\n    return True\n\n\nclass Session:\n    token = ''\n",
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("# Demo project\n", encoding="utf-8")
    return tmp_path


# --- .seedcode structure -------------------------------------------------------
class TestSeedcodeStore:
    def test_ensure_creates_full_skeleton(self, project: Path):
        store = SeedcodeStore(project)
        assert store.ensure() is True
        for sub in ("memory", "index", "context", "sessions"):
            assert (project / SEEDCODE_DIRNAME / sub).is_dir()
        assert (project / SEEDCODE_DIRNAME / "config.json").is_file()

    def test_memory_roundtrip(self, project: Path):
        store = SeedcodeStore(project)
        assert store.save_memory("architecture", "Layered: ui -> core -> tools.")
        assert store.load_memory("architecture") == "Layered: ui -> core -> tools."
        assert "architecture" in store.list_memories()

    def test_context_roundtrip(self, project: Path):
        store = SeedcodeStore(project)
        store.save_context("conventions", "Use pytest; type hints everywhere.")
        assert "type hints" in store.load_context("conventions")

    def test_config_default_and_custom(self, project: Path):
        store = SeedcodeStore(project)
        store.ensure()
        config = store.load_config()
        assert config["codemode"] is True
        assert store.save_config({"version": 1, "codemode": True, "extra": "x"})
        assert store.load_config()["extra"] == "x"

    def test_secrets_rejected_in_config_and_sessions(self, project: Path):
        store = SeedcodeStore(project)
        with pytest.raises(ValueError):
            store.save_config({"api_key": "sk-or-xxx"})
        with pytest.raises(ValueError):
            store.save_session_summary({"goal": "x", "token": "abc"})

    def test_session_summary_is_compact_json(self, project: Path):
        store = SeedcodeStore(project)
        store.save_session_summary({"goal": "fix login", "outcome": "done", "tool_calls": 4})
        summaries = store.latest_session_summaries()
        assert summaries and summaries[0]["goal"] == "fix login"


# --- incremental index -----------------------------------------------------------
class TestIncrementalIndex:
    def test_first_refresh_indexes_all(self, project: Path):
        store = SeedcodeStore(project)
        result = store.refresh_index()
        assert result["indexed"] == 2  # src/auth.py + README.md
        assert result["unchanged"] == 0
        file_map = store.load_file_map()
        assert "src/auth.py" in file_map
        assert "login" in file_map["src/auth.py"]["symbols"]

    def test_second_refresh_is_incremental(self, project: Path):
        store = SeedcodeStore(project)
        first = store.refresh_index()
        second = store.refresh_index()
        assert second["unchanged"] == first["indexed"]
        assert second["indexed"] == 0

    def test_only_changed_files_reindex(self, project: Path):
        store = SeedcodeStore(project)
        store.refresh_index()
        (project / "src" / "auth.py").write_text(
            "def logout():\n    pass\n", encoding="utf-8"
        )
        result = store.refresh_index()
        assert result["indexed"] == 1
        assert result["unchanged"] == 1
        symbols = store.load_file_map()["src/auth.py"]["symbols"]
        assert any("logout" in s for s in symbols)

    def test_new_file_is_picked_up(self, project: Path):
        store = SeedcodeStore(project)
        store.refresh_index()
        (project / "src" / "extra.py").write_text("x = 1\n", encoding="utf-8")
        result = store.refresh_index()
        assert result["indexed"] == 1
        assert "src/extra.py" in store.load_file_map()

    def test_seedcode_itself_is_never_indexed(self, project: Path):
        store = SeedcodeStore(project)
        store.save_memory("note", "internal")
        store.refresh_index()
        assert not any(
            rel.startswith(SEEDCODE_DIRNAME) for rel in store.load_file_map()
        )

    def test_index_skips_caches_and_venvs(self, project: Path):
        (project / "node_modules" / "pkg").mkdir(parents=True)
        (project / "node_modules" / "pkg" / "lib.js").write_text("x", encoding="utf-8")
        (project / ".venv").mkdir()
        (project / ".venv" / "site.py").write_text("x", encoding="utf-8")
        store = SeedcodeStore(project)
        result = store.refresh_index()
        assert result["indexed"] == 2  # only the real project files
        file_map = store.load_file_map()
        assert not any("node_modules" in rel for rel in file_map)

    def test_query_index_matches_paths_and_symbols(self, project: Path):
        store = SeedcodeStore(project)
        store.refresh_index()
        hits = store.query_index("auth")
        assert hits and hits[0]["path"] == "src/auth.py"
        assert store.query_index("login")  # symbol search
        assert store.query_index("zzz-nonexistent") == []


# --- secret guard -----------------------------------------------------------------
class TestSecretGuard:
    def test_flat_secret_key_rejected(self):
        with pytest.raises(ValueError):
            assert_no_secrets({"api_key": "x"})

    def test_nested_secret_key_rejected(self):
        with pytest.raises(ValueError):
            assert_no_secrets({"nested": {"password": "x"}})

    def test_clean_data_passes(self):
        assert_no_secrets({"goal": "fix", "files": ["a.py"]})


# --- session state ------------------------------------------------------------------
class TestCodeModeState:
    def test_enable_sets_workspace_and_index(self, project: Path):
        state = enable(project)
        assert state.enabled
        assert state.workspace == project.resolve()
        assert state.store is not None and state.store.exists
        assert state.last_index["indexed"] >= 1

    def test_status_lines_shape(self, project: Path):
        state = enable(project)
        lines = state.status_lines()
        assert lines[0] == "Code Mode: ON"
        assert str(project.resolve()) in lines[1]
        assert any("Memory" in line for line in lines)
        assert any("Index" in line for line in lines)

    def test_disable_keeps_state_on_disk(self, project: Path):
        enable(project)
        disable()
        assert not codemode_state().enabled
        assert (project / SEEDCODE_DIRNAME / "config.json").is_file()


# --- workspace tool exclusion ---------------------------------------------------------
class TestWorkspaceExclusion:
    def test_search_and_index_skip_seedcode(self, project: Path, monkeypatch):
        from seedcode.tools import PermissionManager, get_tool

        store = SeedcodeStore(project)
        store.save_memory("secret-note", "findme-internal-marker")
        perm = PermissionManager(workspace=project)

        search = get_tool("search_text").run(
            perm, {"pattern": "findme-internal-marker"}
        )
        assert "No matches" in search.output

        index = get_tool("project_index").run(perm, {})
        assert ".seedcode" not in index.output
