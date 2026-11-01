"""Tests for the unified hierarchical permission model and config migration."""

from __future__ import annotations

import pytest

from seedcode.core.models import AppConfig
from seedcode.tools.permissions import (
    PermissionError_,
    PermissionLevel,
    PermissionManager,
    PermissionMode,
)


# --- hierarchy ----------------------------------------------------------------
class TestHierarchy:
    def test_levels_are_ordered(self):
        assert PermissionLevel.READ_ONLY < PermissionLevel.WORKSPACE < PermissionLevel.FULL_SYSTEM

    def test_legacy_alias_full_access(self):
        # Old code/config said FULL_ACCESS; it is the same level as FULL_SYSTEM.
        assert PermissionLevel.FULL_ACCESS == PermissionLevel.FULL_SYSTEM
        assert PermissionMode is PermissionLevel  # one enum, two names

    def test_parse_aliases(self):
        assert PermissionLevel.parse("read_only") is PermissionLevel.READ_ONLY
        assert PermissionLevel.parse("workspace") is PermissionLevel.WORKSPACE
        assert PermissionLevel.parse("full_system") == PermissionLevel.FULL_SYSTEM
        assert PermissionLevel.parse("full_access") == PermissionLevel.FULL_SYSTEM
        assert PermissionLevel.parse(PermissionLevel.FULL_SYSTEM) is PermissionLevel.FULL_SYSTEM
        with pytest.raises(ValueError):
            PermissionLevel.parse("supreme")

    def test_the_desktop_level_is_gone(self):
        """Desktop control was removed in v9.2.0 — the level no longer exists."""
        assert not hasattr(PermissionLevel, "DESKTOP")
        with pytest.raises(ValueError):
            PermissionLevel.parse("desktop")

    def test_value_str_and_labels(self):
        assert PermissionLevel.WORKSPACE.value_str == "workspace"
        assert PermissionLevel.FULL_SYSTEM.value_str == "full_system"
        assert PermissionLevel.FULL_SYSTEM.label == "Full System"


# --- require() ----------------------------------------------------------------
class TestRequire:
    def test_sufficient_level_passes(self, tmp_path):
        perm = PermissionManager(workspace=tmp_path, level=PermissionLevel.FULL_SYSTEM)
        perm.require(PermissionLevel.WORKSPACE)
        perm.require(PermissionLevel.FULL_SYSTEM)
        perm.require("full_system", "write outside the workspace")

    def test_insufficient_level_blocks_with_hint(self, tmp_path):
        perm = PermissionManager(workspace=tmp_path, level=PermissionLevel.WORKSPACE)
        with pytest.raises(PermissionError_, match="Full System"):
            perm.require(PermissionLevel.FULL_SYSTEM)

    def test_legacy_mode_kwarg_still_works(self, tmp_path):
        perm = PermissionManager(workspace=tmp_path, mode=PermissionMode.WORKSPACE)
        assert perm.level is PermissionLevel.WORKSPACE
        assert perm.mode is PermissionLevel.WORKSPACE  # alias property

    def test_write_gates_by_level(self, tmp_path, tmp_path_factory):
        outside = tmp_path_factory.mktemp("elsewhere") / "x.txt"
        read_only = PermissionManager(workspace=tmp_path, level=PermissionLevel.READ_ONLY)
        with pytest.raises(PermissionError_):
            read_only.check_write(tmp_path / "a.txt")
        ws = PermissionManager(workspace=tmp_path, level=PermissionLevel.WORKSPACE)
        ws.check_write(tmp_path / "a.txt")  # inside: fine
        with pytest.raises(PermissionError_):
            ws.check_write(outside)  # outside needs full_system
        full = PermissionManager(workspace=tmp_path, level=PermissionLevel.FULL_SYSTEM)
        full.check_write(outside)  # full_system may write anywhere


# --- config migration ---------------------------------------------------------
class TestConfigMigration:
    def test_legacy_full_access_renames(self):
        config = AppConfig.model_validate({"permission_mode": "full_access"})
        assert config.permission_mode == "full_system"

    def test_legacy_desktop_permission_normalizes_to_workspace(self):
        """A legacy "desktop" level keeps its file/command capability."""
        config = AppConfig.model_validate({"permission_mode": "desktop"})
        assert config.permission_mode == "workspace"

    def test_legacy_desktop_mode_flag_is_ignored(self):
        config = AppConfig.model_validate(
            {"permission_mode": "workspace", "desktop_mode": True}
        )
        assert config.permission_mode == "workspace"
        assert not hasattr(config, "desktop_mode")

    def test_legacy_desktop_mode_with_full_access(self):
        config = AppConfig.model_validate(
            {"permission_mode": "full_access", "desktop_mode": True}
        )
        assert config.permission_mode == "full_system"

    def test_desktop_mode_dropped_from_serialised_config(self):
        config = AppConfig.model_validate({"desktop_mode": True})
        dumped = config.model_dump()
        assert "desktop_mode" not in dumped
        # And the migrated shape round-trips unchanged.
        assert AppConfig.model_validate(dumped).permission_mode == config.permission_mode
