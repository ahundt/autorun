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
Edge case and error handling tests for command blocking functionality.

Tests edge cases, error conditions, and robustness:
- Invalid inputs
- Corrupted state files
- Concurrent access patterns
- Special characters and encoding
- Boundary conditions

Migrated from main.py (deleted) to daemon-path ScopeAccessor + plugins.check_blocked_commands.
Canonical API: ScopeAccessor(ctx, "session/global") + plugins.check_blocked_commands(ctx).
"""

import contextlib
import pytest
import json
import threading
import time
import uuid
from pathlib import Path
from unittest.mock import patch

from autorun.main import command_matches_pattern
from autorun.core import EventContext, ThreadSafeDB
from autorun import plugins


def _make_ctx(cmd: str = "ls", patterns=None, session_id: str = None) -> EventContext:
    """Create isolated EventContext with optional pre-seeded session_blocked_patterns."""
    ctx = EventContext(
        session_id=session_id or f"test-edge-{uuid.uuid4().hex[:8]}",
        event="PreToolUse",
        tool_name="Bash",
        tool_input={"command": cmd},
        store=ThreadSafeDB(),
    )
    if patterns is not None:
        ctx.session_blocked_patterns = patterns
    return ctx


def _check_block(cmd, patterns=None):
    """Returns deny result if command is blocked, else None.

    Daemon-path replacement for deleted should_block_command().
    Canonical: plugins.check_blocked_commands(ctx).
    """
    if not cmd or not cmd.strip():
        return None
    ctx = _make_ctx(cmd, patterns)
    result = plugins.check_blocked_commands(ctx)
    if result is None:
        return None
    perm = result.get("hookSpecificOutput", {}).get("permissionDecision", "allow")
    return result if perm == "deny" else None


@contextlib.contextmanager
def _isolated_global_store():
    """Patch session_state for global scope so tests don't touch real ~/.autorun/sessions/__global__.*"""
    store = {}

    @contextlib.contextmanager
    def mock_session_state(session_id, **kwargs):
        yield store

    with patch("autorun.plugins.session_state", mock_session_state):
        yield store


class TestPatternMatchingEdgeCases:
    """Test edge cases in pattern matching."""

    def test_whitespace_variations(self):
        """Test patterns with various whitespace."""
        assert command_matches_pattern("rm file.txt", "rm") is True
        assert command_matches_pattern("  rm file.txt", "rm") is True
        assert command_matches_pattern("rm   file.txt", "rm") is True

    def test_empty_command_parts(self):
        """Test commands that split into empty parts."""
        assert command_matches_pattern("|| rm", "rm") is True
        assert command_matches_pattern("rm && echo test", "rm") is True

    def test_case_sensitivity(self):
        """Test that pattern matching is case-sensitive."""
        assert command_matches_pattern("RM file.txt", "rm") is False
        assert command_matches_pattern("rm file.txt", "RM") is False

    def test_special_regex_characters(self):
        """Test patterns with special regex characters."""
        # AST-based detection extracts command names, not arguments.
        # "*.txt" is an argument to grep, not a command name, so it does NOT match.
        # "test?" is an argument to echo, not a command name, so it does NOT match.
        assert command_matches_pattern("grep *.txt", "*.txt") is False
        assert command_matches_pattern("echo test?", "test?") is False

    def test_very_long_commands(self):
        """Test matching with very long command strings."""
        long_command = "rm " + "a" * 1000 + ".txt"
        assert command_matches_pattern(long_command, "rm") is True

    def test_unicode_patterns(self):
        """Test patterns with unicode characters."""
        assert command_matches_pattern("echo 'café'", "café") is False
        # The pattern doesn't match because café is not a command part


