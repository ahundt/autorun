#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright 2025 Andrew Hundt <ATHundt@gmail.com>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Integration tests for command blocking functionality.

Tests the end-to-end integration of command blocking with:
- Command handlers (/cr:no, /cr:ok, /cr:clear, /cr:status, etc.)
- PreToolUse hook blocking
- UserPromptSubmit command handling
"""

import pytest
import json
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

from clautorun.main import (
    handle_block_pattern,
    handle_allow_pattern,
    handle_clear_pattern,
    handle_block_status,
    handle_global_block_pattern,
    handle_global_allow_pattern,
    handle_global_block_status,
    pretooluse_handler,
    clear_session_blocks,
    COMMAND_HANDLERS,
    GLOBAL_CONFIG_FILE
)
from clautorun.config import DEFAULT_INTEGRATIONS


class MockContext:
    """Mock context object for hook handlers."""

    def __init__(self, tool_name="Bash", tool_input=None, session_id="test-session"):
        self.tool_name = tool_name
        self.tool_input = tool_input or {}
        self.session_id = session_id
        self.prompt = ""
        self.hook_event_name = ""
        self.session_transcript = []


class TestCommandBlockingHandlers:
    """Test command blocking handler functions."""

    def setup_method(self):
        """Set up test session ID."""
        self.test_session_id = "test-handler-session"
        clear_session_blocks(self.test_session_id)

    def teardown_method(self):
        """Clean up session blocks."""
        clear_session_blocks(self.test_session_id)

    def test_handle_block_pattern_adds_block(self):
        """Test that handle_block_pattern adds a session block."""
        state = {
            "session_id": self.test_session_id,
            "activation_prompt": "/cr:no rm"
        }

        response = handle_block_pattern(state)

        assert "Blocked: rm" in response
        assert "Session blocks: 1" in response

    def test_handle_block_pattern_with_custom_pattern(self):
        """Test blocking with custom patterns."""
        state = {
            "session_id": self.test_session_id,
            "activation_prompt": "/cr:no dd if="
        }

        response = handle_block_pattern(state)

        assert "Blocked: dd if=" in response

    def test_handle_block_pattern_duplicate(self):
        """Test handling duplicate block attempts."""
        state = {
            "session_id": self.test_session_id,
            "activation_prompt": "/cr:no rm"
        }

        # First block
        handle_block_pattern(state)

        # Second attempt
        response = handle_block_pattern(state)

        assert "already blocked" in response

    def test_handle_block_pattern_missing_args(self):
        """Test handling missing pattern argument."""
        state = {
            "session_id": self.test_session_id,
            "activation_prompt": "/cr:no"
        }

        response = handle_block_pattern(state)

        assert "Usage:" in response or "Example:" in response

    def test_handle_allow_pattern(self):
        """Test allowing a blocked pattern."""
        # First block it
        state = {
            "session_id": self.test_session_id,
            "activation_prompt": "/cr:no rm"
        }
        handle_block_pattern(state)

        # Then allow it
        state["activation_prompt"] = "/cr:ok rm"
        response = handle_allow_pattern(state)

        assert "Allowed: rm" in response

    def test_handle_allow_pattern_not_blocked(self):
        """Test allowing a pattern that wasn't blocked."""
        state = {
            "session_id": self.test_session_id,
            "activation_prompt": "/cr:ok rm"
        }

        response = handle_allow_pattern(state)

        assert "not blocked" in response

    def test_handle_clear_pattern_all(self):
        """Test clearing all session blocks."""
        # Add multiple blocks
        state1 = {"session_id": self.test_session_id, "activation_prompt": "/cr:no rm"}
        state2 = {"session_id": self.test_session_id, "activation_prompt": "/cr:no dd"}

        handle_block_pattern(state1)
        handle_block_pattern(state2)

        # Clear all
        state3 = {"session_id": self.test_session_id, "activation_prompt": "/cr:clear"}
        response = handle_clear_pattern(state3)

        assert "Cleared all session blocks" in response

    def test_handle_clear_pattern_specific(self):
        """Test clearing a specific pattern."""
        # Add multiple blocks
        state1 = {"session_id": self.test_session_id, "activation_prompt": "/cr:no rm"}
        state2 = {"session_id": self.test_session_id, "activation_prompt": "/cr:no dd"}

        handle_block_pattern(state1)
        handle_block_pattern(state2)

        # Clear specific pattern
        state3 = {"session_id": self.test_session_id, "activation_prompt": "/cr:clear rm"}
        response = handle_clear_pattern(state3)

        assert "Cleared: rm" in response

    def test_handle_block_status(self):
        """Test status display."""
        # Add a block
        state = {
            "session_id": self.test_session_id,
            "activation_prompt": "/cr:no rm"
        }
        handle_block_pattern(state)

        # Get status
        state["activation_prompt"] = "/cr:status"
        response = handle_block_status(state)

        assert "Command Blocking Status" in response
        assert "Session blocks" in response
        assert "rm" in response


