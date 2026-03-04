#!/usr/bin/env python3
"""Test the PreToolUse blocking fix - ensure operations don't get blocked incorrectly.

Tests daemon-path PreToolUse chain using real EventContext (no mocks).
"""
import os
import pytest
import tempfile
from pathlib import Path

# Add src to path for testing
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from autorun.core import EventContext, ThreadSafeDB
from autorun import plugins
from autorun.session_manager import session_state


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


class TestPreToolUseBlockingFix:
    """Test that the PreToolUse handler doesn't block all operations (original bug fix)"""

    def test_non_write_tools_allowed(self):
        """Test that non-Write tools are always allowed - fixes original blocking bug"""
        ctx = EventContext(session_id="test-non-write", event="PreToolUse", tool_name="Bash",
                           tool_input={"command": "echo hello"}, store=ThreadSafeDB())
        result = _pretooluse(ctx)
        assert result["continue"] is True, "PreToolUse should always continue for non-Write tools"
        assert result["hookSpecificOutput"]["permissionDecision"] == "allow", \
            "Non-Write tools with file paths should be allowed"

    def test_write_no_file_path_allowed(self):
        """Test that Write tools with no file path are allowed under default ALLOW policy"""
        sid = "test-write-no-path"
        with session_state(sid) as s:
            s["file_policy"] = "ALLOW"
        ctx = EventContext(session_id=sid, event="PreToolUse", tool_name="Write",
                           tool_input={}, store=ThreadSafeDB())
        result = _pretooluse(ctx)
        assert result["continue"] is True, "PreToolUse should continue for Write tools under ALLOW policy"
        assert result["hookSpecificOutput"]["permissionDecision"] == "allow", \
            "Write tools should be allowed under ALLOW policy"

    def test_write_empty_file_path_allowed(self):
        """Test that Write tools with empty file path are allowed under default ALLOW policy"""
        sid = "test-write-empty-path"
        with session_state(sid) as s:
            s["file_policy"] = "ALLOW"
        ctx = EventContext(session_id=sid, event="PreToolUse", tool_name="Write",
                           tool_input={"file_path": ""}, store=ThreadSafeDB())
        result = _pretooluse(ctx)
        assert result["continue"] is True, \
            "PreToolUse should continue for Write tools with empty file path under ALLOW policy"
        assert result["hookSpecificOutput"]["permissionDecision"] == "allow", \
            "Write tools with empty file path should be allowed under ALLOW policy"

    def test_existing_file_allowed(self):
        """Test that modifying existing files is allowed under SEARCH policy (user requirement)"""
        sid = "test-existing-file"
        with session_state(sid) as s:
            s["file_policy"] = "SEARCH"
        # Create a real temp file that exists
        with tempfile.NamedTemporaryFile(delete=False) as f:
            existing_path = f.name
        try:
            ctx = EventContext(session_id=sid, event="PreToolUse", tool_name="Write",
                               tool_input={"file_path": existing_path}, store=ThreadSafeDB())
            result = _pretooluse(ctx)
            assert result["continue"] is True, \
                "PreToolUse should continue for existing file modification under SEARCH policy"
            assert result["hookSpecificOutput"]["permissionDecision"] == "allow", \
                "Existing file modification should be allowed under SEARCH policy per user requirements"
        finally:
            os.unlink(existing_path)

    def test_new_file_allow_policy_allowed(self):
        """Test that new files are allowed under ALLOW policy"""
        sid = "test-new-file-allow"
        with session_state(sid) as s:
            s["file_policy"] = "ALLOW"
        ctx = EventContext(session_id=sid, event="PreToolUse", tool_name="Write",
                           tool_input={"file_path": "/tmp/nonexistent_autorun_test_xyz.txt"},
                           store=ThreadSafeDB())
        result = _pretooluse(ctx)
        assert result["continue"] is True
        assert result["hookSpecificOutput"]["permissionDecision"] == "allow"

    def test_new_file_search_policy_blocked(self):
        """Test that new files are blocked under SEARCH policy"""
        sid = "test-new-file-search"
        with session_state(sid) as s:
            s["file_policy"] = "SEARCH"
        ctx = EventContext(session_id=sid, event="PreToolUse", tool_name="Write",
                           tool_input={"file_path": "/tmp/nonexistent_autorun_test_xyz.txt"},
                           store=ThreadSafeDB())
        result = _pretooluse(ctx)
        assert result["continue"] is True  # AI keeps running per official hooks docs
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "SEARCH" in result["hookSpecificOutput"]["permissionDecisionReason"]

    def test_new_file_justify_policy_blocked_without_justification(self):
        """Test that new files are blocked under JUSTIFY policy without justification"""
        sid = "test-justify-no-just"
        with session_state(sid) as s:
            s["file_policy"] = "JUSTIFY"
        ctx = EventContext(session_id=sid, event="PreToolUse", tool_name="Write",
                           tool_input={"file_path": "/tmp/nonexistent_autorun_test_xyz.txt"},
                           session_transcript=[],  # No justification
                           store=ThreadSafeDB())
        result = _pretooluse(ctx)
        assert result["continue"] is True  # AI keeps running per official hooks docs
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "justif" in result["hookSpecificOutput"]["permissionDecisionReason"].lower()

    def test_new_file_justify_policy_allowed_with_justification(self):
        """Test that new files are allowed under JUSTIFY policy with justification"""
        sid = "test-justify-with-just"
        with session_state(sid) as s:
            s["file_policy"] = "JUSTIFY"
        ctx = EventContext(
            session_id=sid, event="PreToolUse", tool_name="Write",
            tool_input={"file_path": "/tmp/nonexistent_autorun_test_xyz.txt"},
            session_transcript=["This file is needed because <AUTOFILE_JUSTIFICATION>existing files don't support this feature</AUTOFILE_JUSTIFICATION>"],
            store=ThreadSafeDB(),
        )
        result = _pretooluse(ctx)
        assert result["continue"] is True
        assert result["hookSpecificOutput"]["permissionDecision"] == "allow"

    def test_original_blocking_bug_fixed(self):
        """Test the original blocking bug is fixed - operations shouldn't be universally blocked"""
        # The original bug was that ALL operations were getting blocked
        # This test ensures we can still perform normal operations
        sid = "test-orig-bug"
        # No policy set — should default to ALLOW
        ctx = EventContext(session_id=sid, event="PreToolUse", tool_name="Write",
                           tool_input={"file_path": "/tmp/test_file.txt"},
                           store=ThreadSafeDB())
        result = _pretooluse(ctx)
        # CRITICAL: Should NOT block operations (ALLOW is default)
        assert result["continue"] is True
        assert result["hookSpecificOutput"]["permissionDecision"] == "allow"
        # The original bug would have resulted in permissionDecision being "deny"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
