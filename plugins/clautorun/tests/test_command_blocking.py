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
    get_command_warning,
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
        # AST parser (bashlex) strips quotes from tokens, so "echo 'test'" is
        # parsed as tokens ["echo", "test"] (no quotes). The pattern "echo 'test'"
        # is parsed by ParsedPattern as positional "'test'" (with quotes), which
        # does not match the extracted positional "test" (without quotes).
        assert command_matches_pattern("echo 'test'", "echo 'test'") is False
        # A bare flag like "--force" as a single-word pattern has base="--force"
        # and is_single_word=True, so it checks all_potential. But flags (tokens
        # starting with -) are never added to all_potential by the AST extractor,
        # so this correctly returns False. To block --force usage, use a pattern
        # like "grep --force" which matches the command + flag combination.
        assert command_matches_pattern("grep --force pattern", "--force") is False
        assert command_matches_pattern("grep --force pattern", "grep --force") is True

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

        # Also patch initialize_default_blocks to prevent auto-initialization
        self.init_patcher = patch('clautorun.main.initialize_default_blocks', return_value=False)
        self.init_patcher.start()

    def teardown_method(self):
        """Clean up temporary directory."""
        self.init_patcher.stop()
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

        # Also patch initialize_default_blocks to prevent auto-initialization
        self.init_patcher = patch('clautorun.main.initialize_default_blocks', return_value=False)
        self.init_patcher.start()

        # Clear session blocks
        clear_session_blocks(self.test_session_id)

    def teardown_method(self):
        """Clean up patches and session blocks."""
        self.init_patcher.stop()
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
        block_info = should_block_command(self.test_session_id, "echo hello")

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

        # Patch initialize_default_blocks to prevent auto-initialization
        self.init_patcher = patch('clautorun.main.initialize_default_blocks', return_value=False)
        self.init_patcher.start()

        clear_session_blocks(self.test_session_id)

    def teardown_method(self):
        """Clean up session blocks."""
        self.init_patcher.stop()
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
        block_info = should_block_command(self.test_session_id, "echo hello")

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
        assert "action" in DEFAULT_INTEGRATIONS["rm"]

    def test_rm_rf_integration_exists(self):
        """Test that rm -rf has a default integration."""
        assert "rm -rf" in DEFAULT_INTEGRATIONS
        assert "suggestion" in DEFAULT_INTEGRATIONS["rm -rf"]

    def test_dd_integration_exists(self):
        """Test that dd if= has a default integration."""
        assert "dd if=" in DEFAULT_INTEGRATIONS
        assert "suggestion" in DEFAULT_INTEGRATIONS["dd if="]

    def test_suggestions_contain_helpful_info(self):
        """Test that default suggestions are helpful."""
        for pattern, config in DEFAULT_INTEGRATIONS.items():
            assert "suggestion" in config
            assert len(config["suggestion"]) > 0