class TestGlobalCommandHandlers:
    """Test global command blocking handlers."""

    def setup_method(self):
        """Set up temporary config directory."""
        self.temp_dir = tempfile.mkdtemp()
        self.temp_config_file = Path(self.temp_dir) / "command-blocks.json"

        # Patch GLOBAL_CONFIG_FILE
        self.patcher = patch('clautorun.main.GLOBAL_CONFIG_FILE', self.temp_config_file)
        self.patcher.start()

    def teardown_method(self):
        """Clean up patches and temporary directory."""
        self.patcher.stop()
        if Path(self.temp_dir).exists():
            shutil.rmtree(self.temp_dir)

    def test_handle_global_block_pattern(self):
        """Test adding a global block."""
        state = {
            "activation_prompt": "/cr:globalno rm"
        }

        response = handle_global_block_pattern(state)

        assert "Global block: rm" in response

    def test_handle_global_allow_pattern(self):
        """Test removing a global block."""
        # First add it
        state = {"activation_prompt": "/cr:globalno rm"}
        handle_global_block_pattern(state)

        # Then remove it
        state["activation_prompt"] = "/cr:globalok rm"
        response = handle_global_allow_pattern(state)

        assert "Global allow: rm" in response

    def test_handle_global_block_status(self):
        """Test global status display."""
        # Add a global block
        state = {"activation_prompt": "/cr:globalno rm"}
        handle_global_block_pattern(state)

        # Get status
        state["activation_prompt"] = "/cr:globalstatus"
        response = handle_global_block_status(state)

        assert "Global Command Blocks" in response
        assert "rm" in response


class TestPreToolUseBlocking:
    """Test PreToolUse hook blocking integration."""

    def setup_method(self):
        """Set up test session."""
        self.test_session_id = "test-pretooluse"
        clear_session_blocks(self.test_session_id)

    def teardown_method(self):
        """Clean up session blocks."""
        clear_session_blocks(self.test_session_id)

    def test_bash_command_blocked(self):
        """Test that Bash commands can be blocked."""
        # Add block
        from clautorun.main import add_session_block
        add_session_block(self.test_session_id, "rm")

        # Create mock context
        ctx = MockContext(
            tool_name="Bash",
            tool_input={"command": "rm file.txt"},
            session_id=self.test_session_id
        )

        response = pretooluse_handler(ctx)

        # Check hookSpecificOutput for permissionDecision
        assert "hookSpecificOutput" in response
        assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "blocked" in response["hookSpecificOutput"]["permissionDecisionReason"].lower()

    def test_bash_command_allowed(self):
        """Test that unblocked Bash commands are allowed."""
        # Create mock context without any blocks
        ctx = MockContext(
            tool_name="Bash",
            tool_input={"command": "ls file.txt"},
            session_id=self.test_session_id
        )

        response = pretooluse_handler(ctx)

        # Should not be denied by blocking (may be allowed for other reasons)
        # The important thing is it's not denied due to blocking
        if response.get("hookSpecificOutput", {}).get("permissionDecision") == "deny":
            assert "Command blocked" not in response["hookSpecificOutput"].get("permissionDecisionReason", "")

    def test_non_bash_tools_not_affected(self):
        """Test that non-Bash tools are not affected by blocking."""
        # Add block
        from clautorun.main import add_session_block
        add_session_block(self.test_session_id, "rm")

        # Create mock context for Write tool
        ctx = MockContext(
            tool_name="Write",
            tool_input={"file_path": "rm_test.txt", "content": "test"},
            session_id=self.test_session_id
        )

        response = pretooluse_handler(ctx)

        # Should not be denied due to command blocking
        reason = response.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
        assert "Command blocked" not in reason

    def test_pattern_matching_in_pretooluse(self):
        """Test pattern matching in PreToolUse blocking."""
        # Block "dd if=" pattern
        from clautorun.main import add_session_block
        add_session_block(self.test_session_id, "dd if=")

        # Create mock context
        ctx = MockContext(
            tool_name="Bash",
            tool_input={"command": "dd if=/dev/zero of=test"},
            session_id=self.test_session_id
        )

        response = pretooluse_handler(ctx)

        assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "dd if=" in response["hookSpecificOutput"]["permissionDecisionReason"]


