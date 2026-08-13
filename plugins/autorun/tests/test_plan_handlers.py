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
    def test_plan_procedures_live_in_skills_with_short_spellings_intact(self):
        """The four plan procedures are skills; the short spellings still work.

        They used to be command documents with pn/pr/pu/pp symlinked onto them
        so the two spellings could not drift. The procedure now lives once in
        `skills/<name>/SKILL.md`, which every harness reading ~/.agents/skills
        can load, and each short document points at it.
        """
        plugin_root = Path(__file__).parent.parent
        commands_dir = plugin_root / "commands"
        skills_dir = plugin_root / "skills"

        for name, short in (
            ("plannew", "pn"),
            ("planrefine", "pr"),
            ("planupdate", "pu"),
            ("planprocess", "pp"),
        ):
            skill = skills_dir / name / "SKILL.md"
            assert skill.is_file(), f"Missing skills/{name}/SKILL.md"

            pointer = commands_dir / f"{short}.md"
            assert pointer.is_file(), f"Missing commands/{short}.md"
            text = pointer.read_text(encoding="utf-8")
            assert name in text, f"{short}.md does not name the {name} skill"
            assert len(text.splitlines()) < 25, f"{short}.md grew a second copy"

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
    def test_planprocess_handler_activates_three_stage_execution_and_keeps_path(self):
        from autorun.core import EventContext, ThreadSafeDB
        from autorun.plugins import _make_plan_handler

        ctx = EventContext(
            session_id="planprocess-activation",
            event="UserPromptSubmit",
            prompt="ar:pp notes/approved-plan.md",
            store=ThreadSafeDB(),
            cli_type="pi",
        )
        ctx.activation_prompt = ctx.prompt

        result = _make_plan_handler("planprocess")(ctx)

        assert ctx.plan_active is True
        assert ctx.plan_arguments == "notes/approved-plan.md"
        assert ctx.autorun_active is True
        assert ctx.autorun_stage == EventContext.STAGE_1
        assert ctx.autorun_task == "Execute plan notes/approved-plan.md"
        assert ctx.plan_awaiting_execution_tasks is True
        assert ctx.plan_awaiting_planning_tasks is False
        assert "Development Planning" in result

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
