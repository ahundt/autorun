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

from autorun.main import (
    command_matches_pattern,
    get_session_blocks,
    add_session_block,
    remove_session_block,
    clear_session_blocks,
    get_global_blocks,
    add_global_block,
    remove_global_block,
    GLOBAL_CONFIG_FILE
)
from autorun.config import DEFAULT_INTEGRATIONS
import autorun.integrations as integ
# Daemon-path imports
from autorun.core import EventContext, ThreadSafeDB
from autorun import plugins


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
        self.patcher = patch('autorun.main.GLOBAL_CONFIG_FILE', self.temp_config_file)
        self.patcher.start()

        # Also patch initialize_default_blocks to prevent auto-initialization
        self.init_patcher = patch('autorun.main.initialize_default_blocks', return_value=False)
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
    """Test allow beats block precedence — daemon path check_blocked_commands()."""

    def _ctx(self, command: str) -> EventContext:
        store = ThreadSafeDB()
        return EventContext(
            session_id=f"test-prec-{id(self)}-{command[:8].replace(' ', '_')}",
            event="PreToolUse", tool_name="Bash",
            tool_input={"command": command}, store=store
        )

    def test_session_block_produces_deny(self):
        """Session block pattern causes deny response."""
        ctx = self._ctx("rm file.txt")
        ctx.session_blocked_patterns = [{"pattern": "rm", "suggestion": "use trash-cli"}]
        result = plugins.check_blocked_commands(ctx)
        assert result is not None
        perm = result.get("hookSpecificOutput", {}).get("permissionDecision")
        assert perm == "deny"

    def test_session_allow_beats_session_block(self):
        """Session allow short-circuits session block for same pattern."""
        ctx = self._ctx("rm file.txt")
        ctx.session_blocked_patterns = [{"pattern": "rm", "suggestion": "use trash"}]
        ctx.session_allowed_patterns = [{"pattern": "rm"}]
        result = plugins.check_blocked_commands(ctx)
        if result is not None:
            perm = result.get("hookSpecificOutput", {}).get("permissionDecision")
            assert perm == "allow", f"session allow must beat block, got {perm!r}"

    def test_no_block_when_none_set(self):
        """Commands with no matching block or integration return None."""
        ctx = self._ctx("echo hello world")
        result = plugins.check_blocked_commands(ctx)
        assert result is None

    def test_session_allow_beats_default_integration(self):
        """Session allow pattern overrides DEFAULT_INTEGRATIONS block for rm."""
        ctx = self._ctx("rm file.txt")
        ctx.session_allowed_patterns = [{"pattern": "rm"}]
        result = plugins.check_blocked_commands(ctx)
        if result is not None:
            perm = result.get("hookSpecificOutput", {}).get("permissionDecision")
            assert perm == "allow", f"session allow must beat default integration, got {perm!r}"


class TestShouldBlockCommand:
    """Test command blocking via daemon-path check_blocked_commands()."""

    def _ctx(self, command: str) -> EventContext:
        store = ThreadSafeDB()
        return EventContext(
            session_id=f"test-sbc-{id(self)}-{command[:8].replace(' ', '_')}",
            event="PreToolUse", tool_name="Bash",
            tool_input={"command": command}, store=store
        )

    def test_blocked_command_returns_deny(self):
        """Session-blocked command produces deny with pattern in message."""
        ctx = self._ctx("rm file.txt")
        ctx.session_blocked_patterns = [{"pattern": "rm", "suggestion": "use trash-cli"}]
        result = plugins.check_blocked_commands(ctx)
        assert result is not None
        perm = result.get("hookSpecificOutput", {}).get("permissionDecision")
        assert perm == "deny"
        msg = result.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
        assert "rm" in msg.lower() or "trash" in msg.lower()

    def test_unblocked_command_returns_none(self):
        """Commands with no matching block return None (pass-through)."""
        ctx = self._ctx("echo hello")
        result = plugins.check_blocked_commands(ctx)
        assert result is None

    def test_pattern_matching_in_block_check(self):
        """Session block uses substring pattern matching (dd if= matches dd if=/dev/...)."""
        ctx = self._ctx("dd if=/dev/zero of=test")
        ctx.session_blocked_patterns = [{"pattern": "dd if=", "suggestion": "dangerous disk op"}]
        result = plugins.check_blocked_commands(ctx)
        assert result is not None
        perm = result.get("hookSpecificOutput", {}).get("permissionDecision")
        assert perm == "deny"



