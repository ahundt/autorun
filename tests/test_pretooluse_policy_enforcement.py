#!/usr/bin/env python3
"""Test PreToolUse hook policy enforcement functionality"""
import pytest
import sys
import uuid
from pathlib import Path
from unittest.mock import Mock

# Add the src directory to Python path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from clautorun.main import pretooluse_handler, session_state, CONFIG, _session_backends

# Import conftest utilities for cleanup
from conftest import register_test_session, should_keep_test_artifacts


class TestPreToolUsePolicyEnforcement:
    """Test PreToolUse hook policy enforcement logic"""

    def setup_method(self):
        """Set up test environment before each test"""
        # Use a unique session ID for each test to avoid backend cache conflicts
        self.session_id = f"test_session_{uuid.uuid4().hex[:8]}"

        # Register for cleanup
        register_test_session(self.session_id)

        # Clear any cached backend for this session
        if self.session_id in _session_backends:
            del _session_backends[self.session_id]

    def teardown_method(self):
        """Clean up test session files unless debug flag is set"""
        if should_keep_test_artifacts():
            return

        # Clean up session database files
        state_dir = Path.home() / ".claude" / "sessions"
        if state_dir.exists():
            for pattern in [f"{self.session_id}*", f"test_backend_{self.session_id}*", f"test_dumbdbm_{self.session_id}*"]:
                for filepath in state_dir.glob(pattern):
                    try:
                        filepath.unlink()
                    except (OSError, IOError):
                        pass

        # Clear cached backend
        if self.session_id in _session_backends:
            del _session_backends[self.session_id]

    def create_mock_context(self, tool_name, file_path=None, session_transcript=None):
        """Create a mock context object for PreToolUse testing"""
        ctx = Mock()
        ctx.tool_name = tool_name
        ctx.tool_input = {}

        if file_path is not None:
            ctx.tool_input["file_path"] = file_path

        ctx.session_id = self.session_id
        ctx.session_transcript = session_transcript or []

        return ctx

    def test_non_write_tools_with_file_paths_allowed(self):
        """Test that non-Write tools with file paths are allowed regardless of policy"""
        non_write_tools = ["Read", "Edit", "Bash", "Glob", "Grep"]
        policies = ["ALLOW", "SEARCH", "JUSTIFY"]

        for tool in non_write_tools:
            for policy in policies:
                # Set policy
                with session_state(self.session_id) as state:
                    state["file_policy"] = policy

                ctx = self.create_mock_context(tool, "some_file.txt")
                result = pretooluse_handler(ctx)

                # Should allow non-Write tools with file paths
                assert result["continue"] is True
                assert result["hookSpecificOutput"]["permissionDecision"] == "allow"

    def test_non_write_tools_without_file_paths_policy_enforced(self):
        """Test that non-Write tools without file paths undergo policy checking"""
        non_write_tools = ["Read", "Edit", "Bash", "Glob", "Grep"]
        policies = ["ALLOW", "SEARCH", "JUSTIFY"]

        for tool in non_write_tools:
            for policy in policies:
                # Set policy
                with session_state(self.session_id) as state:
                    state["file_policy"] = policy

                ctx = self.create_mock_context(tool, None)  # No file_path
                result = pretooluse_handler(ctx)

                # Policy should be checked for non-Write tools without file paths
                if policy == "ALLOW":
                    assert result["continue"] is True
                    assert result["hookSpecificOutput"]["permissionDecision"] == "allow"
                elif policy == "SEARCH":
                    assert result["continue"] is True
                    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
                    assert "STRICT SEARCH" in result["systemMessage"]
                elif policy == "JUSTIFY":
                    assert result["continue"] is True
                    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
                    assert "JUSTIFIED" in result["systemMessage"]

    def test_write_tools_without_file_paths_policy_enforced(self):
        """Test that Write tools without file paths undergo policy checking"""
        write_scenarios = [
            (None, None),           # No file_path key
            ("", None),              # Empty file_path
        ]

        for policy in ["ALLOW", "SEARCH", "JUSTIFY"]:
            for file_path, _ in write_scenarios:
                # Set policy
                with session_state(self.session_id) as state:
                    state["file_policy"] = policy

                ctx = self.create_mock_context("Write", file_path)
                result = pretooluse_handler(ctx)

                # Policy should be checked (all Write tools undergo policy checking after fix)
                if policy == "ALLOW":
                    assert result["continue"] is True
                    assert result["hookSpecificOutput"]["permissionDecision"] == "allow"
                elif policy == "SEARCH":
                    assert result["continue"] is True
                    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
                    assert "STRICT SEARCH" in result["systemMessage"]
                elif policy == "JUSTIFY":
                    assert result["continue"] is True
                    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
                    assert "JUSTIFIED" in result["systemMessage"]

    def test_write_tools_with_file_paths_policy_enforced(self):
        """Test that Write tools with file paths undergo policy checking"""
        file_paths = ["new_file.txt", "existing_file.py", "/path/to/file.md"]
        policies = ["ALLOW", "SEARCH", "JUSTIFY"]

        for policy in policies:
            for file_path in file_paths:
                # Set policy
                with session_state(self.session_id) as state:
                    state["file_policy"] = policy

                ctx = self.create_mock_context("Write", file_path)
                result = pretooluse_handler(ctx)

                # Policy should be checked (all Write tools undergo policy checking after fix)
                if policy == "ALLOW":
                    assert result["continue"] is True
                    assert result["hookSpecificOutput"]["permissionDecision"] == "allow"
                elif policy == "SEARCH":
                    assert result["continue"] is True
                    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
                    assert "STRICT SEARCH" in result["systemMessage"]
                elif policy == "JUSTIFY":
                    assert result["continue"] is True
                    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
                    assert "JUSTIFIED" in result["systemMessage"]

    def test_justify_policy_with_justification_present(self):
        """Test that JUSTIFY policy allows operations when justification is present"""
        justification_scenarios = [
            "I need to create this because <AUTOFILE_JUSTIFICATION>no existing file handles this functionality</AUTOFILE_JUSTIFICATION>",
            "After searching, <AUTOFILE_JUSTIFICATION>existing files are incompatible</AUTOFILE_JUSTIFICATION>",
            "Creating new file <AUTOFILE_JUSTIFICATION>specific technical reason here</AUTOFILE_JUSTIFICATION>"
        ]

        for justification in justification_scenarios:
            # Set JUSTIFY policy
            with session_state(self.session_id) as state:
                state["file_policy"] = "JUSTIFY"

            ctx = self.create_mock_context("Write", "new_file.txt", [justification])
            result = pretooluse_handler(ctx)

            # Should allow when justification is present
            assert result["continue"] is True
            assert result["hookSpecificOutput"]["permissionDecision"] == "allow"

    def test_justify_policy_without_justification_blocks(self):
        """Test that JUSTIFY policy blocks operations when no justification is present"""
        no_justification_scenarios = [
            [],  # Empty transcript
            ["Some previous message"],  # No justification in transcript
            ["No justification here", "Still no justification"],  # Multiple messages without justification
        ]

        for transcript in no_justification_scenarios:
            # Set JUSTIFY policy
            with session_state(self.session_id) as state:
                state["file_policy"] = "JUSTIFY"

            ctx = self.create_mock_context("Write", "new_file.txt", transcript)
            result = pretooluse_handler(ctx)

            # Should block when no justification is present
            assert result["continue"] is True
            assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
            assert "JUSTIFIED" in result["systemMessage"]

    def test_search_policy_blocks_all_write_operations(self):
        """Test that SEARCH policy blocks all Write operations regardless of file path"""
        write_scenarios = [
            (None, None),           # No file_path
            ("", None),              # Empty file_path
            ("new_file.txt", None), # Non-empty file_path
            ("existing.py", None),  # Existing file
        ]

        for file_path, _ in write_scenarios:
            # Set SEARCH policy
            with session_state(self.session_id) as state:
                state["file_policy"] = "SEARCH"

            ctx = self.create_mock_context("Write", file_path)
            result = pretooluse_handler(ctx)

            # Should block all Write operations
            assert result["continue"] is True
            assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
            assert "STRICT SEARCH" in result["systemMessage"]
            assert "NO new files" in result["systemMessage"]

    def test_allow_policy_permits_all_operations(self):
        """Test that ALLOW policy permits all operations"""
        tools = ["Write", "Read", "Edit", "Bash"]
        file_paths = [None, "", "some_file.txt", "new_file.py"]

        for tool in tools:
            for file_path in file_paths:
                # Set ALLOW policy
                with session_state(self.session_id) as state:
                    state["file_policy"] = "ALLOW"

                ctx = self.create_mock_context(tool, file_path)
                result = pretooluse_handler(ctx)

                # For non-Write tools with file paths, should allow
                if tool != "Write" and file_path:
                    assert result["continue"] is True
                    assert result["hookSpecificOutput"]["permissionDecision"] == "allow"
                # For other cases, should also allow due to ALLOW policy
                else:
                    assert result["continue"] is True
                    assert result["hookSpecificOutput"]["permissionDecision"] == "allow"

    def test_default_policy_is_allow(self):
        """Test that default policy (when none set) is ALLOW"""
        ctx = self.create_mock_context("Write", "some_file.txt")
        result = pretooluse_handler(ctx)

        # Should allow when no policy is set (defaults to ALLOW)
        assert result["continue"] is True
        assert result["hookSpecificOutput"]["permissionDecision"] == "allow"

    def test_policy_response_format(self):
        """Test that policy enforcement responses have correct format"""
        with session_state(self.session_id) as state:
            state["file_policy"] = "SEARCH"

        ctx = self.create_mock_context("Write", "blocked_file.txt")
        result = pretooluse_handler(ctx)

        # Verify response format
        assert "continue" in result
        assert "stopReason" in result
        assert "suppressOutput" in result
        assert "systemMessage" in result
        assert "hookSpecificOutput" in result

        hook_output = result["hookSpecificOutput"]
        assert "hookEventName" in hook_output
        assert "permissionDecision" in hook_output
        assert "permissionDecisionReason" in hook_output

        assert hook_output["hookEventName"] == "PreToolUse"
        assert hook_output["permissionDecision"] in ["allow", "deny"]

    def test_policy_blocked_messages_are_from_config(self):
        """Test that blocked messages use the configured policy messages"""
        with session_state(self.session_id) as state:
            state["file_policy"] = "SEARCH"

        ctx = self.create_mock_context("Write", "blocked_file.txt")
        result = pretooluse_handler(ctx)

        # Should use the configured SEARCH policy message
        expected_message = CONFIG["policies"]["SEARCH"][1]
        assert expected_message in result["systemMessage"]

        # Test JUSTIFY policy
        with session_state(self.session_id) as state:
            state["file_policy"] = "JUSTIFY"

        ctx = self.create_mock_context("Write", "blocked_file.txt")
        result = pretooluse_handler(ctx)

        # Should use the configured JUSTIFY policy message
        expected_message = CONFIG["policies"]["JUSTIFY"][1]
        assert expected_message in result["systemMessage"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])