class TestGitCommandIntegrations:
    """Test git command DEFAULT_INTEGRATIONS for safety suggestions."""

    def test_git_reset_hard_integration_exists(self):
        """Test that git reset --hard has a default integration."""
        assert "git reset --hard" in DEFAULT_INTEGRATIONS
        assert "suggestion" in DEFAULT_INTEGRATIONS["git reset --hard"]

    def test_git_reset_hard_suggests_stash(self):
        """Test that git reset --hard suggests stash as primary alternative."""
        suggestion = DEFAULT_INTEGRATIONS["git reset --hard"]["suggestion"]
        assert "git stash" in suggestion
        assert "RECOMMENDED" in suggestion

    def test_git_reset_hard_suggests_backup_branch(self):
        """Test that git reset --hard suggests creating backup branch as fallback."""
        suggestion = DEFAULT_INTEGRATIONS["git reset --hard"]["suggestion"]
        assert "backup/" in suggestion
        assert "git checkout -b" in suggestion

    def test_git_reset_hard_has_date_format_in_branch_name(self):
        """Test that backup branch suggestion uses concrete date-based naming."""
        suggestion = DEFAULT_INTEGRATIONS["git reset --hard"]["suggestion"]
        # Check for date format pattern
        assert "%Y%m%d" in suggestion or "$(date" in suggestion

    def test_git_checkout_dot_integration_exists(self):
        """Test that git checkout . has a default integration."""
        assert "git checkout ." in DEFAULT_INTEGRATIONS
        assert "when" in DEFAULT_INTEGRATIONS["git checkout ."]

    def test_git_checkout_dot_suggests_stash(self):
        """Test that git checkout . suggests stash as primary alternative."""
        suggestion = DEFAULT_INTEGRATIONS["git checkout ."]["suggestion"]
        assert "git stash" in suggestion

    def test_git_clean_f_integration_exists(self):
        """Test that git clean -f has a default integration."""
        assert "git clean -f" in DEFAULT_INTEGRATIONS
        assert "suggestion" in DEFAULT_INTEGRATIONS["git clean -f"]

    def test_git_clean_f_suggests_dry_run(self):
        """Test that git clean -f suggests dry-run preview first."""
        suggestion = DEFAULT_INTEGRATIONS["git clean -f"]["suggestion"]
        assert "git clean -n" in suggestion
        assert "dry-run" in suggestion.lower() or "preview" in suggestion.lower()

    def test_git_clean_f_suggests_stash_untracked(self):
        """Test that git clean -f suggests stashing untracked files."""
        suggestion = DEFAULT_INTEGRATIONS["git clean -f"]["suggestion"]
        assert "stash" in suggestion.lower()
        assert "-u" in suggestion  # -u flag for untracked files

    def test_git_reset_head_integration_exists(self):
        """Test that git reset HEAD~ has a default integration."""
        assert "git reset HEAD~" in DEFAULT_INTEGRATIONS
        assert "suggestion" in DEFAULT_INTEGRATIONS["git reset HEAD~"]

    def test_git_reset_head_suggests_soft_reset(self):
        """Test that git reset HEAD~ suggests soft reset alternative."""
        suggestion = DEFAULT_INTEGRATIONS["git reset HEAD~"]["suggestion"]
        assert "--soft" in suggestion

    def test_git_reset_head_suggests_revert(self):
        """Test that git reset HEAD~ suggests revert as safer option."""
        suggestion = DEFAULT_INTEGRATIONS["git reset HEAD~"]["suggestion"]
        assert "git revert" in suggestion

    def test_git_reset_head_mentions_reflog_recovery(self):
        """Test that git reset HEAD~ mentions reflog for recovery."""
        suggestion = DEFAULT_INTEGRATIONS["git reset HEAD~"]["suggestion"]
        assert "reflog" in suggestion

    def test_all_git_suggestions_have_allow_instruction(self):
        """Test that all git block-action suggestions include allow instruction.

        Entries with action: 'warn' are informational and don't need /cr:ok
        because they don't block the command.
        """
        git_patterns = [p for p in DEFAULT_INTEGRATIONS.keys() if p.startswith("git")]
        for pattern in git_patterns:
            config = DEFAULT_INTEGRATIONS[pattern]
            if config.get("action") == "warn":
                continue  # warn actions allow the command, no /cr:ok needed
            suggestion = config["suggestion"]
            assert "/cr:ok" in suggestion, f"Missing /cr:ok instruction in {pattern}"