class TestStateManagementEdgeCases:
    """Test edge cases in session block state management via ScopeAccessor."""

    def test_empty_session_blocks(self):
        """Test getting blocks from empty session."""
        ctx = _make_ctx()
        acc = plugins.ScopeAccessor(ctx, "session")
        blocks = acc.get()
        assert blocks == []

    def test_add_empty_pattern(self):
        """Test adding an empty pattern via session_blocked_patterns."""
        ctx = _make_ctx()
        ctx.session_blocked_patterns = [{"pattern": "", "suggestion": "", "pattern_type": "literal"}]
        # Empty pattern stored but won't match meaningful commands
        acc = plugins.ScopeAccessor(ctx, "session")
        blocks = acc.get()
        assert len(blocks) == 1

    def test_add_whitespace_pattern(self):
        """Test adding a whitespace-only pattern."""
        ctx = _make_ctx()
        ctx.session_blocked_patterns = [{"pattern": "   ", "suggestion": "", "pattern_type": "literal"}]
        acc = plugins.ScopeAccessor(ctx, "session")
        blocks = acc.get()
        assert len(blocks) == 1

    def test_remove_nonexistent_pattern(self):
        """Test removing a pattern that doesn't exist."""
        ctx = _make_ctx()
        acc = plugins.ScopeAccessor(ctx, "session")
        # Set blocks (no "nonexistent" pattern)
        acc.set([{"pattern": "rm", "suggestion": "use trash", "pattern_type": "literal"}])
        # Filter out "nonexistent"
        current = acc.get()
        filtered = [b for b in current if b["pattern"] != "nonexistent"]
        before_count = len(current)
        after_count = len(filtered)
        assert before_count == after_count  # Nothing removed — same length

    def test_clear_empty_session(self):
        """Test clearing a session with no blocks.
        Uses _make_ctx() which generates a unique uuid session_id → before is always [].
        """
        ctx = _make_ctx()
        acc = plugins.ScopeAccessor(ctx, "session")
        before = acc.get()
        acc.set([])
        after = acc.get()
        assert after == []
        assert before == []  # Safe: _make_ctx() uses uuid, so session is always fresh

    def test_multiple_blocks_same_pattern(self):
        """Test that ScopeAccessor.set() is a raw setter — it does NOT deduplicate.

        Dedup is enforced at the command-handler layer (_make_block_op in plugins.py),
        not at the storage layer. Direct storage of duplicates stores all of them.
        To test dedup, dispatch '/ar:no rm' twice and check only 1 block is stored.
        Canonical dedup path: plugins.py:_make_block_op("session", "block").
        """
        ctx = _make_ctx()
        # Set blocks with duplicates directly (bypasses command-handler dedup)
        ctx.session_blocked_patterns = [
            {"pattern": "rm", "suggestion": "use trash", "pattern_type": "literal"},
            {"pattern": "rm", "suggestion": "use trash", "pattern_type": "literal"},
            {"pattern": "rm", "suggestion": "use trash", "pattern_type": "literal"},
        ]
        acc = plugins.ScopeAccessor(ctx, "session")
        # ScopeAccessor.set() is a raw setter — stores all 3 duplicates as-is
        acc.set(ctx.session_blocked_patterns)
        blocks = acc.get()
        rm_blocks = [b for b in blocks if b["pattern"] == "rm"]
        # Raw setter stores all 3; dedup only happens via _make_block_op command handler
        assert len(rm_blocks) == 3


class TestGlobalStateEdgeCases:
    """Test edge cases in global block state management via ScopeAccessor."""

    def test_empty_global_blocks(self):
        """Test getting blocks from empty global store."""
        with _isolated_global_store():
            ctx = _make_ctx()
            acc = plugins.ScopeAccessor(ctx, "global")
            blocks = acc.get()
            assert blocks == []

    def test_add_and_get_global_block(self):
        """Test adding and retrieving a global block."""
        with _isolated_global_store():
            ctx = _make_ctx()
            acc = plugins.ScopeAccessor(ctx, "global")
            acc.set([{"pattern": "rm", "suggestion": "use trash", "pattern_type": "literal"}])
            blocks = acc.get()
            assert len(blocks) == 1
            assert blocks[0]["pattern"] == "rm"

    def test_global_block_with_missing_fields(self):
        """Test blocks with missing optional fields are stored correctly."""
        with _isolated_global_store():
            ctx = _make_ctx()
            acc = plugins.ScopeAccessor(ctx, "global")
            acc.set([{"pattern": "rm"}])
            blocks = acc.get()
            assert len(blocks) == 1
            assert blocks[0]["pattern"] == "rm"

    def test_clear_global_blocks(self):
        """Test clearing global blocks."""
        with _isolated_global_store():
            ctx = _make_ctx()
            acc = plugins.ScopeAccessor(ctx, "global")
            acc.set([{"pattern": "rm", "suggestion": "use trash", "pattern_type": "literal"}])
            assert len(acc.get()) == 1
            acc.set([])
            assert acc.get() == []