class TestPredicateFunctions:
    """Test predicate-based conditional blocking — daemon path."""

    def _ctx(self, command: str) -> EventContext:
        store = ThreadSafeDB()
        return EventContext(
            session_id=f"test-pred-{id(self)}-{command[:8].replace(' ', '_')}",
            event="PreToolUse", tool_name="Bash",
            tool_input={"command": command}, store=store
        )

    def test_predicate_returns_true_blocks(self):
        """When _has_unstaged_changes predicate returns True, git checkout . is denied."""
        with patch.dict(integ._WHEN_PREDICATES, {"_has_unstaged_changes": lambda ctx: True}):
            result = plugins.check_blocked_commands(self._ctx("git checkout ."))
            assert result is not None
            perm = result.get("hookSpecificOutput", {}).get("permissionDecision")
            assert perm == "deny"

    def test_predicate_returns_false_allows(self):
        """When ALL relevant predicates return False, git checkout . is not denied."""
        with patch.dict(integ._WHEN_PREDICATES, {
            "_has_unstaged_changes": lambda ctx: False,
            "_file_has_unstaged_changes": lambda ctx: False,
            "_checkout_targets_file_with_changes": lambda ctx: False,
        }):
            result = plugins.check_blocked_commands(self._ctx("git checkout ."))
            if result is not None:
                perm = result.get("hookSpecificOutput", {}).get("permissionDecision")
                assert perm != "deny", f"predicate=False must not deny, got {perm!r}"

    def test_no_predicate_always_blocks(self):
        """Integrations without 'when' predicate always block (rm has no when field)."""
        result = plugins.check_blocked_commands(self._ctx("rm file.txt"))
        assert result is not None
        perm = result.get("hookSpecificOutput", {}).get("permissionDecision")
        assert perm == "deny"

    def test_stash_drop_blocked_when_stash_exists(self):
        """git stash drop is denied when _stash_exists predicate returns True."""
        with patch.dict(integ._WHEN_PREDICATES, {"_stash_exists": lambda ctx: True}):
            result = plugins.check_blocked_commands(self._ctx("git stash drop"))
            assert result is not None
            perm = result.get("hookSpecificOutput", {}).get("permissionDecision")
            assert perm == "deny"

    def test_stash_drop_allowed_when_no_stash(self):
        """git stash drop is allowed when _stash_exists predicate returns False."""
        with patch.dict(integ._WHEN_PREDICATES, {"_stash_exists": lambda ctx: False}):
            result = plugins.check_blocked_commands(self._ctx("git stash drop"))
            if result is not None:
                perm = result.get("hookSpecificOutput", {}).get("permissionDecision")
                assert perm != "deny", f"no stash → stash drop must not be denied, got {perm!r}"

    def test_suggestion_included_in_block_message(self):
        """Block message includes suggestion from DEFAULT_INTEGRATIONS."""
        result = plugins.check_blocked_commands(self._ctx("rm file.txt"))
        assert result is not None
        msg = result.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
        assert msg, "block message must not be empty"
        assert "trash" in msg.lower() or "rm" in msg.lower(), \
            f"rm block message must mention trash or rm, got: {msg[:100]!r}"


# ============================================================================
# Daemon-path tests — use EventContext + plugins.check_blocked_commands()
# ============================================================================