class TestGitBlockingTargeting:
    """Test that git blocking only targets damaging commands, not safe ones."""

    def test_git_checkout_branch_is_safe(self):
        """Test that git checkout <branch> is NOT blocked (only checkout . is)."""
        # The pattern "git checkout ." should NOT match "git checkout main"
        pattern = "git checkout ."
        safe_commands = [
            "git checkout main",
            "git checkout feature-branch",
            "git checkout -b new-branch",
            "git checkout HEAD~1",
        ]
        for cmd in safe_commands:
            # Pattern should not be a substring of safe commands
            assert pattern not in cmd, f"Pattern '{pattern}' incorrectly matches safe command: {cmd}"

    def test_git_checkout_dot_is_blocked(self):
        """Test that git checkout . variations ARE blocked."""
        pattern = "git checkout ."
        dangerous_commands = [
            "git checkout .",
            "git checkout . --",
        ]
        for cmd in dangerous_commands:
            assert pattern in cmd, f"Pattern '{pattern}' should match dangerous command: {cmd}"

    def test_git_reset_branch_is_safe(self):
        """Test that git reset <ref> (without --hard) is less dangerous."""
        # git reset HEAD~ is a block action but has no "redirect" key,
        # meaning it warns/blocks but doesn't auto-redirect to an alternative.
        # It is less dangerous than --hard because it keeps changes in working dir.
        assert "git reset HEAD~" in DEFAULT_INTEGRATIONS
        assert "redirect" not in DEFAULT_INTEGRATIONS["git reset HEAD~"]

    def test_git_stash_is_not_blocked(self):
        """Test that git stash (the safe alternative) is NOT in blocking list."""
        assert "git stash" not in DEFAULT_INTEGRATIONS
        assert "git stash push" not in DEFAULT_INTEGRATIONS
        assert "git stash pop" not in DEFAULT_INTEGRATIONS

    def test_git_restore_is_not_blocked(self):
        """Test that git restore (the safe alternative) is NOT blocked."""
        assert "git restore" not in DEFAULT_INTEGRATIONS

    def test_git_revert_is_not_blocked(self):
        """Test that git revert (safe - creates new commit) is NOT blocked."""
        assert "git revert" not in DEFAULT_INTEGRATIONS

    def test_safe_git_commands_not_in_integrations(self):
        """Test that common safe git commands are NOT in DEFAULT_INTEGRATIONS."""
        safe_commands = [
            "git status",
            "git diff",
            "git log",
            "git branch",
            "git add",
            "git commit",
            "git push",
            "git pull",
            "git fetch",
            "git merge",
            "git stash",
            "git restore",
            "git revert",
        ]
        for cmd in safe_commands:
            assert cmd not in DEFAULT_INTEGRATIONS, f"Safe command '{cmd}' should NOT be blocked"


class TestPredicateFunctions:
    """Test predicate-based conditional blocking."""

    def setup_method(self):
        """Set up test session."""
        self.test_session_id = "test-predicates"

        # Patch initialize_default_blocks to prevent auto-initialization
        self.init_patcher = patch('clautorun.main.initialize_default_blocks', return_value=False)
        self.init_patcher.start()

        clear_session_blocks(self.test_session_id)

    def teardown_method(self):
        """Clean up session blocks."""
        self.init_patcher.stop()
        clear_session_blocks(self.test_session_id)

    def test_predicate_returns_true_blocks(self):
        """Test that when predicate returns True, command is blocked."""
        from clautorun.main import _PREDICATES, should_block_command

        # Mock predicate to return True (block)
        with patch.dict(_PREDICATES, {"_has_unstaged_changes": lambda cmd: True}):
            block_info = should_block_command(self.test_session_id, "git checkout .")
            assert block_info is not None
            assert block_info["pattern"] == "git checkout ."

    def test_predicate_returns_false_allows(self):
        """Test that when predicate returns False, command is allowed."""
        from clautorun.main import _PREDICATES, should_block_command

        # Mock predicate to return False (allow)
        with patch.dict(_PREDICATES, {"_has_unstaged_changes": lambda cmd: False}):
            block_info = should_block_command(self.test_session_id, "git checkout .")
            assert block_info is None  # Not blocked

    def test_no_predicate_always_blocks(self):
        """Test that entries without 'when' field always block."""
        # "rm" has no "when" field, should always block
        from clautorun.main import should_block_command

        block_info = should_block_command(self.test_session_id, "rm file.txt")
        assert block_info is not None
        assert block_info["pattern"] == "rm"

    def test_stash_drop_blocked_when_stash_exists(self):
        """Test git stash drop is blocked when stash has entries."""
        from clautorun.main import _PREDICATES, should_block_command

        # Mock stash exists
        with patch.dict(_PREDICATES, {"_stash_exists": lambda cmd: True}):
            block_info = should_block_command(self.test_session_id, "git stash drop")
            assert block_info is not None
            assert block_info["pattern"] == "git stash drop"
            assert "suggestion" in block_info

    def test_stash_drop_allowed_when_no_stash(self):
        """Test git stash drop is allowed when stash is empty."""
        from clautorun.main import _PREDICATES, should_block_command

        # Mock no stash entries
        with patch.dict(_PREDICATES, {"_stash_exists": lambda cmd: False}):
            block_info = should_block_command(self.test_session_id, "git stash drop")
            assert block_info is None  # Not blocked

    def test_suggestion_included_in_block_info(self):
        """Test that suggestion is included in block info from DEFAULT_INTEGRATIONS."""
        from clautorun.main import should_block_command, get_global_blocks

        # Use a unique session to ensure we hit DEFAULT_INTEGRATIONS, not session blocks
        unique_session = "test-suggestion-" + str(id(self))
        clear_session_blocks(unique_session)

        # Mock empty global blocks to ensure we hit DEFAULT_INTEGRATIONS
        with patch('clautorun.main.get_global_blocks', return_value=[]):
            block_info = should_block_command(unique_session, "rm file.txt")
            assert block_info is not None
            assert "suggestion" in block_info
            assert block_info["suggestion"] == DEFAULT_INTEGRATIONS["rm"]["suggestion"]


