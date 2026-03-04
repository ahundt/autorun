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
- Command handlers (/ar:no, /ar:ok, /ar:clear, /ar:blocks, /ar:globalstatus, etc.)
- PreToolUse hook blocking
- UserPromptSubmit command handling

Migrated from main.py (deleted) to daemon-path:
- handle_block_pattern   → plugins.app.dispatch(ctx) with /ar:no prompt
- handle_allow_pattern   → plugins.app.dispatch(ctx) with /ar:ok prompt
- handle_clear_pattern   → plugins.app.dispatch(ctx) with /ar:clear prompt
- handle_block_status    → plugins.app.dispatch(ctx) with /ar:blocks prompt
- handle_global_*        → plugins.app.dispatch(ctx) with /ar:global* prompt
- COMMAND_HANDLERS       → plugins.app.command_handlers (registered /ar:* commands)
- GLOBAL_CONFIG_FILE     → session_state("__global__") via ScopeAccessor
Canonical: EventContext + plugins.app.dispatch(ctx) + _isolated_global_store()
"""

import contextlib
import pytest
import uuid
import json
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

from autorun.config import DEFAULT_INTEGRATIONS
from autorun.core import EventContext, ThreadSafeDB
from autorun.session_manager import session_state
from autorun import plugins


@contextlib.contextmanager
def _isolated_global_store():
    """Patch session_state for global scope so tests don't touch real ~/.autorun/sessions/__global__.*

    Canonical replacement for patching GLOBAL_CONFIG_FILE (deleted).
    """
    store = {}

    @contextlib.contextmanager
    def mock_session_state(session_id, **kwargs):
        yield store

    with patch("autorun.plugins.session_state", mock_session_state):
        yield store


def _dispatch_cmd(prompt: str, session_id: str, store=None) -> dict | str | None:
    """Dispatch a UserPromptSubmit command via daemon-path plugins.app.dispatch().

    Canonical replacement for deleted handle_block_pattern/handle_allow_pattern/etc.
    Pass store=self._store (a ThreadSafeDB instance) so blocks write through to
    session_state JSON and are visible to _get_session_blocks() / _check_block_real().
    Without store=, EventContext writes to _state only (ephemeral) and _get_session_blocks()
    reading from session_state JSON will see nothing.
    """
    ctx = EventContext(
        session_id=session_id,
        event="UserPromptSubmit",
        prompt=prompt,
        tool_name="",
        tool_input={},
        store=store,  # pass through so blocks write to session_state JSON
    )
    return plugins.app.dispatch(ctx)


def _check_block_real(session_id: str, cmd: str, store=None) -> dict | None:
    """Returns deny result if command is blocked (using real session_state), else None.

    Canonical replacement for deleted should_block_command().
    Pass store=self._store (same ThreadSafeDB used in _dispatch_cmd) so the check sees
    session_allowed_patterns and session_blocked_patterns written by _dispatch_cmd.
    Without store=, EventContext reads from _state (empty) and misses session allows/blocks
    stored in ThreadSafeDB (though DEFAULT_INTEGRATIONS blocks are still seen).
    """
    if not cmd or not cmd.strip():
        return None
    ctx = EventContext(
        session_id=session_id,
        event="PreToolUse",
        tool_name="Bash",
        tool_input={"command": cmd},
        store=store,  # pass through to read session allows/blocks from ThreadSafeDB
    )
    result = plugins.check_blocked_commands(ctx)
    if result is None:
        return None
    perm = result.get("hookSpecificOutput", {}).get("permissionDecision", "allow")
    return result if perm == "deny" else None


def _get_session_blocks(session_id: str) -> list:
    """Get session blocks from real session_state."""
    with session_state(session_id) as state:
        return list(state.get("session_blocked_patterns", []))


def _clear_session(session_id: str):
    """Clear session blocks and allows from real session_state."""
    with session_state(session_id) as state:
        state["session_blocked_patterns"] = []
        state["session_allowed_patterns"] = []


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


class TestCommandBlockingHandlers:
    """Test command blocking handler functions via daemon-path dispatch."""

    def setup_method(self):
        """Set up unique session ID per test."""
        self.test_session_id = f"test-handler-{uuid.uuid4().hex[:8]}"
        self._store = ThreadSafeDB()  # shared store mirrors daemon behavior; writes through to session_state JSON
        _clear_session(self.test_session_id)

    def teardown_method(self):
        """Clean up session blocks."""
        _clear_session(self.test_session_id)

    def test_handle_block_pattern_adds_block(self):
        """Test that /ar:no adds a session block."""
        response = _dispatch_cmd(f"/ar:no rm", self.test_session_id, store=self._store)

        assert response is not None
        response_str = str(response)
        assert "rm" in response_str

        # Verify block was persisted (ThreadSafeDB writes through to session_state JSON)
        blocks = _get_session_blocks(self.test_session_id)
        assert any(b["pattern"] == "rm" for b in blocks), f"'rm' not in blocks: {blocks}"

    def test_handle_block_pattern_with_custom_pattern(self):
        """Test blocking with custom patterns.

        Note: parse_pattern_and_description uses shlex.split, so multi-word
        patterns must be quoted. Without quotes, "dd if=" is parsed as
        pattern="dd" with description="if=".
        """
        response = _dispatch_cmd(f'/ar:no "dd if="', self.test_session_id, store=self._store)

        assert response is not None
        response_str = str(response)
        assert "dd if=" in response_str

    def test_handle_block_pattern_duplicate(self):
        """Test handling duplicate block attempts."""
        # First block
        _dispatch_cmd(f"/ar:no rm", self.test_session_id, store=self._store)
        # Second attempt
        response = _dispatch_cmd(f"/ar:no rm", self.test_session_id, store=self._store)

        # Either "Blocked" or already in list — either way rm is in blocks
        blocks = _get_session_blocks(self.test_session_id)
        assert any(b["pattern"] == "rm" for b in blocks)

    def test_handle_block_pattern_missing_args(self):
        """Test handling missing pattern argument."""
        response = _dispatch_cmd(f"/ar:no", self.test_session_id, store=self._store)

        assert response is not None
        response_str = str(response)
        assert "Usage:" in response_str or "usage" in response_str.lower()

    def test_handle_allow_pattern(self):
        """Test allowing a blocked pattern via /ar:ok."""
        # First block it
        _dispatch_cmd(f"/ar:no rm", self.test_session_id, store=self._store)

        # Then allow it
        response = _dispatch_cmd(f"/ar:ok rm", self.test_session_id, store=self._store)

        assert response is not None
        response_str = str(response)
        assert "rm" in response_str

    def test_handle_allow_pattern_not_blocked(self):
        """Test allowing a pattern that wasn't blocked — /ar:ok adds to allows list."""
        response = _dispatch_cmd(f"/ar:ok rm", self.test_session_id, store=self._store)

        assert response is not None
        # /ar:ok adds to allowed patterns regardless of whether rm was blocked
        response_str = str(response)
        assert "rm" in response_str

    def test_handle_clear_pattern_all(self):
        """Test clearing all session blocks via /ar:clear."""
        # Add multiple blocks
        _dispatch_cmd(f"/ar:no rm", self.test_session_id, store=self._store)
        _dispatch_cmd(f"/ar:no dd", self.test_session_id, store=self._store)

        # Clear all
        response = _dispatch_cmd(f"/ar:clear", self.test_session_id, store=self._store)

        assert response is not None
        response_str = str(response)
        assert "clear" in response_str.lower() or "cleared" in response_str.lower()

    def test_handle_block_status(self):
        """Test status display via /ar:blocks."""
        # Add a block
        _dispatch_cmd(f"/ar:no rm", self.test_session_id, store=self._store)

        # Get status
        response = _dispatch_cmd(f"/ar:blocks", self.test_session_id, store=self._store)

        assert response is not None
        response_str = str(response)
        assert "rm" in response_str


