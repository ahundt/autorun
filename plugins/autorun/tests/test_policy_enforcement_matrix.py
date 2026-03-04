#!/usr/bin/env python3
"""Test policy enforcement matrix for PreToolUse hook

This tests the complete policy enforcement matrix for Write tool operations
with new files, existing files, and various policy levels.
"""
import pytest
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from autorun.main import session_state, has_valid_justification
from autorun.core import EventContext, ThreadSafeDB
from autorun import plugins
from conftest import register_test_session


# Daemon-path helper — replaces deleted pretooluse_handler
def _pretooluse(ctx):
    """Run daemon-path PreToolUse chain: file policy then command blocking."""
    result = plugins.enforce_file_policy(ctx)
    if result is not None:
        return result
    result = plugins.check_blocked_commands(ctx)
    if result is not None:
        return result
    return ctx.allow()


class TestPolicyEnforcementMatrix:
    """Test complete policy enforcement matrix"""

    def setup_method(self):
        """Create unique session ID for each test"""
        self.session_id = f"test_policy_matrix_{uuid.uuid4().hex[:8]}"
        register_test_session(self.session_id)

    def create_mock_context(self, tool_name, file_path=None, transcript_content=""):
        """Create a real EventContext for PreToolUse testing"""
        tool_input = {}
        if file_path:
            tool_input["file_path"] = file_path
        transcript = [transcript_content] if transcript_content else []
        return EventContext(
            session_id=self.session_id,
            event="PreToolUse",
            tool_name=tool_name,
            tool_input=tool_input,
            session_transcript=transcript,
            store=ThreadSafeDB(),
        )

    @pytest.mark.parametrize("policy,has_justification,expected_decision", [
        ("ALLOW", False, "allow"),
        ("ALLOW", True, "allow"),
        ("JUSTIFY", False, "deny"),
        ("JUSTIFY", True, "allow"),
        ("SEARCH", False, "deny"),
        ("SEARCH", True, "deny"),  # SEARCH blocks even with justification
    ])
    def test_write_new_file_matrix(self, policy, has_justification, expected_decision):
        """Test policy enforcement matrix for new file creation"""
        # Set policy
        with session_state(self.session_id) as state:
            state["file_policy"] = policy

        # Create transcript with or without justification
        transcript = ""
        if has_justification:
            transcript = "<AUTOFILE_JUSTIFICATION>Creating config for new feature</AUTOFILE_JUSTIFICATION>"

        # Use non-existent path (file doesn't exist = new file)
        ctx = self.create_mock_context("Write", "/nonexistent/path/newfile.txt", transcript)
        result = _pretooluse(ctx)

        actual_decision = result["hookSpecificOutput"]["permissionDecision"]
        assert actual_decision == expected_decision, \
            f"Policy={policy}, Justification={has_justification}: expected '{expected_decision}', got '{actual_decision}'"

    @pytest.mark.parametrize("policy", ["ALLOW", "JUSTIFY", "SEARCH"])
    def test_write_existing_file_always_allowed(self, policy):
        """Test that all policies allow modifying existing files"""
        # Set policy
        with session_state(self.session_id) as state:
            state["file_policy"] = policy

        # Use THIS test file as a known existing file
        existing_file = str(Path(__file__).resolve())
        ctx = self.create_mock_context("Write", existing_file, "")
        result = _pretooluse(ctx)

        assert result["hookSpecificOutput"]["permissionDecision"] == "allow", \
            f"Policy {policy} should allow modifying existing files"

    @pytest.mark.parametrize("tool_name", ["Bash", "Read", "Edit", "Glob", "Grep"])
    @pytest.mark.parametrize("policy", ["ALLOW", "JUSTIFY", "SEARCH"])
    def test_non_write_tools_always_bypass(self, tool_name, policy):
        """Test that non-Write tools bypass all policy checks"""
        # Set strictest policy
        with session_state(self.session_id) as state:
            state["file_policy"] = policy

        ctx = self.create_mock_context(tool_name, None, "")
        result = _pretooluse(ctx)

        assert result["hookSpecificOutput"]["permissionDecision"] == "allow", \
            f"{tool_name} should bypass {policy} policy"


class TestJustificationParsing:
    """Test justification tag detection and validation"""

    def test_valid_justification_content(self):
        """Real justification content should be accepted"""
        assert has_valid_justification(
            "<AUTOFILE_JUSTIFICATION>Config file for user settings</AUTOFILE_JUSTIFICATION>"
        ) is True

    def test_default_placeholder_rejected(self):
        """Default 'reason' placeholder should be rejected"""
        assert has_valid_justification(
            "<AUTOFILE_JUSTIFICATION>reason</AUTOFILE_JUSTIFICATION>"
        ) is False

    def test_empty_justification_rejected(self):
        """Empty justification should be rejected"""
        assert has_valid_justification(
            "<AUTOFILE_JUSTIFICATION></AUTOFILE_JUSTIFICATION>"
        ) is False

    def test_whitespace_only_justification_rejected(self):
        """Whitespace-only justification should be rejected"""
        assert has_valid_justification(
            "<AUTOFILE_JUSTIFICATION>   </AUTOFILE_JUSTIFICATION>"
        ) is False

    def test_no_tag_returns_false(self):
        """No justification tag should return False"""
        assert has_valid_justification("Some random text without tag") is False

    def test_instruction_echo_rejected(self):
        """Instruction echo should be rejected as invalid"""
        instruction = "Include <AUTOFILE_JUSTIFICATION>reason</AUTOFILE_JUSTIFICATION> for new files."
        assert has_valid_justification(instruction) is False

    def test_multiline_justification_accepted(self):
        """Multiline justification should be accepted"""
        multiline = """<AUTOFILE_JUSTIFICATION>
        Creating new config file because:
        1. Existing config.json doesn't support feature X
        2. Need separate file for environment-specific settings
        </AUTOFILE_JUSTIFICATION>"""
        assert has_valid_justification(multiline) is True

    def test_justification_in_larger_transcript(self):
        """Justification should be found in larger transcript"""
        transcript = """User: Create a new config file
        Assistant: I'll create a new configuration file.
        <AUTOFILE_JUSTIFICATION>
        Creating new config file for user preferences - existing settings.json doesn't support this feature
        </AUTOFILE_JUSTIFICATION>
        Let me create the file now."""
        assert has_valid_justification(transcript) is True