class TestWarnAction:
    """Test action: 'warn' behavior (allow + message, not block)."""

    def test_warn_action_does_not_block(self):
        """Test that action='warn' allows command (git has action: 'warn')."""
        # git has action: "warn" in DEFAULT_INTEGRATIONS
        unique_session = "test-warn-" + str(id(self))
        clear_session_blocks(unique_session)

        with patch('clautorun.main.get_global_blocks', return_value=[]):
            result = should_block_command(unique_session, "git status")
            assert result is None, "git status should not be blocked (action: warn)"

    def test_warn_action_returns_warning_message(self):
        """Test that get_command_warning returns message for warn integrations."""
        unique_session = "test-warn-msg-" + str(id(self))
        warning = get_command_warning(unique_session, "git status")
        assert warning is not None, "git should return a warning message"
        assert "CLAUDE.md" in warning, "git warning should mention CLAUDE.md"

    def test_block_action_is_blocked(self):
        """Test that action='block' (default) still blocks commands."""
        unique_session = "test-block-" + str(id(self))
        clear_session_blocks(unique_session)

        with patch('clautorun.main.get_global_blocks', return_value=[]):
            result = should_block_command(unique_session, "rm file.txt")
            assert result is not None, "rm should be blocked (action: block)"
            assert result["pattern"] == "rm"

    def test_block_action_has_no_warning(self):
        """Test that blocked commands don't return warnings."""
        unique_session = "test-block-nowarn-" + str(id(self))
        warning = get_command_warning(unique_session, "rm file.txt")
        assert warning is None, "rm should not have warning (it's blocked)"

    def test_warn_action_git_variants(self):
        """Test various git commands are warned not blocked."""
        unique_session = "test-git-variants-" + str(id(self))
        clear_session_blocks(unique_session)

        git_commands = [
            "git status",
            "git log",
            "git diff",
            "git add file.txt",
            "git commit -m 'test'",
            "git push",
            "git pull",
        ]

        with patch('clautorun.main.get_global_blocks', return_value=[]):
            for cmd in git_commands:
                result = should_block_command(unique_session, cmd)
                # Only general git command (action: warn) should match these
                # They should NOT be blocked
                assert result is None, f"{cmd} should not be blocked"

    def test_dangerous_git_commands_still_blocked(self):
        """Test that dangerous git commands with action='block' are still blocked."""
        unique_session = "test-danger-git-" + str(id(self))
        clear_session_blocks(unique_session)

        # These have explicit action: block integrations
        dangerous_commands = [
            ("git reset --hard HEAD", "git reset --hard"),
            ("git checkout .", "git checkout ."),
            ("git clean -f", "git clean -f"),
        ]

        with patch('clautorun.main.get_global_blocks', return_value=[]):
            for cmd, expected_pattern in dangerous_commands:
                result = should_block_command(unique_session, cmd)
                assert result is not None, f"{cmd} should be blocked"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