class TestCommandHandlerIntegration:
    """Test integration with COMMAND_HANDLERS dict."""

    def test_handlers_registered(self):
        """Test that all blocking handlers are registered."""
        assert "BLOCK_PATTERN" in COMMAND_HANDLERS
        assert "ALLOW_PATTERN" in COMMAND_HANDLERS
        assert "CLEAR_PATTERN" in COMMAND_HANDLERS
        assert "GLOBAL_BLOCK_PATTERN" in COMMAND_HANDLERS
        assert "GLOBAL_ALLOW_PATTERN" in COMMAND_HANDLERS
        assert "GLOBAL_BLOCK_STATUS" in COMMAND_HANDLERS

    def test_handlers_callable(self):
        """Test that all handlers are callable."""
        for handler_name in ["BLOCK_PATTERN", "ALLOW_PATTERN", "CLEAR_PATTERN",
                            "GLOBAL_BLOCK_PATTERN", "GLOBAL_ALLOW_PATTERN",
                            "GLOBAL_BLOCK_STATUS"]:
            handler = COMMAND_HANDLERS[handler_name]
            assert callable(handler)


class TestEndToEndWorkflows:
    """Test complete end-to-end workflows."""

    def setup_method(self):
        """Set up test session and temp directory."""
        self.test_session_id = "test-e2e"
        self.temp_dir = tempfile.mkdtemp()
        self.temp_config_file = Path(self.temp_dir) / "command-blocks.json"

        clear_session_blocks(self.test_session_id)

        # Patch GLOBAL_CONFIG_FILE
        self.patcher = patch('clautorun.main.GLOBAL_CONFIG_FILE', self.temp_config_file)
        self.patcher.start()

    def teardown_method(self):
        """Clean up."""
        self.patcher.stop()
        clear_session_blocks(self.test_session_id)
        if Path(self.temp_dir).exists():
            shutil.rmtree(self.temp_dir)

    def test_block_then_unblock_workflow(self):
        """Test the complete block and unblock workflow."""
        state = {"session_id": self.test_session_id}

        # Block rm
        state["activation_prompt"] = "/cr:no rm"
        response = handle_block_pattern(state)
        assert "Blocked: rm" in response

        # Verify it's blocked
        from clautorun.main import should_block_command
        block_info = should_block_command(self.test_session_id, "rm file.txt")
        assert block_info is not None

        # Unblock rm
        state["activation_prompt"] = "/cr:ok rm"
        response = handle_allow_pattern(state)
        assert "Allowed: rm" in response

        # Verify it's no longer blocked
        block_info = should_block_command(self.test_session_id, "rm file.txt")
        assert block_info is None

    def test_global_to_session_override_workflow(self):
        """Test global block with session override."""
        state = {"session_id": self.test_session_id}

        # Set global block
        state["activation_prompt"] = "/cr:globalno rm"
        response = handle_global_block_pattern(state)
        assert "Global block: rm" in response

        # Verify it's blocked (via global)
        from clautorun.main import should_block_command
        block_info = should_block_command(self.test_session_id, "rm file.txt")
        assert block_info is not None

        # Add session block to override
        state["activation_prompt"] = "/cr:no rm"
        response = handle_block_pattern(state)
        assert "Blocked: rm" in response

        # Remove session block - should fall back to global
        state["activation_prompt"] = "/cr:ok rm"
        response = handle_allow_pattern(state)
        assert "Allowed: rm" in response

        # Still blocked by global
        block_info = should_block_command(self.test_session_id, "rm file.txt")
        assert block_info is not None

    def test_clear_workflow(self):
        """Test clearing blocks workflow."""
        state = {"session_id": self.test_session_id}

        # Add multiple blocks
        state["activation_prompt"] = "/cr:no rm"
        handle_block_pattern(state)

        state["activation_prompt"] = "/cr:no dd"
        handle_block_pattern(state)

        # Clear all
        state["activation_prompt"] = "/cr:clear"
        response = handle_clear_pattern(state)

        assert "Cleared all session blocks" in response

        # Verify all cleared
        from clautorun.main import get_session_blocks
        blocks = get_session_blocks(self.test_session_id)
        assert len(blocks) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
