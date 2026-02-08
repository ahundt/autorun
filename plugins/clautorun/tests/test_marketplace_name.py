#!/usr/bin/env python3
"""
Tests for marketplace naming consistency.

These tests prevent regression of the clautorun-dev vs clautorun marketplace name bug
that caused the UV installer to fail with "Plugin not found in marketplace 'clautorun-dev'".

Bug details:
- The marketplace.json correctly had "name": "clautorun"
- But clautorun_marketplace/__init__.py was using "clautorun-dev" for plugin install commands
- This caused all plugin installations to fail silently

v0.7.0 Update:
- clautorun_marketplace/__init__.py has been replaced by clautorun/install_plugins.py
- Tests now verify the new unified installation module
"""

import json
import re
from pathlib import Path

import pytest


def get_repo_root() -> Path:
    """Get the repository root directory."""
    # This file is at plugins/clautorun/tests/test_marketplace_name.py
    # Repo root is 3 levels up
    return Path(__file__).parent.parent.parent.parent


def get_plugin_root() -> Path:
    """Get the clautorun plugin root directory."""
    # This file is at plugins/clautorun/tests/test_marketplace_name.py
    # Plugin root is 1 level up
    return Path(__file__).parent.parent


class TestMarketplaceName:
    """Tests to ensure marketplace name is consistently 'clautorun' not 'clautorun-dev'."""

    def test_marketplace_json_has_correct_name(self):
        """Verify marketplace.json uses 'clautorun' as the marketplace name."""
        repo_root = get_repo_root()
        marketplace_json = repo_root / ".claude-plugin" / "marketplace.json"

        assert marketplace_json.exists(), f"marketplace.json not found at {marketplace_json}"

        with open(marketplace_json) as f:
            data = json.load(f)

        assert data.get("name") == "clautorun", (
            f"marketplace.json should have name='clautorun', got '{data.get('name')}'"
        )

    def test_install_plugins_uses_correct_name(self):
        """Verify install_plugins.py uses @clautorun not @clautorun-dev."""
        plugin_root = get_plugin_root()
        install_file = plugin_root / "src" / "clautorun" / "install_plugins.py"

        assert install_file.exists(), f"install_plugins.py not found at {install_file}"

        content = install_file.read_text()

        # Should NOT contain clautorun-dev
        assert "clautorun-dev" not in content, (
            "install_plugins.py should NOT contain 'clautorun-dev'. "
            "This bug causes plugin installation to fail."
        )

        # Should contain @clautorun for plugin installs (the pattern for plugin install commands)
        assert "@clautorun" in content or '@{MARKETPLACE}' in content, (
            "install_plugins.py should contain '@clautorun' or '@{MARKETPLACE}' for plugin installs"
        )

        # Verify the MARKETPLACE constant is correct
        assert 'MARKETPLACE = "clautorun"' in content, (
            "install_plugins.py should have MARKETPLACE = \"clautorun\""
        )

    def test_known_marketplaces_uses_correct_name(self):
        """Verify known_marketplaces.json uses 'clautorun' not 'clautorun-dev'."""
        repo_root = get_repo_root()
        known_marketplaces = repo_root / "plugins" / "known_marketplaces.json"

        assert known_marketplaces.exists(), f"known_marketplaces.json not found at {known_marketplaces}"

        with open(known_marketplaces) as f:
            data = json.load(f)

        # Check all marketplace entries
        for marketplace in data.get("marketplaces", []):
            name = marketplace.get("name", "")
            assert "clautorun-dev" not in name, (
                f"known_marketplaces.json should NOT contain 'clautorun-dev', found: {name}"
            )

    def test_readme_does_not_reference_clautorun_dev(self):
        """Verify README.md does not reference clautorun-dev (except in notes/)."""
        repo_root = get_repo_root()
        readme = repo_root / "README.md"

        assert readme.exists(), f"README.md not found at {readme}"

        content = readme.read_text()

        # Count occurrences of clautorun-dev
        matches = re.findall(r"clautorun-dev", content)

        assert len(matches) == 0, (
            f"README.md should NOT contain 'clautorun-dev'. Found {len(matches)} occurrences. "
            "This causes confusion about the correct marketplace name."
        )

    def test_skills_files_use_correct_marketplace_name(self):
        """Verify skills files use @clautorun not @clautorun-dev."""
        plugin_root = get_plugin_root()
        skills_dir = plugin_root / "skills"

        if not skills_dir.exists():
            pytest.skip("Skills directory not found")

        for skill_file in skills_dir.glob("*.md"):
            content = skill_file.read_text()

            # Should NOT contain @clautorun-dev
            if "@clautorun-dev" in content:
                pytest.fail(
                    f"{skill_file.name} contains '@clautorun-dev'. "
                    "Should use '@clautorun' instead."
                )