class TestRuleStacking:
    """Verify the stacking fix: multiple matching rules combine, deny wins over warn.

    Regression tests for the bug where rm -f foo && git status showed only the
    git warn message (last match won), discarding the rm deny.  The fix in
    plugins.py:check_blocked_commands() collects ALL matching deny_parts and
    warn_parts, then returns a single combined response with deny-wins semantics.
    """

    def _ctx(self, command: str, session_id: str = None) -> EventContext:
        store = ThreadSafeDB()
        return EventContext(
            session_id=session_id or f"test-stack-{id(self)}-{command[:8]}",
            event="PreToolUse", tool_name="Bash",
            tool_input={"command": command}, store=store
        )

    def _msg(self, result: dict) -> str:
        """Extract the combined message from hookSpecificOutput.permissionDecisionReason."""
        return result.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")

    def test_deny_and_warn_both_appear_in_combined_message(self):
        """rm && git status → deny for rm, warn for git; BOTH messages in combined."""
        ctx = self._ctx("rm -f foo.ts && git status")
        result = plugins.check_blocked_commands(ctx)
        assert result is not None, "rm && git should produce a response"
        perm = result.get("hookSpecificOutput", {}).get("permissionDecision")
        assert perm == "deny", f"Expected deny (rm blocks), got {perm!r}"
        msg = self._msg(result)
        assert "rm" in msg.lower() or "trash" in msg.lower(), \
            "combined message must mention rm block"
        assert "CLAUDE.md" in msg or "git" in msg.lower(), \
            "combined message must include git warn"

    def test_deny_wins_over_warn(self):
        """Decision is deny whenever any deny-action integration matches, regardless of warns."""
        ctx = self._ctx("rm -f foo.ts && git status")
        result = plugins.check_blocked_commands(ctx)
        assert result is not None
        perm = result.get("hookSpecificOutput", {}).get("permissionDecision")
        assert perm == "deny", f"deny must win over warn, got {perm!r}"

    def test_warn_only_produces_allow_with_message(self):
        """git status alone (warn-only) → permissionDecision=allow, message in systemMessage."""
        ctx = self._ctx("git status")
        result = plugins.check_blocked_commands(ctx)
        assert result is not None, "git status should trigger the warn integration"
        perm = result.get("hookSpecificOutput", {}).get("permissionDecision")
        assert perm == "allow", f"warn-only must be allow, got {perm!r}"
        msg = result.get("systemMessage", "") + result.get("reason", "")
        assert "CLAUDE.md" in msg or "git" in msg.lower(), \
            "warn message should mention CLAUDE.md or git requirements"

    def test_deny_only_produces_deny(self):
        """rm alone (deny-only) → permissionDecision=deny."""
        ctx = self._ctx("rm important.txt")
        result = plugins.check_blocked_commands(ctx)
        assert result is not None
        perm = result.get("hookSpecificOutput", {}).get("permissionDecision")
        assert perm == "deny", f"rm must be denied, got {perm!r}"

    def test_deduplication_session_block_and_default_integration(self):
        """Same pattern in session block AND default integration appears only once."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id=f"test-dedup-{id(self)}",
            event="PreToolUse", tool_name="Bash",
            tool_input={"command": "rm foo"}, store=store
        )
        # Add a session block for the same "rm" pattern that's also in DEFAULT_INTEGRATIONS
        ctx.session_blocked_patterns = [{"pattern": "rm", "suggestion": "use trash-dedup-marker"}]
        result = plugins.check_blocked_commands(ctx)
        assert result is not None
        msg = self._msg(result)
        # The session block message should appear exactly once (dedup by pattern string)
        assert msg.count("use trash-dedup-marker") == 1, \
            f"Deduped message should appear once, got: {msg.count('use trash-dedup-marker')}"

    def test_session_allow_short_circuits_all_blocks(self):
        """Session allow pattern beats session block and default integrations."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id=f"test-allow-sc-{id(self)}",
            event="PreToolUse", tool_name="Bash",
            tool_input={"command": "rm foo"}, store=store
        )
        ctx.session_blocked_patterns = [{"pattern": "rm", "suggestion": "use trash"}]
        ctx.session_allowed_patterns = [{"pattern": "rm"}]
        result = plugins.check_blocked_commands(ctx)
        # Allow wins → either None (pass-through) or explicit allow
        if result is not None:
            perm = result.get("hookSpecificOutput", {}).get("permissionDecision")
            assert perm == "allow", f"session allow must beat block, got {perm!r}"

    def test_multiple_deny_patterns_all_appear(self):
        """Command matching rm AND rm -rf both get messages (different patterns)."""
        ctx = self._ctx("rm -rf /tmp/testdir")
        result = plugins.check_blocked_commands(ctx)
        assert result is not None
        perm = result.get("hookSpecificOutput", {}).get("permissionDecision")
        assert perm == "deny"
        msg = self._msg(result)
        # Both rm and rm -rf patterns should contribute (different patterns, not deduped)
        assert "rm" in msg.lower() or "trash" in msg.lower()

    def test_session_no_suppresses_default_warn_same_pattern(self):
        """Pattern-only dedup: /ar:no git (deny) suppresses DEFAULT git (warn) for same pattern.

        Regression test for the (pattern, decision) dedup key bug:
        - OLD: key ("git","deny") ≠ ("git","warn") → BOTH messages appeared (wrong)
        - NEW: key "git" claimed by session block → DEFAULT git warn SKIPPED (correct)
        User's explicit /ar:no block replaces the default regardless of action type.
        """
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id=f"test-dedup-warn-{id(self)}",
            event="PreToolUse", tool_name="Bash",
            tool_input={"command": "git status"}, store=store
        )
        # Simulate /ar:no git with a custom deny message
        ctx.session_blocked_patterns = [{"pattern": "git", "suggestion": "custom-git-block-msg"}]
        result = plugins.check_blocked_commands(ctx)
        assert result is not None
        perm = result.get("hookSpecificOutput", {}).get("permissionDecision")
        assert perm == "deny", f"session block must deny, got {perm!r}"
        msg = self._msg(result)
        assert "custom-git-block-msg" in msg, "custom session block message must appear"
        assert "CLAUDE.md" not in msg, \
            "DEFAULT git warn must NOT appear when session block claims same pattern"

    def test_session_no_does_not_suppress_more_specific_pattern(self):
        """/ar:no rm (deny) does NOT suppress DEFAULT rm -rf (different pattern → both shown)."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id=f"test-dedup-specific-{id(self)}",
            event="PreToolUse", tool_name="Bash",
            tool_input={"command": "rm -rf /tmp/testdir"}, store=store
        )
        # Session block for "rm" — different from "rm -rf"
        ctx.session_blocked_patterns = [{"pattern": "rm", "suggestion": "custom-rm-block-msg"}]
        result = plugins.check_blocked_commands(ctx)
        assert result is not None
        perm = result.get("hookSpecificOutput", {}).get("permissionDecision")
        assert perm == "deny"
        msg = self._msg(result)
        # "rm" session block MUST appear
        assert "custom-rm-block-msg" in msg, "session block for 'rm' must appear"
        # DEFAULT "rm -rf" integration is a DIFFERENT pattern — MUST also appear
        assert "rm -rf" in msg.lower() or "trash" in msg.lower(), \
            "DEFAULT rm -rf message must also appear (different pattern from 'rm')"

    def test_safe_command_returns_none(self):
        """Commands matching no integration return None (pure pass-through).

        With the dispatch() fix, None from check_blocked_commands propagates to dispatch()
        returning None → AutorunDaemon sends {} → client exits 0 with no stdout.
        This allows parallel hooks (RTK) to apply updatedInput without conflict.
        """
        ctx = self._ctx("echo hello")
        result = plugins.check_blocked_commands(ctx)
        assert result is None, f"safe command must return None, got {result!r}"

    def test_ls_command_returns_none_for_rtk_passthrough(self):
        """ls -alh matches no rule → None → RTK can substitute 'rtk ls -alh' unblocked.

        RTK (Rust Token Killer) uses updatedInput in parallel PreToolUse hook responses.
        When autorun outputs nothing (None → {} → exit 0 no stdout), Claude Code uses
        only RTK's response, applying the token-efficient substitution (60-90% savings).
        """
        ctx = self._ctx("ls -alh /tmp")
        result = plugins.check_blocked_commands(ctx)
        assert result is None, f"ls must return None for RTK passthrough, got {result!r}"


class TestGitCommandTargeting:
    """Verify git blocking targets only dangerous commands, not safe ones — daemon path.

    Replaces the removed TestGitBlockingTargeting with corrected assertions:
    git restore IS in DEFAULT_INTEGRATIONS (as block action, not warn).
    """

    def _ctx(self, command: str) -> EventContext:
        store = ThreadSafeDB()
        return EventContext(
            session_id=f"test-gittgt-{id(self)}-{command[:12].replace(' ', '_')}",
            event="PreToolUse", tool_name="Bash",
            tool_input={"command": command}, store=store
        )

    def test_git_checkout_dot_is_denied(self):
        """git checkout . → deny (discards all unstaged changes)."""
        result = plugins.check_blocked_commands(self._ctx("git checkout ."))
        assert result is not None
        perm = result.get("hookSpecificOutput", {}).get("permissionDecision")
        assert perm == "deny", f"git checkout . must be denied, got {perm!r}"

    def test_git_checkout_branch_is_not_denied(self):
        """git checkout main → not denied (checking out a branch is safe)."""
        result = plugins.check_blocked_commands(self._ctx("git checkout main"))
        # Should be None or allow — NOT deny
        if result is not None:
            perm = result.get("hookSpecificOutput", {}).get("permissionDecision")
            assert perm != "deny", f"git checkout main must not be denied, got {perm!r}"

    def test_git_reset_hard_is_denied(self):
        """git reset --hard HEAD → deny."""
        result = plugins.check_blocked_commands(self._ctx("git reset --hard HEAD"))
        assert result is not None
        perm = result.get("hookSpecificOutput", {}).get("permissionDecision")
        assert perm == "deny"

    def test_git_clean_f_is_denied(self):
        """git clean -f → deny."""
        result = plugins.check_blocked_commands(self._ctx("git clean -f"))
        assert result is not None
        perm = result.get("hookSpecificOutput", {}).get("permissionDecision")
        assert perm == "deny"

    def test_git_stash_is_not_in_default_integrations(self):
        """git stash and variants are safe alternatives — not blocked."""
        for pattern in ("git stash", "git stash push", "git stash pop"):
            assert pattern not in DEFAULT_INTEGRATIONS, \
                f"'{pattern}' is a safe alternative and should NOT be blocked"

    def test_git_revert_is_not_in_default_integrations(self):
        """git revert creates a new commit — safe, not blocked."""
        assert "git revert" not in DEFAULT_INTEGRATIONS

    def test_git_restore_is_in_default_integrations_as_block(self):
        """git restore IS in DEFAULT_INTEGRATIONS as block (discards unstaged changes)."""
        assert "git restore" in DEFAULT_INTEGRATIONS, \
            "git restore discards unstaged changes and should be in DEFAULT_INTEGRATIONS"
        assert DEFAULT_INTEGRATIONS["git restore"].get("action") in ("block", None), \
            "git restore should be block action (None defaults to block)"

    def test_git_reset_head_has_no_redirect(self):
        """git reset HEAD~ has no redirect (keeps changes in working dir)."""
        assert "git reset HEAD~" in DEFAULT_INTEGRATIONS
        assert "redirect" not in DEFAULT_INTEGRATIONS["git reset HEAD~"]

    def test_dangerous_git_commands_blocked_via_daemon_path(self):
        """All three primary dangerous git commands are denied by check_blocked_commands."""
        dangerous = [
            "git reset --hard HEAD",
            "git checkout .",
            "git clean -f",
        ]
        for cmd in dangerous:
            result = plugins.check_blocked_commands(self._ctx(cmd))
            assert result is not None, f"'{cmd}' must produce a response"
            perm = result.get("hookSpecificOutput", {}).get("permissionDecision")
            assert perm == "deny", f"'{cmd}' must be denied, got {perm!r}"


class TestGrepFalsePositiveProtection:
    """Verify grep block never fires on non-Bash tool calls (AI tools, file ops).

    Background: User reported "grep block fires on Read tool" (Task #6).
    Root cause analysis confirmed current code is correct:
    - Claude Code claude-hooks.json PreToolUse matcher: "Write|Edit|Bash|ExitPlanMode"
      → Read, Grep, Glob AI tools never trigger the hook at the CLI level.
    - check_blocked_commands() returns None early for any tool_name not in
      BASH_TOOLS = {"Bash", "bash_command", "run_shell_command"} or
      FILE_TOOLS = {"Write", "write_file", "Edit", "edit_file", "replace"}.
    - Gemini CLI grep_search tool name is NOT in BASH_TOOLS, returns None immediately.

    These tests protect against regressions that would introduce false positives.
    """

    def _ctx(self, tool_name: str, tool_input: dict, session_id: str = None) -> EventContext:
        store = ThreadSafeDB()
        return EventContext(
            session_id=session_id or f"test-grep-fp-{id(self)}-{tool_name}",
            event="PreToolUse", tool_name=tool_name,
            tool_input=tool_input, store=store
        )

    def test_read_tool_with_grep_in_path_returns_none(self):
        """Read tool (Claude) with 'grep' in file path must not trigger grep block."""
        ctx = self._ctx("Read", {"file_path": "/path/to/grep_results.py"})
        result = plugins.check_blocked_commands(ctx)
        assert result is None, \
            f"Read tool must return None (hook-level: never reaches daemon for Read), got {result!r}"

    def test_read_tool_simple_returns_none(self):
        """Read tool with any file path must always return None."""
        ctx = self._ctx("Read", {"file_path": "/Users/user/project/src/main.py"})
        result = plugins.check_blocked_commands(ctx)
        assert result is None, f"Read tool must always return None, got {result!r}"

    def test_grep_ai_tool_returns_none(self):
        """Grep AI tool (Claude Code 'Grep') must not trigger grep block."""
        ctx = self._ctx("Grep", {"pattern": "command_matches", "path": "src/"})
        result = plugins.check_blocked_commands(ctx)
        assert result is None, \
            f"Grep AI tool must return None (not a bash command), got {result!r}"

    def test_glob_ai_tool_returns_none(self):
        """Glob AI tool must not trigger any block."""
        ctx = self._ctx("Glob", {"pattern": "**/*.py", "path": "src/"})
        result = plugins.check_blocked_commands(ctx)
        assert result is None, f"Glob AI tool must return None, got {result!r}"

    def test_gemini_grep_search_tool_returns_none(self):
        """Gemini CLI grep_search tool must not trigger grep block.

        Gemini CLI's BeforeTool fires for grep_search, but check_blocked_commands
        must return None since grep_search is not in BASH_TOOLS or FILE_TOOLS.
        """
        ctx = self._ctx("grep_search", {"pattern": "rm -rf", "path": "."})
        result = plugins.check_blocked_commands(ctx)
        assert result is None, \
            f"Gemini grep_search AI tool must return None (not bash), got {result!r}"

    def test_gemini_read_file_tool_returns_none(self):
        """Gemini CLI read_file tool must not trigger grep block.

        Gemini CLI's BeforeTool fires for read_file, but check_blocked_commands
        must return None since read_file is not in BASH_TOOLS or FILE_TOOLS.
        """
        ctx = self._ctx("read_file", {"file_path": "/path/to/grep_test.py"})
        result = plugins.check_blocked_commands(ctx)
        assert result is None, \
            f"Gemini read_file tool must return None, got {result!r}"

    def test_gemini_glob_tool_returns_none(self):
        """Gemini CLI glob tool must not trigger any block."""
        ctx = self._ctx("glob", {"pattern": "**/*.py"})
        result = plugins.check_blocked_commands(ctx)
        assert result is None, f"Gemini glob tool must return None, got {result!r}"

    def test_bash_grep_not_in_pipe_is_correctly_blocked(self):
        """Bash tool with grep command (not in pipe) IS correctly blocked.

        This verifies the EXPECTED behavior: bash grep outside a pipe should be
        blocked and the AI should use the Grep AI tool instead.
        """
        ctx = self._ctx("Bash", {"command": "grep -r 'pattern' ."})
        result = plugins.check_blocked_commands(ctx)
        assert result is not None, "bash grep (not in pipe) must be blocked"
        perm = result.get("hookSpecificOutput", {}).get("permissionDecision")
        assert perm == "deny", f"bash grep must be denied, got {perm!r}"
        msg = result.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
        assert "grep" in msg.lower(), "block message must mention grep"

    def test_bash_grep_in_pipe_is_allowed(self):
        """Bash tool with grep in pipe is correctly allowed.

        'ps aux | grep python' is a common safe pipe usage — should not be blocked.
        '_not_in_pipe' predicate must return False (allow) for this case.
        """
        ctx = self._ctx("Bash", {"command": "ps aux | grep python"})
        result = plugins.check_blocked_commands(ctx)
        # Should be None (no block) or allow — NOT deny for grep in pipe
        if result is not None:
            perm = result.get("hookSpecificOutput", {}).get("permissionDecision")
            assert perm != "deny", \
                f"grep in pipe must not be denied, got {perm!r} for 'ps aux | grep python'"

    def test_pytest_command_with_grep_in_test_filter_not_blocked(self):
        """pytest -k 'grep' must not trigger grep block.

        The grep keyword appears in the -k filter argument, not as a command.
        'uv run pytest -k grep' should not match the grep integration.
        """
        ctx = self._ctx("Bash", {"command": "uv run pytest plugins/autorun/tests/ -k 'grep'"})
        result = plugins.check_blocked_commands(ctx)
        # Should be None (uv/pytest are not in DEFAULT_INTEGRATIONS as blocks)
        # OR at most a warn (but NOT a deny for grep)
        if result is not None:
            perm = result.get("hookSpecificOutput", {}).get("permissionDecision")
            msg = result.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
            # If there IS a response, it must not be a grep deny
            if perm == "deny":
                assert "grep" not in msg.lower() or "bash grep" in msg.lower(), \
                    f"pytest -k 'grep' must not trigger grep block, got: {msg[:100]!r}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
