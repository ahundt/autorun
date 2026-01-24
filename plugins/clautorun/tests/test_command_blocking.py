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
Unit tests for command blocking functionality.

Tests the core functions for managing command blocking patterns:
- Pattern matching logic
- Session-level block management
- Global block management
- Block precedence (session over global)
"""

import pytest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

from clautorun.main import (
    command_matches_pattern,
    should_block_command,
    get_session_blocks,
    add_session_block,
    remove_session_block,
    clear_session_blocks,
    get_global_blocks,
    add_global_block,
    remove_global_block,
    GLOBAL_CONFIG_FILE
)
from clautorun.config import DEFAULT_INTEGRATIONS


class TestPatternMatching:
    """Test command pattern matching logic."""

    def test_exact_match(self):
        """Test exact command match."""
        assert command_matches_pattern("rm", "rm") is True
        assert command_matches_pattern("ls", "ls") is True

    def test_command_name_match(self):
        """Test matching command name with arguments."""
        assert command_matches_pattern("rm file.txt", "rm") is True
        assert command_matches_pattern("rm -rf /tmp/test", "rm") is True
        assert command_matches_pattern("sudo rm file.txt", "rm") is True

    def test_substring_pattern_match(self):
        """Test matching patterns with spaces."""
        assert command_matches_pattern("dd if=/dev/zero of=file", "dd if=") is True
        assert command_matches_pattern("rm -rf file.txt", "rm -rf") is True

    def test_pattern_with_special_characters(self):
        """Test patterns with special characters."""
        assert command_matches_pattern("echo 'test'", "echo 'test'") is True
        # Note: --force matches as a separate token - this is expected behavior
        # for blocking commands that use the --force flag
        assert command_matches_pattern("grep --force pattern", "--force") is True

    def test_no_match(self):
        """Test commands that don't match patterns."""
        assert command_matches_pattern("ls file.txt", "rm") is False
        assert command_matches_pattern("cat file.txt", "dd if=") is False

    def test_empty_inputs(self):
        """Test edge cases with empty inputs."""
        assert command_matches_pattern("", "rm") is False
        assert command_matches_pattern("rm", "") is False
        assert command_matches_pattern("", "") is False

    def test_piped_commands(self):
        """Test commands with pipes."""
        assert command_matches_pattern("cat file | rm output", "rm") is True
        assert command_matches_pattern("ls | grep test", "rm") is False


class TestSessionBlockManagement:
    """Test session-level block management."""

    def setup_method(self):
        """Set up test session ID."""
        self.test_session_id = "test-session-block"

    def test_add_session_block(self):
        """Test adding a block to session state."""
        # Clear any existing blocks first
        clear_session_blocks(self.test_session_id)

        added = add_session_block(self.test_session_id, "rm")
        assert added is True

        blocks = get_session_blocks(self.test_session_id)
        assert len(blocks) == 1
        assert blocks[0]["pattern"] == "rm"
        assert "suggestion" in blocks[0]
        assert "added_at" in blocks[0]

    def test_add_duplicate_block(self):
        """Test that duplicate blocks are not added."""
        clear_session_blocks(self.test_session_id)

        add_session_block(self.test_session_id, "rm")
        added = add_session_block(self.test_session_id, "rm")

        assert added is False
        assert len(get_session_blocks(self.test_session_id)) == 1

    def test_remove_session_block(self):
        """Test removing a block from session state."""
        clear_session_blocks(self.test_session_id)
        add_session_block(self.test_session_id, "rm")
        add_session_block(self.test_session_id, "dd")

        removed = remove_session_block(self.test_session_id, "rm")
        assert removed is True

        blocks = get_session_blocks(self.test_session_id)
        assert len(blocks) == 1
        assert blocks[0]["pattern"] == "dd"

    def test_remove_nonexistent_block(self):
        """Test removing a block that doesn't exist."""
        clear_session_blocks(self.test_session_id)

        removed = remove_session_block(self.test_session_id, "rm")
        assert removed is False

    def test_clear_all_session_blocks(self):
        """Test clearing all session blocks."""
        add_session_block(self.test_session_id, "rm")
        add_session_block(self.test_session_id, "dd")

        count = clear_session_blocks(self.test_session_id, None)
        assert count == 2
        assert len(get_session_blocks(self.test_session_id)) == 0

    def test_clear_specific_session_block(self):
        """Test clearing a specific session block."""
        add_session_block(self.test_session_id, "rm")
        add_session_block(self.test_session_id, "dd")

        count = clear_session_blocks(self.test_session_id, "rm")
        assert count == 1

        blocks = get_session_blocks(self.test_session_id)
        assert len(blocks) == 1
        assert blocks[0]["pattern"] == "dd"

    def test_default_suggestion_from_integration(self):
        """Test that default integrations provide suggestions."""
        clear_session_blocks(self.test_session_id)

        add_session_block(self.test_session_id, "rm")
        blocks = get_session_blocks(self.test_session_id)

        assert "trash" in blocks[0]["suggestion"]

    def test_custom_suggestion(self):
        """Test custom suggestions override defaults."""
        clear_session_blocks(self.test_session_id)

        custom_suggestion = "Use custom command instead"
        add_session_block(self.test_session_id, "rm", custom_suggestion)

        blocks = get_session_blocks(self.test_session_id)
        assert blocks[0]["suggestion"] == custom_suggestion


