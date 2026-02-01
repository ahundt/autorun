#!/usr/bin/env python3
"""
Tests for marketplace naming consistency.

These tests prevent regression of the clautorun-dev vs clautorun marketplace name bug
that caused the UV installer to fail with "Plugin not found in marketplace 'clautorun-dev'".

Bug details:
- The marketplace.json correctly had "name": "clautorun"
- But clautorun_marketplace/__init__.py was using "clautorun-dev" for plugin install commands
- This caused all plugin installations to fail silently
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

    def test_marketplace_installer_uses_correct_name(self):
        """Verify clautorun_marketplace/__init__.py uses @clautorun not @clautorun-dev."""
        repo_root = get_repo_root()
        init_file = repo_root / "src" / "clautorun_marketplace" / "__init__.py"

        assert init_file.exists(), f"clautorun_marketplace/__init__.py not found at {init_file}"

        content = init_file.read_text()

        # Should NOT contain clautorun-dev
        assert "clautorun-dev" not in content, (
            "clautorun_marketplace/__init__.py should NOT contain 'clautorun-dev'. "
            "This bug causes plugin installation to fail."
        )

        # Should contain @clautorun for plugin installs
        assert "@clautorun" in content, (
            "clautorun_marketplace/__init__.py should contain '@clautorun' for plugin installs"
        )

        # Verify the specific plugin install pattern
        assert 'f"{plugin_name}@clautorun"' in content, (
            "clautorun_marketplace/__init__.py should use f\"{plugin_name}@clautorun\" pattern"
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
        repo_root = get_repo_root()
        skills_dir = repo_root / "plugins" / "clautorun" / "skills"

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


class TestMarketplaceInstallerExitCode:
    """Tests for marketplace installer exit code behavior."""

    def test_main_returns_exit_code_not_boolean(self):
        """Verify main() returns integer exit code, not boolean.

        Bug: sys.exit(True) == sys.exit(1), so returning True on success
        causes exit code 1 instead of 0.
        """
        repo_root = get_repo_root()
        init_file = repo_root / "src" / "clautorun_marketplace" / "__init__.py"

        content = init_file.read_text()

        # Should return 0 on success, not True
        assert "return 0 if success_count == len(plugins) else 1" in content, (
            "main() should return exit code (0/1), not boolean. "
            "sys.exit(True) == sys.exit(1), which is incorrect for success."
        )

        # Should NOT return boolean
        assert "return success_count == len(plugins)" not in content, (
            "main() should NOT return boolean. "
            "Use 'return 0 if success_count == len(plugins) else 1' instead."
        )


class TestMarketplaceInstallerPrintMessages:
    """Tests for correct marketplace name in print messages."""

    def test_print_messages_use_correct_name(self):
        """Verify print messages reference 'clautorun' not 'clautorun-dev'."""
        repo_root = get_repo_root()
        init_file = repo_root / "src" / "clautorun_marketplace" / "__init__.py"

        content = init_file.read_text()

        # Check for correct print messages
        expected_messages = [
            "Adding clautorun marketplace",
            "Added clautorun marketplace",
            "clautorun marketplace already exists",
        ]

        for msg in expected_messages:
            assert msg in content or msg.replace("clautorun", "clautorun") in content, (
                f"Expected print message containing '{msg}' in marketplace installer"
            )

        # Should NOT have clautorun-dev in print messages
        assert "Adding clautorun-dev" not in content
        assert "Added clautorun-dev" not in content
        assert "clautorun-dev marketplace" not in content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