class TestInstallPluginsExitCode:
    """Tests for install_plugins exit code behavior."""

    def test_install_plugins_returns_integer_exit_code(self):
        """Verify install_plugins() returns integer exit code, not boolean.

        Bug: sys.exit(True) == sys.exit(1), so returning True on success
        causes exit code 1 instead of 0.
        """
        plugin_root = get_plugin_root()
        install_file = plugin_root / "src" / "clautorun" / "install_plugins.py"

        content = install_file.read_text()

        # Should return 0 on success, not True
        assert "return 0 if" in content, (
            "install_plugins() should return exit code (0/1), not boolean. "
            "sys.exit(True) == sys.exit(1), which is incorrect for success."
        )

        # Should NOT return boolean directly
        assert "return success_count == len(plugins)" not in content, (
            "install_plugins() should NOT return boolean. "
            "Use 'return 0 if ... else 1' instead."
        )

    def test_main_entry_point_returns_integer_exit_code(self):
        """Verify __main__.py returns integer exit codes."""
        plugin_root = get_plugin_root()
        main_file = plugin_root / "src" / "clautorun" / "__main__.py"

        content = main_file.read_text()

        # Should have return 0 and return 1 for exit codes
        assert "return 0" in content, (
            "__main__.py should return integer exit codes"
        )


class TestInstallPluginsPrintMessages:
    """Tests for correct marketplace name in print messages."""

    def test_print_messages_use_correct_name(self):
        """Verify print messages reference 'clautorun' not 'clautorun-dev'."""
        plugin_root = get_plugin_root()
        install_file = plugin_root / "src" / "clautorun" / "install_plugins.py"

        content = install_file.read_text()

        # Check for correct print messages
        expected_patterns = [
            "clautorun marketplace",  # Should mention clautorun marketplace
            "clautorun",  # Should use clautorun name
        ]

        for pattern in expected_patterns:
            assert pattern in content, (
                f"Expected content containing '{pattern}' in install_plugins.py"
            )

        # Should NOT have clautorun-dev in print messages
        assert "clautorun-dev" not in content, (
            "install_plugins.py should NOT contain 'clautorun-dev' in any messages"
        )


class TestPluginNameEnum:
    """Tests for PluginName enum validation."""

    def test_plugin_name_enum_exists(self):
        """Verify PluginName enum is defined."""
        plugin_root = get_plugin_root()
        install_file = plugin_root / "src" / "clautorun" / "install_plugins.py"

        content = install_file.read_text()

        assert "class PluginName" in content, (
            "install_plugins.py should define PluginName enum"
        )

    def test_plugin_name_enum_has_all_plugins(self):
        """Verify PluginName enum includes all marketplace plugins."""
        plugin_root = get_plugin_root()
        install_file = plugin_root / "src" / "clautorun" / "install_plugins.py"

        content = install_file.read_text()

        # plan-export merged into clautorun in v0.7.0
        expected_plugins = ["clautorun", "pdf-extractor"]
        for plugin in expected_plugins:
            # Check that the plugin name appears in the enum definition
            assert plugin in content, (
                f"PluginName enum should include '{plugin}'"
            )

        # Verify plan-export is NOT in the enum (merged into clautorun)
        # Check the enum definition specifically to avoid matching comments
        enum_match = re.search(r'class PluginName.*?(?=\n\n|\nclass |\ndef )', content, re.DOTALL)
        if enum_match:
            enum_content = enum_match.group(0)
            assert 'PLAN_EXPORT' not in enum_content, (
                "PluginName enum should NOT include PLAN_EXPORT (merged into clautorun)"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
