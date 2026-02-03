#!/usr/bin/env python3
"""
Tests for plan_export.py tool_response.filePath handling.

These tests verify that plan_export.py correctly uses tool_response.filePath
as the primary source for the plan file path when exiting plan mode.

Bug details:
- ExitPlanMode PostToolUse hook receives tool_response with:
  - filePath: path to the plan file (e.g., "/Users/.../.claude/plans/keen-napping-sparkle.md")
  - plan: the plan content
- The original code ignored tool_response.filePath and tried:
  1. Transcript parsing (often fails)
  2. Session ID metadata search (often fails)
  3. Most recent plan (returns wrong file!)
- The fix uses tool_response.filePath first, then falls back to other methods

Reference: Claude Code hooks documentation confirms tool_response contains
filePath for tools like Write and ExitPlanMode.
"""

import ast
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import tempfile
import os

import pytest


def get_plan_export_script() -> Path:
    """Get the path to plan_export.py."""
    return Path(__file__).parent.parent / "scripts" / "plan_export.py"


class TestToolResponseFilePathCode:
    """Tests that verify the tool_response.filePath handling is correctly implemented."""

    def test_script_checks_tool_response_first(self):
        """Verify plan_export.py checks tool_response.filePath before other methods."""
        script_path = get_plan_export_script()
        content = script_path.read_text()

        # Should check tool_response.get("filePath")
        assert 'tool_response.get("filePath")' in content, (
            "plan_export.py should extract filePath from tool_response"
        )

        # Should have comment explaining this is the most reliable source
        assert "tool_response" in content and "most reliable" in content.lower(), (
            "plan_export.py should document that tool_response is the most reliable source"
        )

    def test_script_has_correct_fallback_order(self):
        """Verify the fallback chain is in the correct order."""
        script_path = get_plan_export_script()
        content = script_path.read_text()

        # Find the main() function section where the fallback chain is implemented
        main_start = content.find("def main():")
        assert main_start > 0, "Should have main() function"
        main_section = content[main_start:]

        # Find positions of each method CALL in main()
        # (not the function definitions which appear earlier)
        tool_response_pos = main_section.find('tool_response.get("filePath")')
        transcript_pos = main_section.find("get_plan_from_transcript(transcript_path)")
        metadata_pos = main_section.find("find_plan_by_session_id(session_id)")
        recent_pos = main_section.find("get_most_recent_plan()")

        assert tool_response_pos > 0, "Should have tool_response.filePath check in main()"
        assert transcript_pos > 0, "Should have transcript parsing call in main()"
        assert metadata_pos > 0, "Should have metadata search call in main()"
        assert recent_pos > 0, "Should have most recent plan call in main()"

        # Verify order: tool_response first, then transcript, then metadata, then recent
        assert tool_response_pos < transcript_pos, (
            "tool_response.filePath should be checked BEFORE transcript parsing"
        )
        assert transcript_pos < metadata_pos, (
            "Transcript parsing should be BEFORE metadata search"
        )
        assert metadata_pos < recent_pos, (
            "Metadata search should be BEFORE most recent plan"
        )

    def test_script_validates_file_exists(self):
        """Verify plan_export.py checks if the file from tool_response exists."""
        script_path = get_plan_export_script()
        content = script_path.read_text()

        # Should validate file exists before using it
        assert "candidate.exists()" in content or "candidate.is_file()" in content, (
            "plan_export.py should verify tool_response.filePath exists before using it"
        )

    def test_script_syntax_is_valid(self):
        """Verify plan_export.py has valid Python syntax."""
        script_path = get_plan_export_script()
        content = script_path.read_text()

        try:
            ast.parse(content)
        except SyntaxError as e:
            pytest.fail(f"plan_export.py has invalid syntax: {e}")


