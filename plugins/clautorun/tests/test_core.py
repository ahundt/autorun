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
TDD tests for clautorun v0.7 core.py components.

Tests for:
- LazyTranscript: Deferred string conversion
- ThreadSafeDB: In-memory cache with shelve persistence
- EventContext: Magic __getattr__/__setattr__ state access
- ClautorunApp: Decorator-based command registration
- ClautorunDaemon: AsyncIO Unix socket server lifecycle
"""

import pytest
import json
import threading
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

# Import the core module components
from clautorun.core import (
    LazyTranscript,
    ThreadSafeDB,
    EventContext,
    ClautorunApp,
    ClautorunDaemon,
    resolve_session_key,
    app,
    logger
)


# ============================================================================
# P1.1: LazyTranscript Tests
# ============================================================================

class TestLazyTranscript:
    """Tests for LazyTranscript lazy string conversion."""

    def test_text_property_converts_on_first_access(self):
        """Text property should convert transcript to JSON string only on first access."""
        raw = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
        transcript = LazyTranscript(raw)

        # Should not be converted yet
        assert transcript._converted is False

        # Access text property
        text = transcript.text

        # Should now be converted
        assert transcript._converted is True
        assert "hello" in text
        assert "hi" in text

    def test_text_property_caches_result(self):
        """Text property should cache the converted string."""
        raw = [{"role": "user", "content": "test"}]
        transcript = LazyTranscript(raw)

        text1 = transcript.text
        text2 = transcript.text

        # Should be the same object (cached)
        assert text1 is text2

    def test_text_property_empty_transcript(self):
        """Empty transcript should return empty string."""
        transcript = LazyTranscript([])
        assert transcript.text == ""

    def test_text_property_none_transcript(self):
        """None transcript should return empty string."""
        transcript = LazyTranscript(None)
        assert transcript.text == ""

    def test_contains_case_insensitive(self):
        """Contains should be case-insensitive."""
        raw = [{"content": "AUTOFILE_JUSTIFICATION"}]
        transcript = LazyTranscript(raw)

        assert transcript.contains("autofile_justification") is True
        assert transcript.contains("AUTOFILE_JUSTIFICATION") is True
        assert transcript.contains("Autofile_Justification") is True

    def test_contains_not_found(self):
        """Contains should return False for non-existent pattern."""
        raw = [{"content": "hello world"}]
        transcript = LazyTranscript(raw)

        assert transcript.contains("nonexistent") is False

    def test_search_regex_finds_pattern(self):
        """search_regex should find pattern with regex."""
        raw = [{"content": "<AUTOFILE_JUSTIFICATION>valid reason</AUTOFILE_JUSTIFICATION>"}]
        transcript = LazyTranscript(raw)

        match = transcript.search_regex(r'<AUTOFILE_JUSTIFICATION>(.*?)</AUTOFILE_JUSTIFICATION>')
        assert match is not None
        assert match.group(1) == "valid reason"

    def test_search_regex_cached(self):
        """search_regex should cache results."""
        raw = [{"content": "test pattern"}]
        transcript = LazyTranscript(raw)

        # Call twice with same pattern
        result1 = transcript.search_regex(r'test')
        result2 = transcript.search_regex(r'test')

        # Results should be cached (same object)
        assert result1 is result2

    def test_has_justification_valid(self):
        """has_justification should return True for valid justification."""
        raw = [{"content": "<AUTOFILE_JUSTIFICATION>Need new config file for auth module</AUTOFILE_JUSTIFICATION>"}]
        transcript = LazyTranscript(raw)

        assert transcript.has_justification() is True

    def test_has_justification_empty(self):
        """has_justification should return False for empty content."""
        raw = [{"content": "<AUTOFILE_JUSTIFICATION></AUTOFILE_JUSTIFICATION>"}]
        transcript = LazyTranscript(raw)

        assert transcript.has_justification() is False

    def test_has_justification_placeholder(self):
        """has_justification should return False for placeholder 'reason'."""
        raw = [{"content": "<AUTOFILE_JUSTIFICATION>reason</AUTOFILE_JUSTIFICATION>"}]
        transcript = LazyTranscript(raw)

        assert transcript.has_justification() is False

    def test_has_justification_missing(self):
        """has_justification should return False when tag is missing."""
        raw = [{"content": "no justification here"}]
        transcript = LazyTranscript(raw)

        assert transcript.has_justification() is False


# ============================================================================
# P1.2: ThreadSafeDB Tests
# ============================================================================

class TestThreadSafeDB:
    """Tests for ThreadSafeDB in-memory cache layer."""

    def test_get_default_value(self):
        """get should return default when key not found."""
        db = ThreadSafeDB()
        result = db.get("nonexistent:key", default="default_value")
        assert result == "default_value"

    def test_set_and_get_simple_value(self):
        """set should store value and get should retrieve it."""
        db = ThreadSafeDB()
        db.set("test_session:file_policy", "SEARCH")
        result = db.get("test_session:file_policy")
        assert result == "SEARCH"

    def test_set_deep_copies_list(self):
        """set should deep copy lists to prevent mutation."""
        db = ThreadSafeDB()
        original_list = [{"pattern": "rm", "type": "literal"}]
        db.set("test:blocks", original_list)

        # Modify original
        original_list.append({"pattern": "dd", "type": "literal"})

        # Cached value should not be modified
        cached = db.get("test:blocks")
        assert len(cached) == 1
        assert cached[0]["pattern"] == "rm"

    def test_set_deep_copies_dict(self):
        """set should deep copy dicts to prevent mutation."""
        db = ThreadSafeDB()
        original_dict = {"key": "value", "nested": {"a": 1}}
        db.set("test:dict", original_dict)

        # Modify original
        original_dict["key"] = "modified"
        original_dict["nested"]["a"] = 999

        # Cached value should not be modified
        cached = db.get("test:dict")
        assert cached["key"] == "value"
        assert cached["nested"]["a"] == 1

    def test_rsplit_handles_session_id_with_colon(self):
        """Key parsing should handle session_ids containing colons."""
        db = ThreadSafeDB()
        # Session ID like "history:abc123.jsonl" contains a colon
        key = "history:abc123.jsonl:file_policy"

        # This should parse as:
        # session_id = "history:abc123.jsonl"
        # field = "file_policy"
        parts = key.rsplit(":", 1)
        assert parts[0] == "history:abc123.jsonl"
        assert parts[1] == "file_policy"

    def test_thread_safety(self):
        """DB should be thread-safe for concurrent access."""
        db = ThreadSafeDB()
        results = []
        errors = []

        def writer(i):
            try:
                db.set(f"test:{i}", f"value_{i}")
            except Exception as e:
                errors.append(e)

        def reader(i):
            try:
                results.append(db.get(f"test:{i}"))
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(10):
            t = threading.Thread(target=writer, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # All writes should succeed
        assert len(errors) == 0

        threads = []
        for i in range(10):
            t = threading.Thread(target=reader, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # All reads should succeed
        assert len(errors) == 0
        assert len(results) == 10


# ============================================================================
# P1.3: EventContext Tests
# ============================================================================

class TestEventContext:
    """Tests for EventContext magic state access."""

    def test_read_only_properties(self):
        """Read-only properties should return payload values."""
        ctx = EventContext(
            session_id="test-session",
            event="UserPromptSubmit",
            prompt="/cr:st",
            tool_name="Write",
            tool_input={"file_path": "/tmp/test.txt"},
            tool_result="success",
            session_transcript=[{"role": "user", "content": "test"}]
        )

        assert ctx.session_id == "test-session"
        assert ctx.event == "UserPromptSubmit"
        assert ctx.prompt == "/cr:st"
        assert ctx.tool_name == "Write"
        assert ctx.tool_input == {"file_path": "/tmp/test.txt"}
        assert ctx.tool_result == "success"

    def test_magic_getattr_returns_default(self):
        """Magic getattr should return default when attribute not set."""
        ctx = EventContext(session_id="test", event="test")
        assert ctx.file_policy == "ALLOW"  # Default from _DEFAULTS
        assert ctx.autorun_active is False  # Default from _DEFAULTS
        assert ctx.autorun_stage == 0  # Default from _DEFAULTS

    def test_magic_setattr_persists_locally(self):
        """Magic setattr should persist value in local state."""
        ctx = EventContext(session_id="test", event="test")
        ctx.file_policy = "SEARCH"
        assert ctx.file_policy == "SEARCH"

    def test_magic_setattr_deep_copies_list(self):
        """Magic setattr should deep copy lists."""
        ctx = EventContext(session_id="test", event="test")
        original = [{"pattern": "rm"}]
        ctx.session_blocked_patterns = original

        # Modify original
        original.append({"pattern": "dd"})

        # Stored value should not change
        assert len(ctx.session_blocked_patterns) == 1

    def test_magic_setattr_with_store(self):
        """Magic setattr should persist to store when provided."""
        store = ThreadSafeDB()
        ctx = EventContext(session_id="test-session", event="test", store=store)

        ctx.file_policy = "JUSTIFY"

        # Should be in store
        assert store.get("test-session:file_policy") == "JUSTIFY"

    def test_transcript_property_lazy(self):
        """transcript property should return LazyTranscript."""
        ctx = EventContext(
            session_id="test", event="test",
            session_transcript=[{"content": "AUTOFILE_JUSTIFICATION"}]
        )

        transcript = ctx.transcript
        assert isinstance(transcript, LazyTranscript)
        assert transcript.contains("AUTOFILE")

    def test_has_justification_property(self):
        """has_justification should delegate to transcript."""
        ctx = EventContext(
            session_id="test", event="test",
            session_transcript=[{"content": "<AUTOFILE_JUSTIFICATION>valid</AUTOFILE_JUSTIFICATION>"}]
        )

        assert ctx.has_justification is True

    def test_file_exists_property_true(self):
        """file_exists should return True for existing file."""
        ctx = EventContext(
            session_id="test", event="test",
            tool_input={"file_path": "/tmp"}  # /tmp should exist
        )

        assert ctx.file_exists is True

    def test_file_exists_property_false(self):
        """file_exists should return False for non-existing file."""
        ctx = EventContext(
            session_id="test", event="test",
            tool_input={"file_path": "/nonexistent/path/to/file.txt"}
        )

        assert ctx.file_exists is False

    def test_file_exists_property_empty_path(self):
        """file_exists should return False for empty path."""
        ctx = EventContext(
            session_id="test", event="test",
            tool_input={}
        )

        assert ctx.file_exists is False

    def test_respond_allow(self):
        """respond with allow should return correct format."""
        ctx = EventContext(session_id="test", event="UserPromptSubmit")
        response = ctx.respond("allow", "Success message")

        assert response["continue"] is True
        assert response["stopReason"] == ""
        assert response["suppressOutput"] is False
        assert response["systemMessage"] == "Success message"

    def test_respond_deny(self):
        """respond with deny should return correct format."""
        ctx = EventContext(session_id="test", event="UserPromptSubmit")
        response = ctx.respond("deny", "Blocked")

        assert response["continue"] is False
        assert response["stopReason"] == "Blocked"

    def test_respond_pretooluse(self):
        """respond for PreToolUse should include hookSpecificOutput."""
        ctx = EventContext(session_id="test", event="PreToolUse")
        response = ctx.respond("deny", "Policy blocked")

        # Critical: continue=false blocks tool execution for deny decisions
        assert response["continue"] is False, "PreToolUse deny must set continue=false to block tool"
        assert response["stopReason"] == "Policy blocked"
        assert "hookSpecificOutput" in response
        assert response["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
        assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert response["hookSpecificOutput"]["permissionDecisionReason"] == "Policy blocked"

    def test_respond_pretooluse_allow(self):
        """respond for PreToolUse allow should have continue=true."""
        ctx = EventContext(session_id="test", event="PreToolUse")
        response = ctx.respond("allow", "Allowed")

        assert response["continue"] is True, "PreToolUse allow must set continue=true"
        assert response["decision"] == "allow"
        assert response["hookSpecificOutput"]["permissionDecision"] == "allow"

    def test_respond_block(self):
        """respond with block should return injection format."""
        ctx = EventContext(session_id="test", event="Stop")
        response = ctx.respond("block", "Continue working...")

        assert response["continue"] is False
        assert response["decision"] == "block"
        assert response["reason"] == "Continue working..."

    def test_allow_convenience(self):
        """allow() convenience method should work."""
        ctx = EventContext(session_id="test", event="test")
        response = ctx.allow("OK")
        assert response["continue"] is True

    def test_deny_convenience(self):
        """deny() convenience method should work."""
        ctx = EventContext(session_id="test", event="test")
        response = ctx.deny("Blocked")
        assert response["continue"] is False

    def test_block_convenience(self):
        """block() convenience method should work."""
        ctx = EventContext(session_id="test", event="Stop")
        response = ctx.block("Injection")
        assert response["decision"] == "block"

    def test_command_response(self):
        """command_response should return correct format for local commands."""
        ctx = EventContext(session_id="test", event="UserPromptSubmit")
        response = ctx.command_response("test message")

        # Commands handled locally should NOT continue to AI
        assert response["continue"] is False
        assert response["systemMessage"] == "test message"
        assert response["response"] == "test message"  # Backward compat
        assert response["stopReason"] == ""
        assert response["suppressOutput"] is False

    def test_stage_constants(self):
        """Stage constants should have correct values."""
        assert EventContext.STAGE_INACTIVE == 0
        assert EventContext.STAGE_1 == 1
        assert EventContext.STAGE_2 == 2
        assert EventContext.STAGE_2_COMPLETED == 3
        assert EventContext.STAGE_3 == 4


# ============================================================================
# P1.4: ClautorunApp Tests
# ============================================================================

class TestClautorunApp:
    """Tests for ClautorunApp decorator-based registration."""

    def test_command_decorator_registers_handler(self):
        """@app.command should register handler with aliases."""
        test_app = ClautorunApp()

        @test_app.command("/cr:test", "/test", "TEST")
        def test_handler(ctx):
            return "test result"

        assert "/cr:test" in test_app.command_handlers
        assert "/test" in test_app.command_handlers
        assert "TEST" in test_app.command_handlers

    def test_on_decorator_registers_chain_handler(self):
        """@app.on should register handler in chain."""
        test_app = ClautorunApp()

        @test_app.on("PreToolUse")
        def test_pretooluse(ctx):
            return None

        assert test_pretooluse in test_app.chains["PreToolUse"]

    def test_on_subagent_stop_shares_stop_chain(self):
        """SubagentStop handlers should be added to Stop chain."""
        test_app = ClautorunApp()

        @test_app.on("SubagentStop")
        def test_subagent(ctx):
            return None

        assert test_subagent in test_app.chains["Stop"]

    def test_run_chain_returns_first_non_none(self):
        """_run_chain should return first non-None result."""
        test_app = ClautorunApp()

        @test_app.on("PreToolUse")
        def handler1(ctx):
            return None  # Continue to next

        @test_app.on("PreToolUse")
        def handler2(ctx):
            return {"result": "from handler2"}

        @test_app.on("PreToolUse")
        def handler3(ctx):
            return {"result": "from handler3"}  # Should not be called

        ctx = EventContext(session_id="test", event="PreToolUse")
        result = test_app._run_chain(ctx, "PreToolUse")

        assert result == {"result": "from handler2"}

    def test_run_chain_returns_none_if_all_none(self):
        """_run_chain should return None if all handlers return None."""
        test_app = ClautorunApp()

        @test_app.on("PreToolUse")
        def handler1(ctx):
            return None

        @test_app.on("PreToolUse")
        def handler2(ctx):
            return None

        ctx = EventContext(session_id="test", event="PreToolUse")
        result = test_app._run_chain(ctx, "PreToolUse")

        assert result is None

    def test_find_command_from_config_mapping(self):
        """_find_command should find commands via CONFIG mappings."""
        test_app = ClautorunApp()

        @test_app.command("ALLOW")
        def handle_allow(ctx):
            return "allowed"

        # /cr:a maps to "ALLOW" in CONFIG
        result = test_app._find_command("/cr:a")

        assert result is not None
        handler, alias = result
        assert alias == "ALLOW"

    def test_find_command_direct_alias(self):
        """_find_command should find commands by direct alias."""
        test_app = ClautorunApp()

        @test_app.command("/custom:cmd")
        def custom_handler(ctx):
            return "custom"

        result = test_app._find_command("/custom:cmd")

        assert result is not None
        handler, alias = result
        assert alias == "/custom:cmd"

    def test_find_command_with_args(self):
        """_find_command should match commands with arguments."""
        test_app = ClautorunApp()

        @test_app.command("/cr:go")
        def handle_go(ctx):
            return "go"

        result = test_app._find_command("/cr:go build something")

        assert result is not None
        handler, alias = result
        assert alias == "/cr:go"

    def test_dispatch_user_prompt_submit_command(self):
        """dispatch should handle UserPromptSubmit with command."""
        test_app = ClautorunApp()

        @test_app.command("/test")
        def test_handler(ctx):
            return "test response"

        ctx = EventContext(session_id="test", event="UserPromptSubmit", prompt="/test")
        result = test_app.dispatch(ctx)

        # Commands handled locally should NOT continue to AI
        assert result["continue"] is False
        assert "test response" in result["systemMessage"]
        assert "test response" in result["response"]  # Backward compat

    def test_dispatch_user_prompt_submit_no_command(self):
        """dispatch should allow non-command prompts."""
        test_app = ClautorunApp()
        ctx = EventContext(session_id="test", event="UserPromptSubmit", prompt="regular prompt")
        result = test_app.dispatch(ctx)

        assert result["continue"] is True

    def test_dispatch_pretooluse_runs_chain(self):
        """dispatch should run PreToolUse chain."""
        test_app = ClautorunApp()

        @test_app.on("PreToolUse")
        def block_handler(ctx):
            if ctx.tool_name == "Write":
                return ctx.deny("Blocked by test")
            return None

        ctx = EventContext(session_id="test", event="PreToolUse", tool_name="Write")
        result = test_app.dispatch(ctx)

        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_dispatch_stop_runs_chain(self):
        """dispatch should run Stop chain."""
        test_app = ClautorunApp()

        @test_app.on("Stop")
        def injection_handler(ctx):
            return ctx.block("Continue working")

        ctx = EventContext(session_id="test", event="Stop")
        result = test_app.dispatch(ctx)

        assert result["decision"] == "block"

    def test_dispatch_default_allow(self):
        """dispatch should return allow for unhandled events."""
        test_app = ClautorunApp()
        ctx = EventContext(session_id="test", event="SomeOtherEvent")
        result = test_app.dispatch(ctx)

        assert result["continue"] is True


# ============================================================================
# P1.5: ClautorunDaemon Tests
# ============================================================================

class TestClautorunDaemon:
    """Tests for ClautorunDaemon lifecycle management."""

    def test_pid_exists_true(self):
        """_pid_exists should return True for running process."""
        import os
        daemon = ClautorunDaemon(ClautorunApp())
        # Current process should exist
        assert daemon._pid_exists(os.getpid()) is True

    def test_pid_exists_false(self):
        """_pid_exists should return False for non-existent process."""
        daemon = ClautorunDaemon(ClautorunApp())
        # PID 99999999 should not exist
        assert daemon._pid_exists(99999999) is False

    def test_active_pids_tracking(self):
        """Daemon should track active PIDs."""
        daemon = ClautorunDaemon(ClautorunApp())

        daemon.active_pids.add(12345)
        daemon.active_pids.add(12346)

        assert 12345 in daemon.active_pids
        assert 12346 in daemon.active_pids
        assert len(daemon.active_pids) == 2

    def test_store_initialization(self):
        """Daemon should initialize ThreadSafeDB store."""
        daemon = ClautorunDaemon(ClautorunApp())
        assert isinstance(daemon.store, ThreadSafeDB)

    def test_stop_sets_running_false(self):
        """stop() should set running to False."""
        daemon = ClautorunDaemon(ClautorunApp())
        daemon.running = True
        daemon.stop()
        assert daemon.running is False

    @pytest.mark.asyncio
    async def test_watchdog_cleans_dead_pids(self):
        """watchdog should clean dead PIDs from active_pids."""
        daemon = ClautorunDaemon(ClautorunApp())
        daemon.running = True

        # Add a dead PID (99999999)
        daemon.active_pids.add(99999999)
        daemon.last_activity = asyncio.get_event_loop().time()

        # Manually call the dead PID cleanup logic
        dead = {pid for pid in daemon.active_pids if not daemon._pid_exists(pid)}
        daemon.active_pids -= dead

        assert 99999999 not in daemon.active_pids

    def test_socket_connect_test_no_socket(self):
        """_socket_connect_test should return True if no socket."""
        daemon = ClautorunDaemon(ClautorunApp())
        # With no socket file, should return True (can proceed)
        with patch.object(Path, 'exists', return_value=False):
            from clautorun.core import SOCKET_PATH
            original_exists = SOCKET_PATH.exists

            # Create temp path that doesn't exist
            import tempfile
            with tempfile.TemporaryDirectory() as tmpdir:
                fake_socket = Path(tmpdir) / "nonexistent.sock"
                with patch('clautorun.core.SOCKET_PATH', fake_socket):
                    result = daemon._socket_connect_test()
                    assert result is True

    def test_shutdown_event_initialized_to_none(self):
        """Daemon should initialize with shutdown_event as None."""
        daemon = ClautorunDaemon(ClautorunApp())
        assert daemon._shutdown_event is None

    def test_watchdog_task_initialized_to_none(self):
        """Daemon should initialize with watchdog_task as None."""
        daemon = ClautorunDaemon(ClautorunApp())
        assert daemon._watchdog_task is None

    def test_loop_initialized_to_none(self):
        """Daemon should initialize with loop as None."""
        daemon = ClautorunDaemon(ClautorunApp())
        assert daemon._loop is None

    def test_cleanup_registered_starts_false(self):
        """Daemon should start with cleanup not registered."""
        daemon = ClautorunDaemon(ClautorunApp())
        assert daemon._cleanup_registered is False

    def test_cleanup_files_handles_missing_socket(self):
        """_cleanup_files should handle missing socket gracefully."""
        daemon = ClautorunDaemon(ClautorunApp())
        # Should not raise even if socket doesn't exist
        daemon._cleanup_files()

    def test_cleanup_files_handles_missing_lock(self):
        """_cleanup_files should handle missing lock gracefully."""
        daemon = ClautorunDaemon(ClautorunApp())
        daemon._lock_fd = None
        # Should not raise
        daemon._cleanup_files()

    def test_stop_signals_shutdown_event(self):
        """stop() should set shutdown event if it exists."""
        daemon = ClautorunDaemon(ClautorunApp())
        daemon._shutdown_event = asyncio.Event()
        daemon.running = True

        daemon.stop()

        assert daemon._shutdown_event.is_set()
        assert daemon.running is False

    @pytest.mark.asyncio
    async def test_async_stop_sets_running_false(self):
        """async_stop should set running to False."""
        daemon = ClautorunDaemon(ClautorunApp())
        daemon.running = True
        daemon._shutdown_event = asyncio.Event()

        await daemon.async_stop()

        assert daemon.running is False

    @pytest.mark.asyncio
    async def test_async_stop_sets_shutdown_event(self):
        """async_stop should set shutdown event."""
        daemon = ClautorunDaemon(ClautorunApp())
        daemon.running = True
        daemon._shutdown_event = asyncio.Event()

        await daemon.async_stop()

        assert daemon._shutdown_event.is_set()

    @pytest.mark.asyncio
    async def test_async_stop_cancels_watchdog(self):
        """async_stop should cancel watchdog task."""
        daemon = ClautorunDaemon(ClautorunApp())
        daemon.running = True
        daemon._shutdown_event = asyncio.Event()

        # Create a mock watchdog task
        async def mock_watchdog():
            await asyncio.sleep(100)

        daemon._watchdog_task = asyncio.create_task(mock_watchdog())

        await daemon.async_stop()

        assert daemon._watchdog_task.cancelled() or daemon._watchdog_task.done()

    @pytest.mark.asyncio
    async def test_async_stop_idempotent(self):
        """async_stop should be safe to call multiple times."""
        daemon = ClautorunDaemon(ClautorunApp())
        daemon.running = True
        daemon._shutdown_event = asyncio.Event()

        # Call async_stop twice
        await daemon.async_stop()
        await daemon.async_stop()  # Should not raise

        assert daemon.running is False


# ============================================================================
# Session Identity Resolution Tests
# ============================================================================

class TestResolveSessionKey:
    """Tests for tri-layer session identity resolution."""

    def test_explicit_env_var(self):
        """Should use CLAUTORUN_SESSION_ID env var when set."""
        with patch.dict('os.environ', {'CLAUTORUN_SESSION_ID': 'explicit-id'}):
            result = resolve_session_key(12345, "/tmp", "fallback")
            assert result == "explicit:explicit-id"

    def test_fallback_to_session_id(self):
        """Should fall back to session_id when no env var."""
        with patch.dict('os.environ', {}, clear=True):
            result = resolve_session_key(12345, "/tmp", "fallback-session")
            assert result == "fallback-session"

    def test_identity_layer_disabled_by_default(self):
        """JSONL scanning should be disabled without CLAUTORUN_USE_IDENTITY."""
        with patch.dict('os.environ', {}, clear=True):
            result = resolve_session_key(12345, "/tmp", "fallback")
            # Should go directly to fallback without trying JSONL scan
            assert result == "fallback"


# ============================================================================
# Global App Instance Tests
# ============================================================================

class TestGlobalApp:
    """Tests for global app instance."""

    def test_global_app_exists(self):
        """Global app instance should exist."""
        assert app is not None
        assert isinstance(app, ClautorunApp)

    def test_global_app_has_chains(self):
        """Global app should have chain structures."""
        assert "PreToolUse" in app.chains
        assert "Stop" in app.chains
        assert "SessionStart" in app.chains