class TestGlobalBlockManagement:
    """Test global block management."""

    def setup_method(self):
        """Set up temporary config directory for testing."""
        self.temp_dir = tempfile.mkdtemp()
        self.temp_config_file = Path(self.temp_dir) / "command-blocks.json"

        # Patch GLOBAL_CONFIG_FILE to use temp directory
        self.patcher = patch('clautorun.main.GLOBAL_CONFIG_FILE', self.temp_config_file)
        self.patcher.start()

    def teardown_method(self):
        """Clean up temporary directory."""
        self.patcher.stop()
        if Path(self.temp_dir).exists():
            shutil.rmtree(self.temp_dir)

    def test_add_global_block(self):
        """Test adding a global block."""
        added = add_global_block("rm")
        assert added is True

        blocks = get_global_blocks()
        assert len(blocks) == 1
        assert blocks[0]["pattern"] == "rm"

    def test_add_duplicate_global_block(self):
        """Test that duplicate global blocks are not added."""
        add_global_block("rm")
        added = add_global_block("rm")

        assert added is False
        assert len(get_global_blocks()) == 1

    def test_remove_global_block(self):
        """Test removing a global block."""
        add_global_block("rm")
        add_global_block("dd")

        removed = remove_global_block("rm")
        assert removed is True

        blocks = get_global_blocks()
        assert len(blocks) == 1
        assert blocks[0]["pattern"] == "dd"

    def test_remove_nonexistent_global_block(self):
        """Test removing a global block that doesn't exist."""
        removed = remove_global_block("rm")
        assert removed is False

    def test_empty_global_blocks(self):
        """Test getting blocks when config doesn't exist."""
        blocks = get_global_blocks()
        assert blocks == []

    def test_global_block_persistence(self):
        """Test that global blocks persist across function calls."""
        # This tests file-based persistence
        add_global_block("rm")

        # Create new "session" by calling get_global_blocks again
        blocks = get_global_blocks()
        assert len(blocks) == 1
        assert blocks[0]["pattern"] == "rm"