class TestGlobalCommandHandlers:
    """Test global command blocking handlers via daemon-path dispatch + isolated store."""

    def test_handle_global_block_pattern(self):
        """Test adding a global block via /ar:globalno."""
        with _isolated_global_store():
            response = _dispatch_cmd(f"/ar:globalno rm", "test-global-block")

            assert response is not None
            response_str = str(response)
            assert "rm" in response_str

    def test_handle_global_allow_pattern(self):
        """Test removing a global block via /ar:globalok."""
        with _isolated_global_store():
            # First add it
            _dispatch_cmd(f"/ar:globalno rm", "test-global-allow")

            # Then allow it
            response = _dispatch_cmd(f"/ar:globalok rm", "test-global-allow")

            assert response is not None
            response_str = str(response)
            assert "rm" in response_str

    def test_handle_global_block_status(self):
        """Test global status display via /ar:globalstatus."""
        with _isolated_global_store():
            # Add a global block
            _dispatch_cmd(f"/ar:globalno rm", "test-global-status")

            # Get status
            response = _dispatch_cmd(f"/ar:globalstatus", "test-global-status")

            assert response is not None
            response_str = str(response)
            assert "rm" in response_str


class TestPreToolUseBlocking:
    """Test PreToolUse hook blocking integration via EventContext."""

    def setup_method(self):
        """Set up test session."""
        self.test_session_id = f"test-pretooluse-{uuid.uuid4().hex[:8]}"

    def _ctx_with_blocks(self, cmd: str, patterns: list = None) -> EventContext:
        """Create isolated EventContext with optional pre-seeded session_blocked_patterns."""
        ctx = EventContext(
            session_id=self.test_session_id,
            event="PreToolUse",
            tool_name="Bash",
            tool_input={"command": cmd},
            store=ThreadSafeDB(),
        )
        if patterns is not None:
            ctx.session_blocked_patterns = patterns
        return ctx

    def test_bash_command_blocked(self):
        """Test that Bash commands can be blocked via session_blocked_patterns."""
        ctx = self._ctx_with_blocks(
            "rm file.txt",
            patterns=[{"pattern": "rm", "suggestion": "use trash", "pattern_type": "literal"}],
        )
        response = _pretooluse(ctx)

        assert "hookSpecificOutput" in response
        assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason_lower = response["hookSpecificOutput"]["permissionDecisionReason"].lower()
        assert "rm" in reason_lower or "blocked" in reason_lower or "trash" in reason_lower

    def test_bash_command_allowed(self):
        """Test that unblocked Bash commands are allowed."""
        ctx = self._ctx_with_blocks("ls file.txt")
        response = _pretooluse(ctx)

        if response.get("hookSpecificOutput", {}).get("permissionDecision") == "deny":
            assert "Command blocked" not in response["hookSpecificOutput"].get("permissionDecisionReason", "")

    def test_non_bash_tools_not_affected(self):
        """Test that non-Bash tools are not affected by command blocking."""
        ctx = EventContext(
            session_id=self.test_session_id,
            event="PreToolUse",
            tool_name="Write",
            tool_input={"file_path": "rm_test.txt", "content": "test"},
            store=ThreadSafeDB(),
        )
        ctx.session_blocked_patterns = [{"pattern": "rm", "suggestion": "use trash", "pattern_type": "literal"}]

        response = _pretooluse(ctx)

        reason = response.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
        assert "Command blocked" not in reason

    def test_pattern_matching_in_pretooluse(self):
        """Test pattern matching in PreToolUse blocking."""
        ctx = self._ctx_with_blocks(
            "dd if=/dev/zero of=test",
            patterns=[{"pattern": "dd if=", "suggestion": "use /dev/null", "pattern_type": "literal"}],
        )
        response = _pretooluse(ctx)

        assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "dd if=" in response["hookSpecificOutput"]["permissionDecisionReason"]


