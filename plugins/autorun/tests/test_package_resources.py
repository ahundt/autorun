"""Test package resource access for installed vs source autorun.

Test Coverage:
- get_plugin_root() returns valid directory with .claude-plugin/
- get_commands_dir() returns directory with known command files
- get_skills_dir(), get_agents_dir(), get_hooks_dir() return valid directories
- Resource access works from both source and installed package locations

TDD Methodology:
- RED: These tests define the expected API for resources.py
- GREEN: Implement resources.py to make tests pass
- REFACTOR: Clean up while keeping tests green
"""
from pathlib import Path
import pytest


class TestGetPluginRoot:
    """Test get_plugin_root() locates plugin directory."""

    def test_get_plugin_root_returns_path(self):
        """Test: get_plugin_root() returns a Path object."""
        from autorun.resources import get_plugin_root

        root = get_plugin_root()
        assert isinstance(root, Path)

    def test_get_plugin_root_directory_exists(self):
        """Test: get_plugin_root() returns an existing directory."""
        from autorun.resources import get_plugin_root

        root = get_plugin_root()
        assert root.exists(), f"Plugin root does not exist: {root}"
        assert root.is_dir(), f"Plugin root is not a directory: {root}"

    def test_get_plugin_root_has_claude_plugin(self):
        """Test: get_plugin_root() returns directory containing .claude-plugin/."""
        from autorun.resources import get_plugin_root

        root = get_plugin_root()
        claude_plugin = root / ".claude-plugin"
        assert claude_plugin.exists(), f".claude-plugin not found in {root}"

    def test_get_plugin_root_has_marketplace_json(self):
        """Test: get_plugin_root() returns directory with marketplace.json."""
        from autorun.resources import get_plugin_root

        root = get_plugin_root()
        marketplace = root / ".claude-plugin" / "marketplace.json"
        assert marketplace.exists(), f"marketplace.json not found in {root}"


class TestGetCommandsDir:
    """Test get_commands_dir() returns commands directory."""

    def test_get_commands_dir_returns_path(self):
        """Test: get_commands_dir() returns a Path object."""
        from autorun.resources import get_commands_dir

        commands_dir = get_commands_dir()
        assert isinstance(commands_dir, Path)

    def test_get_commands_dir_exists(self):
        """Test: get_commands_dir() returns existing directory."""
        from autorun.resources import get_commands_dir

        commands_dir = get_commands_dir()
        assert commands_dir.exists(), f"Commands directory not found: {commands_dir}"

    def test_get_commands_dir_has_known_commands(self):
        """Test: commands/ contains known command files."""
        from autorun.resources import get_commands_dir

        commands_dir = get_commands_dir()
        # These are core commands that should always exist
        expected_commands = ["go.md", "st.md", "sos.md"]
        for cmd in expected_commands:
            assert (commands_dir / cmd).exists(), f"Command file missing: {cmd}"


class TestGetSkillsDir:
    """Test get_skills_dir() returns skills directory."""

    def test_get_skills_dir_returns_path(self):
        """Test: get_skills_dir() returns a Path object."""
        from autorun.resources import get_skills_dir

        skills_dir = get_skills_dir()
        assert isinstance(skills_dir, Path)

    def test_get_skills_dir_exists(self):
        """Test: get_skills_dir() returns existing directory."""
        from autorun.resources import get_skills_dir

        skills_dir = get_skills_dir()
        assert skills_dir.exists(), f"Skills directory not found: {skills_dir}"


class TestGetAgentsDir:
    """Test get_agents_dir() returns agents directory."""

    def test_get_agents_dir_returns_path(self):
        """Test: get_agents_dir() returns a Path object."""
        from autorun.resources import get_agents_dir

        agents_dir = get_agents_dir()
        assert isinstance(agents_dir, Path)

    def test_get_agents_dir_exists(self):
        """Test: get_agents_dir() returns existing directory."""
        from autorun.resources import get_agents_dir

        agents_dir = get_agents_dir()
        assert agents_dir.exists(), f"Agents directory not found: {agents_dir}"


class TestGetHooksDir:
    """Test get_hooks_dir() returns hooks directory."""

    def test_get_hooks_dir_returns_path(self):
        """Test: get_hooks_dir() returns a Path object."""
        from autorun.resources import get_hooks_dir

        hooks_dir = get_hooks_dir()
        assert isinstance(hooks_dir, Path)

    def test_get_hooks_dir_exists(self):
        """Test: get_hooks_dir() returns existing directory."""
        from autorun.resources import get_hooks_dir

        hooks_dir = get_hooks_dir()
        assert hooks_dir.exists(), f"Hooks directory not found: {hooks_dir}"

    def test_get_hooks_dir_has_hook_files(self):
        """Test: hooks/ contains hook configuration files."""
        from autorun.resources import get_hooks_dir

        hooks_dir = get_hooks_dir()
        # hooks.json is the primary hook configuration for Claude Code
        assert (hooks_dir / "claude-hooks.json").exists(), "claude-hooks.json not found"
        assert (hooks_dir / "hook_entry.py").exists(), "hook_entry.py not found"


class TestAllResourcesAccessible:
    """Test that all plugin resources are accessible together."""

    def test_all_resource_dirs_under_same_root(self):
        """Test: All resource directories share the same parent plugin root."""
        from autorun.resources import (
            get_plugin_root,
            get_commands_dir,
            get_skills_dir,
            get_agents_dir,
            get_hooks_dir,
        )

        root = get_plugin_root()
        # All resource dirs should be subdirectories of the plugin root
        assert get_commands_dir().parent == root
        assert get_skills_dir().parent == root
        assert get_agents_dir().parent == root
        assert get_hooks_dir().parent == root