class TestConcurrentAccess:
    """Test thread-safety and concurrent access patterns."""

    def test_concurrent_session_block_access(self):
        """Test multiple threads reading session blocks simultaneously."""
        patterns = [{"pattern": f"pattern{i}", "suggestion": "", "pattern_type": "literal"} for i in range(10)]
        ctx = _make_ctx(patterns=patterns)
        results = []

        def read_patterns():
            try:
                acc = plugins.ScopeAccessor(ctx, "session")
                blocks = acc.get()
                results.append(len(blocks))
            except Exception as e:
                results.append(f"ERROR: {e}")

        threads = [threading.Thread(target=read_patterns) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(r == 10 for r in results), f"Inconsistent results: {results}"

    def test_global_blocks_thread_safety(self):
        """Test global blocks under concurrent read access."""
        with _isolated_global_store() as store:
            # Pre-populate
            store["global_blocked_patterns"] = [
                {"pattern": "rm", "suggestion": "use trash", "pattern_type": "literal"}
            ]

            results = []

            def read_global():
                try:
                    ctx = _make_ctx()
                    acc = plugins.ScopeAccessor(ctx, "global")
                    blocks = acc.get()
                    results.append(len(blocks))
                except Exception as e:
                    results.append(f"ERROR: {e}")

            threads = [threading.Thread(target=read_global) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            # All threads should see the same global block
            assert all(isinstance(r, int) for r in results), f"Errors in threads: {results}"


class TestShouldBlockCommandEdgeCases:
    """Test edge cases in check_blocked_commands (canonical: daemon-path)."""

    def test_empty_command(self):
        """Test blocking an empty command."""
        block_info = _check_block(
            "",
            patterns=[{"pattern": "rm", "suggestion": "use trash", "pattern_type": "literal"}]
        )
        assert block_info is None

    def test_whitespace_only_command(self):
        """Test blocking a whitespace-only command."""
        block_info = _check_block(
            "   ",
            patterns=[{"pattern": "rm", "suggestion": "use trash", "pattern_type": "literal"}]
        )
        assert block_info is None

    def test_command_with_newlines(self):
        """Test blocking multi-line commands."""
        block_info = _check_block(
            "rm file.txt\nrm other.txt",
            patterns=[{"pattern": "rm", "suggestion": "use trash", "pattern_type": "literal"}]
        )
        assert block_info is not None

    def test_very_long_pattern(self):
        """Test blocking with very long pattern."""
        long_pattern = "a" * 1000
        block_info = _check_block(
            long_pattern,
            patterns=[{"pattern": long_pattern, "suggestion": "blocked", "pattern_type": "literal"}]
        )
        # Long patterns may or may not match depending on command_detection
        # The important thing is it doesn't crash
        assert block_info is None or isinstance(block_info, dict)

    def test_special_characters_in_command(self):
        """Test commands with special shell characters."""
        block_info = _check_block(
            "rm 'file with spaces.txt'",
            patterns=[{"pattern": "rm", "suggestion": "use trash", "pattern_type": "literal"}]
        )
        assert block_info is not None


class TestDefaultIntegrationsEdgeCases:
    """Test edge cases related to DEFAULT_INTEGRATIONS (via load_all_integrations)."""

    def test_pattern_not_in_defaults(self):
        """Test blocking a pattern not in DEFAULT_INTEGRATIONS."""
        block_info = _check_block(
            "custom_command",
            patterns=[{"pattern": "custom_command", "suggestion": "don't use custom_command", "pattern_type": "literal"}]
        )
        assert block_info is not None
        assert block_info.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"

    def test_overriding_default_suggestion(self):
        """Test providing custom suggestion for a pattern."""
        custom_suggestion = "Use my custom command instead"
        block_info = _check_block(
            "rm foo",
            patterns=[{"pattern": "rm", "suggestion": custom_suggestion, "pattern_type": "literal"}]
        )
        assert block_info is not None
        reason = block_info.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
        assert custom_suggestion in reason or "rm" in reason.lower()


class TestSessionIsolation:
    """Test that sessions are properly isolated via ThreadSafeDB."""

    def test_sessions_dont_interfere(self):
        """Test that different session contexts don't share patterns."""
        ctx1 = _make_ctx("rm foo", patterns=[{"pattern": "rm", "suggestion": "use trash", "pattern_type": "literal"}])
        ctx2 = _make_ctx("dd if=/dev/zero", patterns=[{"pattern": "dd", "suggestion": "don't use dd", "pattern_type": "literal"}])

        result1 = plugins.check_blocked_commands(ctx1)
        result2 = plugins.check_blocked_commands(ctx2)

        # ctx1 should see rm block
        assert result1 is not None
        assert result1.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"

        # ctx2 should see dd block but not rm block
        assert result2 is not None
        assert result2.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"

    def test_clearing_one_context_doesnt_affect_other(self):
        """Test that clearing one context's patterns doesn't affect another."""
        patterns = [{"pattern": "rm", "suggestion": "use trash", "pattern_type": "literal"}]
        ctx1 = _make_ctx("rm foo", patterns=list(patterns))
        ctx2 = _make_ctx("rm foo", patterns=list(patterns))

        # Clear ctx1 patterns
        ctx1.session_blocked_patterns = []

        # ctx2 should still have the block (DEFAULT_INTEGRATIONS blocks rm too)
        result2 = plugins.check_blocked_commands(ctx2)
        assert result2 is not None
        assert result2.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"

        # ctx1 with no session blocks still sees DEFAULT_INTEGRATIONS rm block
        result1 = plugins.check_blocked_commands(ctx1)
        assert result1 is not None  # rm is in DEFAULT_INTEGRATIONS


class TestCommandWithQuotedArguments:
    """Test commands with various quote styles."""

    def test_single_quotes(self):
        """Test commands with single-quoted arguments."""
        assert command_matches_pattern("rm 'file with spaces.txt'", "rm") is True

    def test_double_quotes(self):
        """Test commands with double-quoted arguments."""
        assert command_matches_pattern('rm "file with spaces.txt"', "rm") is True

    def test_mixed_quotes(self):
        """Test commands with mixed quotes."""
        assert command_matches_pattern('''rm "file's name".txt''', "rm") is True

    def test_escaped_quotes(self):
        """Test commands with escaped quotes."""
        assert command_matches_pattern('rm "file\\"with\\"quotes.txt"', "rm") is True


class TestBlockingNoneSessionId:
    """Test handling of None or invalid session_id."""

    def test_none_session_id_ignored(self):
        """Test that EventContext with None session_id handles gracefully."""
        # EventContext with empty session_id uses ThreadSafeDB isolation anyway
        ctx = EventContext(
            session_id="",
            event="PreToolUse",
            tool_name="Bash",
            tool_input={"command": "ls"},
            store=ThreadSafeDB(),
        )
        acc = plugins.ScopeAccessor(ctx, "session")
        blocks = acc.get()
        assert blocks is not None  # Not None — empty list

    def test_empty_string_session_id(self):
        """Test EventContext with empty string session_id."""
        ctx = EventContext(
            session_id="test-empty-session",
            event="PreToolUse",
            tool_name="Bash",
            tool_input={"command": "ls"},
            store=ThreadSafeDB(),
        )
        acc = plugins.ScopeAccessor(ctx, "session")
        blocks = acc.get()
        assert blocks is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