class TestCommandHandlerIntegration:
    """Test that command handlers are registered in plugins.app.command_handlers."""

    def test_blocking_handlers_registered(self):
        """Test that all blocking handlers are registered as app commands."""
        # Canonical: plugins.app.command_handlers dict (not COMMAND_HANDLERS)
        registered_commands = set(plugins.app.command_handlers.keys())
        required = ["/ar:no", "/ar:ok", "/ar:clear", "/ar:blocks",
                    "/ar:globalno", "/ar:globalok", "/ar:globalclear", "/ar:globalstatus"]
        for cmd in required:
            assert cmd in registered_commands, f"Missing command: {cmd}"

    def test_handlers_are_callable(self):
        """Test that all registered blocking handlers are callable."""
        for cmd in ["/ar:no", "/ar:ok", "/ar:clear", "/ar:blocks",
                    "/ar:globalno", "/ar:globalok", "/ar:globalclear", "/ar:globalstatus"]:
            handler = plugins.app.command_handlers.get(cmd)
            assert handler is not None and callable(handler), f"Handler for {cmd} not callable"


class TestEndToEndWorkflows:
    """Test complete end-to-end workflows using real session_state."""

    def setup_method(self):
        """Set up unique session ID."""
        self.test_session_id = f"test-e2e-{uuid.uuid4().hex[:8]}"
        self._store = ThreadSafeDB()  # shared store mirrors daemon behavior; writes through to session_state JSON
        _clear_session(self.test_session_id)

    def teardown_method(self):
        """Clean up."""
        _clear_session(self.test_session_id)

    def test_block_then_check_workflow(self):
        """Test the complete block and check workflow.

        Note: /ar:no rm adds to session_blocked_patterns.
              /ar:ok rm adds to session_allowed_patterns (TIER 1 allows).
        After /ar:ok, rm is in allows → check_blocked_commands allows rm
        (allows short-circuit all blocks including DEFAULT_INTEGRATIONS).
        """
        # Block rm (store=self._store writes through to session_state JSON)
        response = _dispatch_cmd(f"/ar:no rm", self.test_session_id, store=self._store)
        assert response is not None

        # Verify it's blocked (store=self._store reads session block + DEFAULT_INTEGRATIONS)
        block_info = _check_block_real(self.test_session_id, "rm file.txt", store=self._store)
        assert block_info is not None

        # Unblock rm (adds to session_allowed_patterns → TIER 1 allow)
        response = _dispatch_cmd(f"/ar:ok rm", self.test_session_id, store=self._store)
        assert response is not None

        # After /ar:ok, session_allowed_patterns has rm → short-circuits all blocks
        # This means even DEFAULT_INTEGRATIONS rm is bypassed (allows beat blocks)
        # Must use store=self._store so check sees the allow written by /ar:ok dispatch
        block_info = _check_block_real(self.test_session_id, "rm file.txt", store=self._store)
        # rm is now explicitly allowed by session_allowed_patterns TIER 1
        assert block_info is None, "rm should be allowed after /ar:ok rm"

    def test_global_block_workflow(self):
        """Test global block with isolated store."""
        with _isolated_global_store():
            # Set global block
            response = _dispatch_cmd(f"/ar:globalno rm", self.test_session_id, store=self._store)
            assert response is not None
            assert "rm" in str(response)

            # Verify blocked globally
            block_info = _check_block_real(self.test_session_id, "rm file.txt")
            assert block_info is not None

            # Allow globally
            response = _dispatch_cmd(f"/ar:globalok rm", self.test_session_id, store=self._store)
            assert response is not None

    def test_clear_workflow(self):
        """Test clearing blocks workflow."""
        # Add multiple blocks
        _dispatch_cmd(f"/ar:no rm", self.test_session_id, store=self._store)
        _dispatch_cmd(f"/ar:no dd", self.test_session_id, store=self._store)

        # Clear all
        response = _dispatch_cmd(f"/ar:clear", self.test_session_id, store=self._store)

        assert response is not None
        response_str = str(response)
        assert "clear" in response_str.lower() or "cleared" in response_str.lower()

        # Verify all cleared
        blocks = _get_session_blocks(self.test_session_id)
        assert len(blocks) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