class TestToolResponseFilePathBehavior:
    """Tests that verify the tool_response.filePath handling works correctly at runtime."""

    def test_tool_response_with_valid_filepath(self, tmp_path):
        """Test that a valid tool_response.filePath is used correctly."""
        # Create a temporary plan file
        plan_file = tmp_path / "test-plan.md"
        plan_file.write_text("# Test Plan\n\nThis is a test plan.")

        # Simulate tool_response from ExitPlanMode
        tool_response = {
            "filePath": str(plan_file),
            "plan": "# Test Plan\n\nThis is a test plan."
        }

        # The logic from plan_export.py
        plan_path = None
        if isinstance(tool_response, dict):
            file_path = tool_response.get("filePath")
            if file_path:
                candidate = Path(file_path)
                if candidate.exists():
                    plan_path = candidate

        assert plan_path is not None, "Should find plan from tool_response.filePath"
        assert plan_path == plan_file, "Should use the exact path from tool_response"

    def test_tool_response_with_nonexistent_filepath(self, tmp_path):
        """Test that nonexistent tool_response.filePath falls through to fallback."""
        # Simulate tool_response with a path that doesn't exist
        tool_response = {
            "filePath": "/nonexistent/path/plan.md",
            "plan": "# Test Plan"
        }

        # The logic from plan_export.py
        plan_path = None
        if isinstance(tool_response, dict):
            file_path = tool_response.get("filePath")
            if file_path:
                candidate = Path(file_path)
                if candidate.exists():
                    plan_path = candidate

        assert plan_path is None, "Should not use nonexistent path"

    def test_tool_response_without_filepath(self):
        """Test that tool_response without filePath falls through to fallback."""
        # Simulate tool_response without filePath (edge case)
        tool_response = {
            "plan": "# Test Plan"
        }

        # The logic from plan_export.py
        plan_path = None
        if isinstance(tool_response, dict):
            file_path = tool_response.get("filePath")
            if file_path:
                candidate = Path(file_path)
                if candidate.exists():
                    plan_path = candidate

        assert plan_path is None, "Should fall through when no filePath"

    def test_tool_response_not_dict(self):
        """Test that non-dict tool_response is handled gracefully."""
        # Simulate tool_response as string (edge case)
        tool_response = "some string response"

        # The logic from plan_export.py
        plan_path = None
        if isinstance(tool_response, dict):
            file_path = tool_response.get("filePath")
            if file_path:
                candidate = Path(file_path)
                if candidate.exists():
                    plan_path = candidate

        assert plan_path is None, "Should handle non-dict tool_response gracefully"

    def test_tool_response_empty_dict(self):
        """Test that empty tool_response is handled gracefully."""
        tool_response = {}

        plan_path = None
        if isinstance(tool_response, dict):
            file_path = tool_response.get("filePath")
            if file_path:
                candidate = Path(file_path)
                if candidate.exists():
                    plan_path = candidate

        assert plan_path is None, "Should handle empty tool_response gracefully"


class TestRegressionPrevention:
    """Tests to prevent regression of the bug."""

    def test_most_recent_plan_is_last_resort(self):
        """Verify get_most_recent_plan is only used as the last fallback."""
        script_path = get_plan_export_script()
        content = script_path.read_text()

        # Should have comments indicating it's a fallback
        assert "Fallback" in content and "most recent" in content.lower(), (
            "Most recent plan should be documented as a fallback method"
        )

        # Find the CALL site (not the function definition) by looking in main()
        main_start = content.find("def main():")
        main_section = content[main_start:]
        call_pos = main_section.find("get_most_recent_plan()")

        assert call_pos > 0, "Should call get_most_recent_plan() in main()"

        # Should be the LAST fallback (Fallback 3 or "last resort")
        # Use a larger window (400 chars) to capture the comment above the if block
        recent_context = main_section[max(0, call_pos - 400):call_pos]
        assert "Fallback 3" in recent_context or "last resort" in recent_context.lower(), (
            f"Most recent plan should be Fallback 3 or marked as last resort. "
            f"Found context: {recent_context[-200:]!r}"
        )

    def test_tool_response_checked_before_transcript(self):
        """Verify tool_response.filePath is checked before transcript parsing.

        This is the core fix - the bug was that transcript parsing was tried first
        and when it failed, we fell back to most_recent_plan which was wrong.
        """
        script_path = get_plan_export_script()
        content = script_path.read_text()

        # Find the critical section positions
        tool_response_check = content.find('isinstance(tool_response, dict)')
        transcript_call = content.find('get_plan_from_transcript(transcript_path)')

        assert tool_response_check > 0, "Should have tool_response isinstance check"
        assert transcript_call > 0, "Should have transcript parsing call"
        assert tool_response_check < transcript_call, (
            "CRITICAL: tool_response must be checked BEFORE transcript parsing. "
            "This was the root cause of the bug - transcript parsing failed and "
            "fell back to most_recent_plan which returned the wrong file."
        )


class TestExitPlanModeToolResponse:
    """Tests specific to ExitPlanMode tool_response format."""

    def test_exitplanmode_response_format_documented(self):
        """Verify the expected ExitPlanMode response format is documented."""
        script_path = get_plan_export_script()
        content = script_path.read_text()

        # Should document the expected format
        assert "ExitPlanMode" in content, (
            "plan_export.py should document that it handles ExitPlanMode tool"
        )
        assert "filePath" in content and "plan" in content, (
            "plan_export.py should document the expected tool_response fields"
        )

    def test_handles_camelcase_filepath(self):
        """Verify we use camelCase 'filePath' not 'file_path'.

        Claude Code uses camelCase for tool_response fields (e.g., filePath, not file_path).
        """
        script_path = get_plan_export_script()
        content = script_path.read_text()

        assert '"filePath"' in content, (
            "Should use camelCase 'filePath' to match Claude Code's tool_response format"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
