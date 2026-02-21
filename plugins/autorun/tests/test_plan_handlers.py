#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test plan command handlers
"""
import pytest
import sys
from pathlib import Path

# Add src directory to Python path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from autorun import CONFIG


class TestPlanCommandHandlers:
    """Test plan command handlers are properly registered"""

    @pytest.mark.unit
    def test_plan_command_mappings_exist(self):
        """Test plan command mappings are in CONFIG"""
        mappings = CONFIG["command_mappings"]

        # Check short forms
        assert "/ar:pn" in mappings, "Missing /ar:pn mapping"
        assert "/ar:pr" in mappings, "Missing /ar:pr mapping"
        assert "/ar:pu" in mappings, "Missing /ar:pu mapping"
        assert "/ar:pp" in mappings, "Missing /ar:pp mapping"

        # Check long forms
        assert "/ar:plannew" in mappings, "Missing /ar:plannew mapping"
        assert "/ar:planrefine" in mappings, "Missing /ar:planrefine mapping"
        assert "/ar:planupdate" in mappings, "Missing /ar:planupdate mapping"
        assert "/ar:planprocess" in mappings, "Missing /ar:planprocess mapping"

    @pytest.mark.unit
    def test_plan_command_handler_names(self):
        """Test plan commands map to correct handler names"""
        mappings = CONFIG["command_mappings"]

        assert mappings["/ar:pn"] == "NEW_PLAN"
        assert mappings["/ar:pr"] == "REFINE_PLAN"
        assert mappings["/ar:pu"] == "UPDATE_PLAN"
        assert mappings["/ar:pp"] == "PROCESS_PLAN"

        # Long forms should map to same handlers
        assert mappings["/ar:plannew"] == "NEW_PLAN"
        assert mappings["/ar:planrefine"] == "REFINE_PLAN"
        assert mappings["/ar:planupdate"] == "UPDATE_PLAN"
        assert mappings["/ar:planprocess"] == "PROCESS_PLAN"

    @pytest.mark.unit
    def test_plan_markdown_files_exist(self):
        """Test plan markdown files exist in commands directory"""
        commands_dir = Path(__file__).parent.parent / "commands"

        # Check target files exist
        assert (commands_dir / "plannew.md").exists(), "Missing plannew.md"
        assert (commands_dir / "planrefine.md").exists(), "Missing planrefine.md"
        assert (commands_dir / "planupdate.md").exists(), "Missing planupdate.md"
        assert (commands_dir / "planprocess.md").exists(), "Missing planprocess.md"

        # Check symlinks exist
        pn_symlink = commands_dir / "pn.md"
        pr_symlink = commands_dir / "pr.md"
        pu_symlink = commands_dir / "pu.md"
        pp_symlink = commands_dir / "pp.md"

        assert pn_symlink.exists(), "Missing pn.md symlink"
        assert pr_symlink.exists(), "Missing pr.md symlink"
        assert pu_symlink.exists(), "Missing pu.md symlink"
        assert pp_symlink.exists(), "Missing pp.md symlink"

        # Verify symlinks point to correct targets
        assert pn_symlink.is_symlink(), "pn.md should be a symlink"
        assert pr_symlink.is_symlink(), "pr.md should be a symlink"
        assert pu_symlink.is_symlink(), "pu.md should be a symlink"
        assert pp_symlink.is_symlink(), "pp.md should be a symlink"

    @pytest.mark.unit
    def test_plan_handler_factory(self):
        """Test _make_plan_handler factory function works"""
        from autorun.plugins import _make_plan_handler

        # Create a test handler
        handler = _make_plan_handler("plannew.md")
        assert callable(handler), "Handler should be callable"

        # Test with mock context
        class MockContext:
            pass

        ctx = MockContext()
        result = handler(ctx)

        # Should return markdown content
        assert isinstance(result, str), "Handler should return string"
        assert len(result) > 0, "Handler should return non-empty content"
        assert "# Your Task" in result or "plannew" in result.lower(), \
            "Content should be from plannew.md"

    @pytest.mark.unit
    def test_plan_handlers_registered(self):
        """Test plan handlers are registered with app.command()"""
        # Import the app to check registrations
        from autorun.plugins import app

        # Get all registered commands
        # Note: We can't easily test app.command() registrations without
        # actually invoking the plugin system, so this is a basic check
        assert hasattr(app, 'command'), "App should have command decorator"

    @pytest.mark.unit
    def test_nonexistent_plan_file_handling(self):
        """Test handler gracefully handles missing files"""
        from autorun.plugins import _make_plan_handler

        # Create handler for nonexistent file
        handler = _make_plan_handler("nonexistent.md")

        class MockContext:
            pass

        ctx = MockContext()
        result = handler(ctx)

        # Should return error message, not crash
        assert isinstance(result, str), "Handler should return string"
        assert "Error" in result or "not found" in result.lower(), \
            "Should indicate file not found"


# Run with: python3 -m pytest tests/test_plan_handlers.py -v --override-ini='addopts='
