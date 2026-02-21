#!/usr/bin/env python3
"""Test the PreToolUse blocking fix - ensure operations don't get blocked incorrectly"""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add src to path for testing
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from autorun import pretooluse_handler


class TestPreToolUseBlockingFix:
    """Test that the PreToolUse handler doesn't block all operations (original bug fix)"""

    def test_non_write_tools_allowed(self):
        """Test that non-Write tools are always allowed - fixes original blocking bug"""
        # Create mock context for non-Write tool with file path (should always be allowed)
        mock_ctx = MagicMock()
        mock_ctx.tool_name = "Bash"
        mock_ctx.tool_input = {"command": "echo hello", "file_path": "some_file.txt"}  # Has file path
        mock_ctx.session_id = "test_session"

        # Mock session state to avoid database issues
        with patch('autorun.main.session_state') as mock_session:
            mock_state = {"file_policy": "ALLOW"}  # Default policy
            mock_session.return_value.__enter__.return_value = mock_state
            mock_session.return_value.__exit__.return_value = None

            result = pretooluse_handler(mock_ctx)

            # Should allow execution - non-Write tools with file paths are always allowed
            assert result["continue"] is True, "PreToolUse should always continue for non-Write tools"
            assert result["hookSpecificOutput"]["permissionDecision"] == "allow", "Non-Write tools with file paths should be allowed"

    def test_write_no_file_path_allowed(self):
        """Test that Write tools with no file path are allowed under default ALLOW policy"""
        mock_ctx = MagicMock()
        mock_ctx.tool_name = "Write"
        mock_ctx.tool_input = {}  # No file_path
        mock_ctx.session_id = "test_session"

        # Mock session state to avoid database issues and set default ALLOW policy
        with patch('autorun.main.session_state') as mock_session:
            mock_state = {"file_policy": "ALLOW"}  # Default policy
            mock_session.return_value.__enter__.return_value = mock_state
            mock_session.return_value.__exit__.return_value = None

            result = pretooluse_handler(mock_ctx)

            # Should allow execution under ALLOW policy
            assert result["continue"] is True, "PreToolUse should continue for Write tools under ALLOW policy"
            assert result["hookSpecificOutput"]["permissionDecision"] == "allow", "Write tools should be allowed under ALLOW policy"

    def test_write_empty_file_path_allowed(self):
        """Test that Write tools with empty file path are allowed under default ALLOW policy"""
        mock_ctx = MagicMock()
        mock_ctx.tool_name = "Write"
        mock_ctx.tool_input = {"file_path": ""}
        mock_ctx.session_id = "test_session"

        # Mock session state to avoid database issues and set default ALLOW policy
        with patch('autorun.main.session_state') as mock_session:
            mock_state = {"file_policy": "ALLOW"}  # Default policy
            mock_session.return_value.__enter__.return_value = mock_state
            mock_session.return_value.__exit__.return_value = None

            result = pretooluse_handler(mock_ctx)

            # Should allow execution under ALLOW policy
            assert result["continue"] is True, "PreToolUse should continue for Write tools with empty file path under ALLOW policy"
            assert result["hookSpecificOutput"]["permissionDecision"] == "allow", "Write tools with empty file path should be allowed under ALLOW policy"

    def test_existing_file_allowed(self):
        """Test that modifying existing files is allowed under SEARCH policy (user requirement)"""
        mock_ctx = MagicMock()
        mock_ctx.tool_name = "Write"
        mock_ctx.tool_input = {"file_path": "/tmp/existing_file.txt"}
        mock_ctx.session_id = "test_session"

        # Mock that file exists and set SEARCH policy
        with patch('autorun.main.Path') as mock_path, \
             patch('autorun.main.session_state') as mock_session:
            mock_path.return_value.exists.return_value = True

            # Set SEARCH policy - should allow editing existing files per user requirements
            mock_state = {"file_policy": "SEARCH"}
            mock_session.return_value.__enter__.return_value = mock_state
            mock_session.return_value.__exit__.return_value = None

            result = pretooluse_handler(mock_ctx)

            # Should allow execution - SEARCH policy allows editing existing files
            assert result["continue"] is True, "PreToolUse should continue for existing file modification under SEARCH policy"
            assert result["hookSpecificOutput"]["permissionDecision"] == "allow", "Existing file modification should be allowed under SEARCH policy per user requirements"

    def test_new_file_allow_policy_allowed(self):
        """Test that new files are allowed under ALLOW policy"""
        mock_ctx = MagicMock()
        mock_ctx.tool_name = "Write"
        mock_ctx.tool_input = {"file_path": "/tmp/new_file.txt"}
        mock_ctx.session_id = "test_session"
        mock_ctx.session_transcript = []

        # Mock that file doesn't exist and policy is ALLOW
        with patch('autorun.main.Path') as mock_path, \
             patch('autorun.main.session_state') as mock_session:
            mock_path.return_value.exists.return_value = False

            # Setup mock session state
            mock_state = {"file_policy": "ALLOW"}
            mock_session.return_value.__enter__.return_value = mock_state
            mock_session.return_value.__exit__.return_value = None

            result = pretooluse_handler(mock_ctx)

            # Should allow execution
            assert result["continue"] is True
            assert result["hookSpecificOutput"]["permissionDecision"] == "allow"

    def test_new_file_search_policy_blocked(self):
        """Test that new files are blocked under SEARCH policy"""
        mock_ctx = MagicMock()
        mock_ctx.tool_name = "Write"
        mock_ctx.tool_input = {"file_path": "/tmp/new_file.txt"}
        mock_ctx.session_id = "test_session"

        # Mock that file doesn't exist and policy is SEARCH
        with patch('autorun.main.Path') as mock_path, \
             patch('autorun.main.session_state') as mock_session:
            # Mock Path chain: Path(file_path).resolve().exists()
            mock_resolved = MagicMock()
            mock_resolved.exists.return_value = False
            mock_resolved.is_file.return_value = False
            mock_path.return_value.resolve.return_value = mock_resolved

            # Setup mock session state
            mock_state = {"file_policy": "SEARCH"}
            mock_session.return_value.__enter__.return_value = mock_state
            mock_session.return_value.__exit__.return_value = None

            result = pretooluse_handler(mock_ctx)

            # Should deny tool execution (continue=True: AI keeps running, permissionDecision blocks tool)
            assert result["continue"] is True  # AI keeps running per official hooks docs
            assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
            assert "SEARCH policy" in result["hookSpecificOutput"]["permissionDecisionReason"]

    def test_new_file_justify_policy_blocked_without_justification(self):
        """Test that new files are blocked under JUSTIFY policy without justification"""
        mock_ctx = MagicMock()
        mock_ctx.tool_name = "Write"
        mock_ctx.tool_input = {"file_path": "/tmp/new_file.txt"}
        mock_ctx.session_id = "test_session"
        mock_ctx.session_transcript = []  # No justification in transcript

        # Mock that file doesn't exist and policy is JUSTIFY
        with patch('autorun.main.Path') as mock_path, \
             patch('autorun.main.session_state') as mock_session:
            # Mock Path chain: Path(file_path).resolve().exists()
            mock_resolved = MagicMock()
            mock_resolved.exists.return_value = False
            mock_resolved.is_file.return_value = False
            mock_path.return_value.resolve.return_value = mock_resolved

            # Setup mock session state
            mock_state = {"file_policy": "JUSTIFY", "autofile_justification_detected": False}
            mock_session.return_value.__enter__.return_value = mock_state
            mock_session.return_value.__exit__.return_value = None

            result = pretooluse_handler(mock_ctx)

            # Should deny tool execution (continue=True: AI keeps running, permissionDecision blocks tool)
            assert result["continue"] is True  # AI keeps running per official hooks docs
            assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
            assert "justification" in result["hookSpecificOutput"]["permissionDecisionReason"].lower()

    def test_new_file_justify_policy_allowed_with_justification(self):
        """Test that new files are allowed under JUSTIFY policy with justification"""
        mock_ctx = MagicMock()
        mock_ctx.tool_name = "Write"
        mock_ctx.tool_input = {"file_path": "/tmp/new_file.txt"}
        mock_ctx.session_id = "test_session"
        mock_ctx.session_transcript = ["This file is needed because <AUTOFILE_JUSTIFICATION>existing files don't support this feature</AUTOFILE_JUSTIFICATION>"]

        # Mock that file doesn't exist and policy is JUSTIFY
        with patch('autorun.main.Path') as mock_path, \
             patch('autorun.main.session_state') as mock_session:
            mock_path.return_value.exists.return_value = False

            # Setup mock session state
            mock_state = {"file_policy": "JUSTIFY"}
            mock_session.return_value.__enter__.return_value = mock_state
            mock_session.return_value.__exit__.return_value = None

            result = pretooluse_handler(mock_ctx)

            # Should allow execution
            assert result["continue"] is True
            assert result["hookSpecificOutput"]["permissionDecision"] == "allow"

    def test_original_blocking_bug_fixed(self):
        """Test the original blocking bug is fixed - operations shouldn't be universally blocked"""
        # The original bug was that ALL operations were getting blocked
        # This test ensures we can still perform normal operations

        mock_ctx = MagicMock()
        mock_ctx.tool_name = "Write"
        mock_ctx.tool_input = {"file_path": "/tmp/test_file.txt"}
        mock_ctx.session_id = "test_session"

        # Test with default policy (ALLOW)
        with patch('autorun.main.Path') as mock_path, \
             patch('autorun.main.session_state') as mock_session:
            mock_path.return_value.exists.return_value = False

            # Setup mock session state with default policy
            mock_state = {}  # No policy set, should default to ALLOW
            mock_session.return_value.__enter__.return_value = mock_state
            mock_session.return_value.__exit__.return_value = None

            result = pretooluse_handler(mock_ctx)

            # CRITICAL: Should NOT block operations
            assert result["continue"] is True
            assert result["hookSpecificOutput"]["permissionDecision"] == "allow"

            # The original bug would have resulted in permissionDecision being "deny"
            # This test ensures that doesn't happen


if __name__ == "__main__":
    pytest.main([__file__, "-v"])