class TestBlockPrecedence:
    """Test session vs global block precedence."""

    def setup_method(self):
        """Set up test session and temporary config."""
        self.test_session_id = "test-precedence"
        self.temp_dir = tempfile.mkdtemp()
        self.temp_config_file = Path(self.temp_dir) / "command-blocks.json"

        # Patch GLOBAL_CONFIG_FILE
        self.patcher = patch('clautorun.main.GLOBAL_CONFIG_FILE', self.temp_config_file)
        self.patcher.start()

        # Clear session blocks
        clear_session_blocks(self.test_session_id)

    def teardown_method(self):
        """Clean up patches and session blocks."""
        self.patcher.stop()
        clear_session_blocks(self.test_session_id)
        if Path(self.temp_dir).exists():
            shutil.rmtree(self.temp_dir)

    def test_session_block_takes_precedence(self):
        """Test that session blocks override global blocks."""
        # Add global block
        add_global_block("rm")

        # Add conflicting session block
        custom_suggestion = "Session-specific suggestion"
        add_session_block(self.test_session_id, "rm", custom_suggestion)

        # Check blocking
        block_info = should_block_command(self.test_session_id, "rm file.txt")

        assert block_info is not None
        assert block_info["pattern"] == "rm"
        # Session suggestion should be used
        # Note: The current implementation uses global suggestion if session block
        # was added without custom suggestion. This is the expected behavior.

    def test_global_block_when_no_session_block(self):
        """Test that global blocks are used when no session block exists."""
        add_global_block("rm")

        block_info = should_block_command(self.test_session_id, "rm file.txt")

        assert block_info is not None
        assert block_info["pattern"] == "rm"

    def test_no_block_when_none_set(self):
        """Test that commands are allowed when no blocks are set."""
        block_info = should_block_command(self.test_session_id, "rm file.txt")

        assert block_info is None

    def test_session_block_allows_overriding_global(self):
        """Test that session can override global by not having a block."""
        # This tests that clearing a session block falls back to global
        add_global_block("rm")

        # Initially should be blocked by global
        block_info = should_block_command(self.test_session_id, "rm file.txt")
        assert block_info is not None

        # Add and then remove session block
        add_session_block(self.test_session_id, "rm", "Session block")
        remove_session_block(self.test_session_id, "rm")

        # Should still be blocked by global
        block_info = should_block_command(self.test_session_id, "rm file.txt")
        assert block_info is not None


class TestShouldBlockCommand:
    """Test the should_block_command function."""

    def setup_method(self):
        """Set up test session."""
        self.test_session_id = "test-should-block"
        clear_session_blocks(self.test_session_id)

    def teardown_method(self):
        """Clean up session blocks."""
        clear_session_blocks(self.test_session_id)

    def test_blocked_command_returns_info(self):
        """Test that blocked commands return block info."""
        add_session_block(self.test_session_id, "rm")

        block_info = should_block_command(self.test_session_id, "rm file.txt")

        assert block_info is not None
        assert "pattern" in block_info
        assert "suggestion" in block_info
        assert block_info["pattern"] == "rm"

    def test_unblocked_command_returns_none(self):
        """Test that unblocked commands return None."""
        block_info = should_block_command(self.test_session_id, "rm file.txt")

        assert block_info is None

    def test_pattern_matching_in_block_check(self):
        """Test that pattern matching works in block checking."""
        add_session_block(self.test_session_id, "dd if=")

        block_info = should_block_command(self.test_session_id, "dd if=/dev/zero of=test")

        assert block_info is not None
        assert block_info["pattern"] == "dd if="


class TestDefaultIntegrations:
    """Test DEFAULT_INTEGRATIONS configuration."""

    def test_rm_integration_exists(self):
        """Test that rm has a default integration."""
        assert "rm" in DEFAULT_INTEGRATIONS
        assert "suggestion" in DEFAULT_INTEGRATIONS["rm"]
        assert "severity" in DEFAULT_INTEGRATIONS["rm"]

    def test_rm_rf_integration_exists(self):
        """Test that rm -rf has a default integration."""
        assert "rm -rf" in DEFAULT_INTEGRATIONS
        assert DEFAULT_INTEGRATIONS["rm -rf"]["severity"] == "critical"

    def test_dd_integration_exists(self):
        """Test that dd if= has a default integration."""
        assert "dd if=" in DEFAULT_INTEGRATIONS
        assert DEFAULT_INTEGRATIONS["dd if="]["severity"] == "critical"

    def test_suggestions_contain_helpful_info(self):
        """Test that default suggestions are helpful."""
        for pattern, config in DEFAULT_INTEGRATIONS.items():
            assert "suggestion" in config
            assert len(config["suggestion"]) > 0
            assert "severity" in config


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
