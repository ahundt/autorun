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
TDD tests for autorun v0.7 core.py components.

Tests for:
- LazyTranscript: Deferred string conversion
- ThreadSafeDB: In-memory cache with shelve persistence
- EventContext: Magic __getattr__/__setattr__ state access
- AutorunApp: Decorator-based command registration
- AutorunDaemon: AsyncIO Unix socket server lifecycle
"""

import pytest
import json
import threading
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

# Import the core module components
from autorun.core import (
    LazyTranscript,
    ThreadSafeDB,
    EventContext,
    AutorunApp,
    AutorunDaemon,
    resolve_session_key,
    get_cli_event_name,
    format_suggestion,
    get_tool_names,
    CLI_TOOL_NAMES,
    INTERNAL_TO_CLAUDE,
    INTERNAL_TO_GEMINI,
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
            prompt="/ar:st",
            tool_name="Write",
            tool_input={"file_path": "/tmp/test.txt"},
            tool_result="success",
            session_transcript=[{"role": "user", "content": "test"}]
        )

        assert ctx.session_id == "test-session"
        assert ctx.event == "UserPromptSubmit"
        assert ctx.prompt == "/ar:st"
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

        # autorun mandate: Always keep AI working unless it's a local command
        assert response["continue"] is True
        assert response["decision"] == "approve"

    def test_respond_pretooluse(self):
        """respond for PreToolUse should include hookSpecificOutput."""
        ctx = EventContext(session_id="test", event="PreToolUse")
        response = ctx.respond("deny", "Policy blocked")

        # Critical: autorun uses permissionDecision: deny to block tool,
        # but continue: true to let AI suggest alternatives.
        assert response["continue"] is True, "PreToolUse should keep AI working"
        assert "hookSpecificOutput" in response
        assert response["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
        assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert response["hookSpecificOutput"]["permissionDecisionReason"] == "Policy blocked"

    def test_respond_pretooluse_allow(self):
        """respond for PreToolUse allow should have continue=true."""
        ctx = EventContext(session_id="test", event="PreToolUse")
        response = ctx.respond("allow", "Allowed")

        assert response["continue"] is True, "PreToolUse allow must set continue=true"
        # respond() maps 'allow' to 'approve' for top-level Claude decision
        assert response["decision"] == "approve"
        assert response["hookSpecificOutput"]["permissionDecision"] == "allow"

    def test_respond_block(self):
        """respond with block should return injection format."""
        ctx = EventContext(session_id="test", event="Stop")
        response = ctx.respond("block", "Continue working...")

        # Stop injection MUST use continue: True to trigger retry
        assert response["continue"] is True
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
        # Convenience deny should follow the same keep-working pattern
        assert response["continue"] is True

    def test_block_convenience(self):
        """block() convenience method should work."""
        ctx = EventContext(session_id="test", event="Stop")
        response = ctx.block("Injection")
        assert response["decision"] == "block"

    def test_command_response_continues_by_default(self):
        """command_response must return continue=True so AI processes output."""
        ctx = EventContext(session_id="test", event="UserPromptSubmit")
        response = ctx.command_response("Policy: allow-all")
        assert response["continue"] is True, "AI loop must continue after command"
        assert response["systemMessage"] == "Policy: allow-all"
        assert response["stopReason"] == ""
        assert response["suppressOutput"] is False

    def test_command_response_can_halt(self):
        """estop/stop commands can opt into continue=False."""
        ctx = EventContext(session_id="test", event="UserPromptSubmit")
        response = ctx.command_response("Emergency stop!", continue_loop=False)
        assert response["continue"] is False
        assert response["systemMessage"] == "Emergency stop!"

    def test_command_response_no_response_key(self):
        """command_response must not include non-spec 'response' key."""
        ctx = EventContext(session_id="test", event="UserPromptSubmit")
        response = ctx.command_response("test")
        assert "response" not in response, "'response' is not in hook spec"

    def test_stage_constants(self):
        """Stage constants should have correct values."""
        assert EventContext.STAGE_INACTIVE == 0
        assert EventContext.STAGE_1 == 1
        assert EventContext.STAGE_2 == 2
        assert EventContext.STAGE_2_COMPLETED == 3
        assert EventContext.STAGE_3 == 4

    # ------------------------------------------------------------------
    # cwd propagation tests (regression: plan export cwd not available)
    # Root cause: client.py:197 injects _cwd but core.py:handle_client()
    # did not pass it to EventContext constructor, so ctx.cwd was always None.
    # Fix: added _cwd slot + cwd param + cwd property + wiring in handle_client.
    # Debug evidence: "record_write: cwd not available" repeated 40+ times.
    # ------------------------------------------------------------------

    def test_cwd_is_none_by_default(self):
        """EventContext.cwd returns None when not provided (backward compatible)."""
        ctx = EventContext(session_id="test", event="PostToolUse")
        assert ctx.cwd is None

    def test_cwd_set_from_constructor(self):
        """EventContext.cwd returns value passed to constructor."""
        ctx = EventContext(session_id="test", event="PostToolUse", cwd="/Users/test/myproject")
        assert ctx.cwd == "/Users/test/myproject"

    def test_cwd_empty_string(self):
        """EventContext.cwd can be empty string (no project dir available)."""
        ctx = EventContext(session_id="test", event="PostToolUse", cwd="")
        assert ctx.cwd == ""

    def test_cwd_not_in_magic_state(self):
        """EventContext.cwd must not be persisted to magic state (Shelve).

        cwd is a transient per-request value (set by client.py from os.getcwd())
        and must not leak into the cross-session shelve DB.
        """
        ctx = EventContext(session_id="test", event="PostToolUse", cwd="/some/path")
        assert "cwd" not in ctx._DEFAULTS
        assert "_cwd" in ctx.__slots__


# ============================================================================
# to_human parameter tests — all 5 pathways
# ============================================================================

class TestRespondToHuman:
    """Tests for to_human parameter across all pathways in EventContext.respond().

    Pathway summary:
      PATHWAY 1 (PreToolUse):      to_human silently ignored — hookSpecificOutput always kept
      PATHWAY 2 (PostToolUse, UPS): to_human supported — False→AI injection, True/str→human-visible
      PATHWAY 3 (Stop):            to_human silently ignored — systemMessage already human-visible
      PATHWAY 4 (SessionStart):    to_human silently ignored — systemMessage always set, no AI-only path
    """

    # =========================================================================
    # PATHWAY 2: PostToolUse — fully supported
    # =========================================================================

    def test_posttooluse_default_sends_to_both_channels(self):
        """Default (to_human=True, to_ai=True): both systemMessage and additionalContext set."""
        ctx = EventContext(session_id="test", event="PostToolUse")
        response = ctx.respond("allow", "📋 Plan exported to notes/test.md")
        assert response["systemMessage"] == "📋 Plan exported to notes/test.md"
        assert "hookSpecificOutput" in response
        assert response["hookSpecificOutput"]["additionalContext"] == "📋 Plan exported to notes/test.md"
        assert response.get("reason", "") == ""  # empty prevents double-print

    def test_posttooluse_to_human_true_with_default_to_ai_sends_both(self):
        """Explicit to_human=True with default to_ai=True: same as default — both channels."""
        ctx = EventContext(session_id="test", event="PostToolUse")
        response = ctx.respond("allow", "msg", to_human=True)
        assert response["systemMessage"] == "msg"
        assert "hookSpecificOutput" in response
        assert response["hookSpecificOutput"]["additionalContext"] == "msg"

    def test_posttooluse_custom_strings_per_channel(self):
        """Different custom strings for human and AI channels."""
        ctx = EventContext(session_id="test", event="PostToolUse")
        response = ctx.respond("allow", "reason", to_human="📋 Saved", to_ai="Plan at notes/x.md")
        assert response["systemMessage"] == "📋 Saved"
        assert response["hookSpecificOutput"]["additionalContext"] == "Plan at notes/x.md"

    def test_posttooluse_to_ai_false_human_only(self):
        """to_ai=False: human-only (replaces old to_human=True behavior)."""
        ctx = EventContext(session_id="test", event="PostToolUse")
        response = ctx.respond("allow", "msg", to_ai=False)
        assert response["systemMessage"] == "msg"
        assert "hookSpecificOutput" not in response

    def test_posttooluse_to_human_true_shows_reason_to_user(self):
        """to_human=True, to_ai=False: hookSpecificOutput absent, reason empty → no double-print."""
        ctx = EventContext(session_id="test", event="PostToolUse")
        response = ctx.respond("allow", "Plan exported to notes/plan.md", to_human=True, to_ai=False)
        assert response["systemMessage"] == "Plan exported to notes/plan.md"
        assert "hookSpecificOutput" not in response  # absent → human-only when to_ai=False
        # reason must be empty — canonical example (claude-code-hooks-api.md:202-210) omits it
        assert response.get("reason", "") == ""

    def test_posttooluse_to_human_custom_string(self):
        """to_human='custom', to_ai=False: human sees custom string; reason empty to prevent double-print."""
        ctx = EventContext(session_id="test", event="PostToolUse")
        response = ctx.respond("allow", "verbose AI context", to_human="📋 Plan saved", to_ai=False)
        assert response["systemMessage"] == "📋 Plan saved"
        assert "hookSpecificOutput" not in response
        assert response.get("reason", "") == ""

    def test_posttooluse_to_human_empty_string_treated_as_false(self):
        """to_human='': empty string is falsy → _resolve_channel returns None."""
        ctx = EventContext(session_id="test", event="PostToolUse")
        response = ctx.respond("allow", "context", to_human="")
        # to_ai=True (default) → hookSpecificOutput present; sys_msg falls back to ai_text
        assert "hookSpecificOutput" in response

    def test_posttooluse_to_human_truthy_int_treated_as_false(self):
        """to_human=1: truthy int but not True by identity → _resolve_channel returns None."""
        ctx = EventContext(session_id="test", event="PostToolUse")
        response = ctx.respond("allow", "context", to_human=1)
        # to_ai=True (default) → hookSpecificOutput present
        assert "hookSpecificOutput" in response

    def test_posttooluse_to_human_true_with_empty_reason(self):
        """to_human=True + empty reason + to_ai=False: human path taken but systemMessage is empty.
        Documented trap — callers must pass non-empty reason when to_human=True."""
        ctx = EventContext(session_id="test", event="PostToolUse")
        response = ctx.respond("allow", "", to_human=True, to_ai=False)
        assert "hookSpecificOutput" not in response  # human path taken, AI explicitly off
        assert response["systemMessage"] == ""       # empty — no visible output

    def test_posttooluse_ai_injection_has_reason_not_empty(self):
        """AI injection path (to_human=False): reason field carries context to AI."""
        ctx = EventContext(session_id="test", event="PostToolUse")
        response = ctx.respond("allow", "AI context here", to_human=False)
        assert "hookSpecificOutput" in response
        assert response["hookSpecificOutput"]["additionalContext"] == "AI context here"
        assert response["reason"] == "AI context here"  # human_text=None → reason=msg_reason (backwards compat)
        assert response["systemMessage"] == "AI context here"  # sys_msg falls back to ai_text

    def test_posttooluse_to_human_gemini_no_hso(self):
        """Gemini AfterTool + to_human=True + to_ai=False: hookSpecificOutput absent, systemMessage set.
        decision: 'approve' — consistent with existing PATHWAY 2 AI path (pre-existing pattern).
        reason: '' — no double-print."""
        ctx = EventContext(session_id="test", event="PostToolUse", cli_type="gemini")
        response = ctx.respond("allow", "Plan exported", to_human=True, to_ai=False)
        assert response["systemMessage"] == "Plan exported"
        assert "hookSpecificOutput" not in response
        assert response["decision"] == "approve"  # PATHWAY 2 uses "approve" for both CLIs
        assert response.get("reason", "") == ""

    # =========================================================================
    # PATHWAY 1: PreToolUse — to_human silently ignored (security)
    # =========================================================================

    def test_pretooluse_to_human_true_deny_hso_always_kept(self):
        """PreToolUse deny + to_human=True: hookSpecificOutput MUST be kept.
        Prevents fail-open (tool executes if hookSpecificOutput absent).
        systemMessage deliberately empty for deny on Claude Code (anti-triple-print, core.py:843)."""
        ctx = EventContext(session_id="test", event="PreToolUse")
        response = ctx.respond("deny", "Blocked", to_human=True)
        assert "hookSpecificOutput" in response
        assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert response.get("systemMessage", "") == ""  # anti-triple-print preserved

    def test_pretooluse_to_human_true_allow_hso_kept(self):
        """PreToolUse allow + to_human=True: hookSpecificOutput still kept."""
        ctx = EventContext(session_id="test", event="PreToolUse")
        response = ctx.respond("allow", "Allowed", to_human=True)
        assert "hookSpecificOutput" in response
        assert response["hookSpecificOutput"]["permissionDecision"] == "allow"

    # =========================================================================
    # PATHWAY 3: Stop — to_human silently ignored
    # =========================================================================

    def test_stop_to_human_silently_ignored(self):
        """Stop + to_human=True: silently ignored; response identical to without it."""
        ctx = EventContext(session_id="test", event="Stop")
        response_with = ctx.respond("block", "Keep working", to_human=True)
        response_without = ctx.respond("block", "Keep working")
        assert response_with == response_without

    # =========================================================================
    # PATHWAY 4: SessionStart — to_human silently ignored
    # =========================================================================

    def test_sessionstart_to_human_silently_ignored(self):
        """SessionStart + to_human=True: silently ignored; systemMessage always set by PATHWAY 4."""
        ctx = EventContext(session_id="test", event="SessionStart")
        response_with = ctx.respond("allow", "Session started", to_human=True)
        response_without = ctx.respond("allow", "Session started")
        assert response_with == response_without


# ============================================================================
# P1.3.1: CLI Event Name Mapping Tests
# ============================================================================

class TestCLIEventNameMapping:
    """Test dynamic event name mapping for Gemini/Claude Code compatibility.

    Regression test for Bug: Hardcoded "PreToolUse" sent to Gemini CLI
    causing "Invalid hook event name" warning.

    The daemon normalizes incoming event names to internal format (PreToolUse),
    processes them, then denormalizes to CLI-specific format in responses:
    - Gemini expects: BeforeTool, AfterTool, BeforeAgent, AfterAgent
    - Claude expects: PreToolUse, PostToolUse, UserPromptSubmit, Stop
    """

    def test_gemini_event_name_mapping(self):
        """Test internal events map to Gemini CLI event names."""
        assert get_cli_event_name("PreToolUse", "gemini") == "BeforeTool"
        assert get_cli_event_name("PostToolUse", "gemini") == "AfterTool"
        assert get_cli_event_name("UserPromptSubmit", "gemini") == "BeforeAgent"
        assert get_cli_event_name("Stop", "gemini") == "AfterAgent"
        assert get_cli_event_name("SessionStart", "gemini") == "SessionStart"
        assert get_cli_event_name("SessionEnd", "gemini") == "SessionEnd"

    def test_claude_event_name_mapping(self):
        """Test internal events map to Claude Code event names (identity)."""
        assert get_cli_event_name("PreToolUse", "claude") == "PreToolUse"
        assert get_cli_event_name("PostToolUse", "claude") == "PostToolUse"
        assert get_cli_event_name("UserPromptSubmit", "claude") == "UserPromptSubmit"
        assert get_cli_event_name("Stop", "claude") == "Stop"
        assert get_cli_event_name("SessionStart", "claude") == "SessionStart"
        assert get_cli_event_name("SessionEnd", "claude") == "SessionEnd"

    def test_unknown_event_passthrough(self):
        """Test unknown events pass through unchanged."""
        assert get_cli_event_name("UnknownEvent", "gemini") == "UnknownEvent"
        assert get_cli_event_name("UnknownEvent", "claude") == "UnknownEvent"

    def test_gemini_pretooluse_regression(self):
        """Regression test: Gemini MUST receive 'BeforeTool', not 'PreToolUse'.

        Bug: Daemon was sending hardcoded "PreToolUse" to Gemini CLI,
        causing warning: "Invalid hook event name: PreToolUse"

        Fix: get_cli_event_name("PreToolUse", "gemini") → "BeforeTool"
        """
        # This is the critical test that detects the original bug
        event_name = get_cli_event_name("PreToolUse", "gemini")
        assert event_name == "BeforeTool", \
            f"Gemini CLI expects 'BeforeTool' but got '{event_name}'"
        assert event_name != "PreToolUse", \
            "Bug detected: Sending Claude event name 'PreToolUse' to Gemini CLI"

    def test_claude_pretooluse_identity(self):
        """Test Claude Code receives 'PreToolUse' unchanged."""
        event_name = get_cli_event_name("PreToolUse", "claude")
        assert event_name == "PreToolUse"

    def test_mapping_constants_complete(self):
        """Test mapping constants contain all expected events."""
        # Verify Gemini mapping
        assert "PreToolUse" in INTERNAL_TO_GEMINI
        assert "PostToolUse" in INTERNAL_TO_GEMINI
        assert "UserPromptSubmit" in INTERNAL_TO_GEMINI
        assert "Stop" in INTERNAL_TO_GEMINI

        # Verify Claude mapping (should be identity mapping)
        assert "PreToolUse" in INTERNAL_TO_CLAUDE
        assert "PostToolUse" in INTERNAL_TO_CLAUDE
        assert "UserPromptSubmit" in INTERNAL_TO_CLAUDE
        assert "Stop" in INTERNAL_TO_CLAUDE

    def test_eventcontext_respond_uses_dynamic_event_names(self):
        """Test EventContext.respond() uses get_cli_event_name() for hookSpecificOutput."""
        # Create mock context for Gemini
        ctx = EventContext(
            session_id="test",
            event="PreToolUse",
            prompt="test",
            tool_name="write_file",
            tool_input={},
            cli_type="gemini"
        )

        # Test respond with allow
        response = ctx.respond(decision="allow", reason="test")

        # Verify hookSpecificOutput uses Gemini event name
        assert "hookSpecificOutput" in response
        assert response["hookSpecificOutput"]["hookEventName"] == "BeforeTool", \
            "EventContext.respond() must use get_cli_event_name() for Gemini"

    def test_eventcontext_respond_claude_uses_pretooluse(self):
        """Test EventContext.respond() uses 'PreToolUse' for Claude Code."""
        # Create mock context for Claude
        ctx = EventContext(
            session_id="test",
            event="PreToolUse",
            prompt="test",
            tool_name="Write",
            tool_input={},
            cli_type="claude"
        )

        # Test respond
        response = ctx.respond(decision="allow", reason="test")

        # Verify hookSpecificOutput uses Claude event name
        assert "hookSpecificOutput" in response
        assert response["hookSpecificOutput"]["hookEventName"] == "PreToolUse"


# ============================================================================
# P1.4: AutorunApp Tests
# ============================================================================

class TestAutorunApp:
    """Tests for AutorunApp decorator-based registration."""

    def test_command_decorator_registers_handler(self):
        """@app.command should register handler with aliases."""
        test_app = AutorunApp()

        @test_app.command("/ar:test", "/test", "TEST")
        def test_handler(ctx):
            return "test result"

        assert "/ar:test" in test_app.command_handlers
        assert "/test" in test_app.command_handlers
        assert "TEST" in test_app.command_handlers

    def test_on_decorator_registers_chain_handler(self):
        """@app.on should register handler in chain."""
        test_app = AutorunApp()

        @test_app.on("PreToolUse")
        def test_pretooluse(ctx):
            return None

        assert test_pretooluse in test_app.chains["PreToolUse"]

    def test_on_subagent_stop_shares_stop_chain(self):
        """SubagentStop handlers should be added to Stop chain."""
        test_app = AutorunApp()

        @test_app.on("SubagentStop")
        def test_subagent(ctx):
            return None

        assert test_subagent in test_app.chains["Stop"]

    def test_run_chain_returns_first_non_none(self):
        """_run_chain should return first non-None result."""
        test_app = AutorunApp()

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
        test_app = AutorunApp()

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
        test_app = AutorunApp()

        @test_app.command("ALLOW")
        def handle_allow(ctx):
            return "allowed"

        # /ar:a maps to "ALLOW" in CONFIG
        result = test_app._find_command("/ar:a")

        assert result is not None
        handler, alias = result
        assert alias == "ALLOW"

    def test_find_command_direct_alias(self):
        """_find_command should find commands by direct alias."""
        test_app = AutorunApp()

        @test_app.command("/custom:cmd")
        def custom_handler(ctx):
            return "custom"

        result = test_app._find_command("/custom:cmd")

        assert result is not None
        handler, alias = result
        assert alias == "/custom:cmd"

    def test_find_command_with_args(self):
        """_find_command should match commands with arguments."""
        test_app = AutorunApp()

        @test_app.command("/ar:go")
        def handle_go(ctx):
            return "go"

        result = test_app._find_command("/ar:go build something")

        assert result is not None
        handler, alias = result
        assert alias == "/ar:go"

    def test_dispatch_user_prompt_submit_command(self):
        """dispatch should handle UserPromptSubmit with command."""
        test_app = AutorunApp()

        @test_app.command("/test")
        def test_handler(ctx):
            return "test response"

        ctx = EventContext(session_id="test", event="UserPromptSubmit", prompt="/test")
        result = test_app.dispatch(ctx)

        # Commands handled locally: AI continues (sees systemMessage)
        assert result["continue"] is True
        assert "test response" in result["systemMessage"]

    def test_dispatch_user_prompt_submit_no_command(self):
        """dispatch should allow non-command prompts."""
        test_app = AutorunApp()
        ctx = EventContext(session_id="test", event="UserPromptSubmit", prompt="regular prompt")
        result = test_app.dispatch(ctx)

        assert result["continue"] is True

    def test_dispatch_pretooluse_runs_chain(self):
        """dispatch should run PreToolUse chain."""
        test_app = AutorunApp()

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
        test_app = AutorunApp()

        @test_app.on("Stop")
        def injection_handler(ctx):
            return ctx.block("Continue working")

        ctx = EventContext(session_id="test", event="Stop")
        result = test_app.dispatch(ctx)

        assert result["decision"] == "block"

    def test_dispatch_default_passthrough(self):
        """dispatch returns None for unhandled events (pass-through).

        None → daemon sends {} → client exits 0 with no stdout.
        Allows parallel hooks (RTK) to apply updatedInput without conflict.
        """
        test_app = AutorunApp()
        ctx = EventContext(session_id="test", event="SomeOtherEvent")
        result = test_app.dispatch(ctx)

        assert result is None, f"unhandled event must return None for pass-through, got {result!r}"


# ============================================================================
# P1.5: AutorunDaemon Tests
# ============================================================================

class TestAutorunDaemon:
    """Tests for AutorunDaemon lifecycle management."""

    def test_pid_exists_true(self):
        """_pid_exists should return True for running process."""
        import os
        daemon = AutorunDaemon(AutorunApp())
        # Current process should exist
        assert daemon._pid_exists(os.getpid()) is True

    def test_pid_exists_false(self):
        """_pid_exists should return False for non-existent process."""
        daemon = AutorunDaemon(AutorunApp())
        # PID 99999999 should not exist
        assert daemon._pid_exists(99999999) is False

    def test_active_pids_tracking(self):
        """Daemon should track active PIDs."""
        daemon = AutorunDaemon(AutorunApp())

        daemon.active_pids.add(12345)
        daemon.active_pids.add(12346)

        assert 12345 in daemon.active_pids
        assert 12346 in daemon.active_pids
        assert len(daemon.active_pids) == 2

    def test_store_initialization(self):
        """Daemon should initialize ThreadSafeDB store."""
        daemon = AutorunDaemon(AutorunApp())
        assert isinstance(daemon.store, ThreadSafeDB)

    def test_stop_sets_running_false(self):
        """stop() should set running to False."""
        daemon = AutorunDaemon(AutorunApp())
        daemon.running = True
        daemon.stop()
        assert daemon.running is False

    @pytest.mark.asyncio
    async def test_watchdog_cleans_dead_pids(self):
        """watchdog should clean dead PIDs from active_pids."""
        daemon = AutorunDaemon(AutorunApp())
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
        daemon = AutorunDaemon(AutorunApp())
        # With no socket file, should return True (can proceed)
        with patch.object(Path, 'exists', return_value=False):
            from autorun.core import SOCKET_PATH
            original_exists = SOCKET_PATH.exists

            # Create temp path that doesn't exist
            import tempfile
            with tempfile.TemporaryDirectory() as tmpdir:
                fake_socket = Path(tmpdir) / "nonexistent.sock"
                with patch('autorun.core.SOCKET_PATH', fake_socket):
                    result = daemon._socket_connect_test()
                    assert result is True

    def test_shutdown_event_initialized_to_none(self):
        """Daemon should initialize with shutdown_event as None."""
        daemon = AutorunDaemon(AutorunApp())
        assert daemon._shutdown_event is None

    def test_watchdog_task_initialized_to_none(self):
        """Daemon should initialize with watchdog_task as None."""
        daemon = AutorunDaemon(AutorunApp())
        assert daemon._watchdog_task is None

    def test_loop_initialized_to_none(self):
        """Daemon should initialize with loop as None."""
        daemon = AutorunDaemon(AutorunApp())
        assert daemon._loop is None

    def test_cleanup_registered_starts_false(self):
        """Daemon should start with cleanup not registered."""
        daemon = AutorunDaemon(AutorunApp())
        assert daemon._cleanup_registered is False

    def test_cleanup_files_handles_missing_socket(self):
        """_cleanup_files should handle missing socket gracefully."""
        daemon = AutorunDaemon(AutorunApp())
        # Should not raise even if socket doesn't exist
        daemon._cleanup_files()

    def test_cleanup_files_handles_missing_lock(self):
        """_cleanup_files should handle missing lock gracefully."""
        daemon = AutorunDaemon(AutorunApp())
        daemon._lock_fd = None
        # Should not raise
        daemon._cleanup_files()

    def test_stop_signals_shutdown_event(self):
        """stop() should set shutdown event if it exists."""
        daemon = AutorunDaemon(AutorunApp())
        daemon._shutdown_event = asyncio.Event()
        daemon.running = True

        daemon.stop()

        assert daemon._shutdown_event.is_set()
        assert daemon.running is False

    @pytest.mark.asyncio
    async def test_async_stop_sets_running_false(self):
        """async_stop should set running to False."""
        daemon = AutorunDaemon(AutorunApp())
        daemon.running = True
        daemon._shutdown_event = asyncio.Event()

        await daemon.async_stop()

        assert daemon.running is False

    @pytest.mark.asyncio
    async def test_async_stop_sets_shutdown_event(self):
        """async_stop should set shutdown event."""
        daemon = AutorunDaemon(AutorunApp())
        daemon.running = True
        daemon._shutdown_event = asyncio.Event()

        await daemon.async_stop()

        assert daemon._shutdown_event.is_set()

    @pytest.mark.asyncio
    async def test_async_stop_cancels_watchdog(self):
        """async_stop should cancel watchdog task."""
        daemon = AutorunDaemon(AutorunApp())
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
        daemon = AutorunDaemon(AutorunApp())
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
        """Should use AUTORUN_SESSION_ID env var when set."""
        with patch.dict('os.environ', {'AUTORUN_SESSION_ID': 'explicit-id'}):
            result = resolve_session_key(12345, "/tmp", "fallback")
            assert result == "explicit:explicit-id"

    def test_fallback_to_session_id(self):
        """Should fall back to session_id when no env var."""
        with patch.dict('os.environ', {}, clear=True):
            result = resolve_session_key(12345, "/tmp", "fallback-session")
            assert result == "fallback-session"

    def test_identity_layer_disabled_by_default(self):
        """JSONL scanning should be disabled without AUTORUN_USE_IDENTITY."""
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
        assert isinstance(app, AutorunApp)

    def test_global_app_has_chains(self):
        """Global app should have chain structures."""
        assert "PreToolUse" in app.chains
        assert "Stop" in app.chains
        assert "SessionStart" in app.chains


# ============================================================================
# P1.5: TestFormatSuggestion - Platform-aware tool name substitution
# ============================================================================

class TestFormatSuggestion:
    """TDD tests for format_suggestion() dispatch table.

    Parallel to TestCLIEventNameMapping: same pattern, tool names instead of events.
    The {grep}, {read}, etc. format variables allow suggestion strings to resolve
    to the correct tool name per CLI, without duplicating the strings.
    """

    # ─── Dispatch table contract ─────────────────────────────────────────────

    def test_cli_tool_names_has_claude_and_gemini(self):
        assert "claude" in CLI_TOOL_NAMES
        assert "gemini" in CLI_TOOL_NAMES

    def test_get_tool_names_claude_has_all_keys(self):
        tools = get_tool_names("claude")
        for key in ("grep", "glob", "read", "write", "edit", "bash", "ls"):
            assert key in tools, f"claude missing tool key: {key}"

    def test_get_tool_names_gemini_has_all_keys(self):
        tools = get_tool_names("gemini")
        for key in ("grep", "glob", "read", "write", "edit", "bash", "ls"):
            assert key in tools, f"gemini missing tool key: {key}"

    def test_get_tool_names_unknown_cli_returns_empty(self):
        """Unknown CLI → empty dict → all placeholders pass through."""
        assert get_tool_names("vscode") == {}

    # ─── format_suggestion() contract ────────────────────────────────────────

    def test_claude_grep(self):
        assert format_suggestion("Use the {grep} tool", "claude") == "Use the Grep tool"

    def test_gemini_grep(self):
        """Regression: Gemini MUST receive 'grep_search', not 'Grep'."""
        assert format_suggestion("Use the {grep} tool", "gemini") == "Use the grep_search tool"

    def test_claude_all_tools(self):
        msg = "{grep} {glob} {read} {write} {edit} {bash} {ls}"
        result = format_suggestion(msg, "claude")
        assert result == "Grep Glob Read Write Edit Bash LS"

    def test_gemini_all_tools(self):
        msg = "{grep} {glob} {read} {write} {edit} {bash} {ls}"
        result = format_suggestion(msg, "gemini")
        assert result == "grep_search glob read_file write_file replace run_shell_command list_directory"

    def test_unknown_cli_passthrough(self):
        """Unknown CLI leaves all placeholders unchanged — generic safe fallback."""
        result = format_suggestion("Use the {grep} tool", "vscode")
        assert result == "Use the {grep} tool"

    def test_non_tool_placeholder_preserved(self):
        """{args} in redirect strings must NOT be substituted."""
        assert format_suggestion("trash {args}", "gemini") == "trash {args}"
        assert format_suggestion("trash {args}", "claude") == "trash {args}"

    def test_mixed_tool_and_args_placeholder(self):
        """Both {grep} and {args} in same string — only tool name substitutes."""
        result = format_suggestion("Use {grep} tool on {args}", "gemini")
        assert result == "Use grep_search tool on {args}"

    # ─── Regression: actual DEFAULT_INTEGRATIONS strings ────────────────────

    def test_real_grep_suggestion_claude(self):
        from autorun.config import DEFAULT_INTEGRATIONS
        msg = DEFAULT_INTEGRATIONS["grep"]["suggestion"]
        result = format_suggestion(msg, "claude")
        assert "Grep" in result
        assert "grep_search" not in result

    def test_real_grep_suggestion_gemini(self):
        """Regression: grep suggestion must name grep_search for Gemini."""
        from autorun.config import DEFAULT_INTEGRATIONS
        msg = DEFAULT_INTEGRATIONS["grep"]["suggestion"]
        result = format_suggestion(msg, "gemini")
        assert "grep_search" in result
        assert "{grep}" not in result

    def test_real_find_suggestion_gemini(self):
        from autorun.config import DEFAULT_INTEGRATIONS
        msg = DEFAULT_INTEGRATIONS["find"]["suggestion"]
        result = format_suggestion(msg, "gemini")
        assert "glob" in result.lower()
        assert "{glob}" not in result

    def test_real_cat_suggestion_gemini(self):
        from autorun.config import DEFAULT_INTEGRATIONS
        msg = DEFAULT_INTEGRATIONS["cat"]["suggestion"]
        result = format_suggestion(msg, "gemini")
        assert "read_file" in result
        assert "{read}" not in result

    def test_policy_blocked_search_gemini(self):
        from autorun.config import CONFIG
        msg = CONFIG["policy_blocked"]["SEARCH"]
        result = format_suggestion(msg, "gemini")
        assert "grep_search" in result
        assert "glob" in result.lower()
        assert "{grep}" not in result

    # ─── Canary: API tool name stability ────────────────────────────────────
    # These tests document exact API tool names as of specific CLI versions.
    # A failure means the CLI renamed a tool at the API level.
    # → Update CLI_TOOL_NAMES and file an issue: https://github.com/ahundt/autorun/issues
    # → Note: terminal display names can differ from API names.
    #   e.g. Claude Code CLI v2.1.47 renders Glob→"Search" but API name is still "Glob".

    def test_canary_claude_api_names_v2_1_47(self):
        """Claude Code CLI v2.1.47: PascalCase API tool names.

        If this fails, Anthropic renamed a tool at the API level.
        Update CLI_TOOL_NAMES["claude"] and file an issue:
        https://github.com/ahundt/autorun/issues
        """
        claude = CLI_TOOL_NAMES["claude"]
        assert claude["grep"]  == "Grep",   "Grep renamed? Update CLI_TOOL_NAMES['claude']['grep']"
        assert claude["glob"]  == "Glob",   "Glob renamed? (terminal shows 'Search' but API is 'Glob' as of v2.1.47)"
        assert claude["read"]  == "Read",   "Read renamed? Update CLI_TOOL_NAMES['claude']['read']"
        assert claude["write"] == "Write",  "Write renamed? Update CLI_TOOL_NAMES['claude']['write']"
        assert claude["edit"]  == "Edit",   "Edit renamed? Update CLI_TOOL_NAMES['claude']['edit']"
        assert claude["bash"]  == "Bash",   "Bash renamed? Update CLI_TOOL_NAMES['claude']['bash']"
        assert claude["ls"]    == "LS",     "LS renamed? Update CLI_TOOL_NAMES['claude']['ls']"

    def test_canary_gemini_api_names(self):
        """Gemini CLI: snake_case API tool names, confirmed by hooks.json BeforeTool matcher:
        "write_file|run_shell_command|replace|read_file|glob|grep_search"

        If this fails, Gemini renamed a tool. Update CLI_TOOL_NAMES["gemini"] and file:
        https://github.com/ahundt/autorun/issues
        """
        gemini = CLI_TOOL_NAMES["gemini"]
        assert gemini["grep"]  == "grep_search",      "grep_search renamed? Update CLI_TOOL_NAMES['gemini']['grep']"
        assert gemini["glob"]  == "glob",             "glob renamed? Update CLI_TOOL_NAMES['gemini']['glob']"
        assert gemini["read"]  == "read_file",        "read_file renamed? Update CLI_TOOL_NAMES['gemini']['read']"
        assert gemini["write"] == "write_file",       "write_file renamed? Update CLI_TOOL_NAMES['gemini']['write']"
        assert gemini["edit"]  == "replace",          "replace renamed? Update CLI_TOOL_NAMES['gemini']['edit']"
        assert gemini["bash"]  == "run_shell_command","run_shell_command renamed? Update CLI_TOOL_NAMES['gemini']['bash']"
        assert gemini["ls"]    == "list_directory",   "list_directory renamed? Update CLI_TOOL_NAMES['gemini']['ls']"

    # ─── Symmetry: all CLIs must map the same template keys ─────────────────

    def test_all_clis_have_same_template_keys(self):
        """Every CLI in CLI_TOOL_NAMES must map exactly the same set of template keys.

        A failure means a new key was added to one CLI but not others.
        Add the missing key to all CLIs, then update suggestion strings.
        """
        key_sets = {cli: frozenset(tools.keys()) for cli, tools in CLI_TOOL_NAMES.items()}
        first_cli, first_keys = next(iter(key_sets.items()))
        for cli, keys in key_sets.items():
            assert keys == first_keys, (
                f"CLI_TOOL_NAMES[{cli!r}] keys {sorted(keys)} differ from "
                f"CLI_TOOL_NAMES[{first_cli!r}] keys {sorted(first_keys)}. "
                f"Add missing keys to all CLIs."
            )

    # ─── Coverage: no unreplaced placeholders in any suggestion string ────────
    # These tests scan ALL suggestion strings in DEFAULT_INTEGRATIONS and
    # CONFIG["policy_blocked"] after format_suggestion() for each known CLI.
    # A remaining {placeholder} means either:
    #   (a) A new {key} was added to a suggestion string without updating CLI_TOOL_NAMES, OR
    #   (b) A new CLI tool exists that nobody mapped yet.
    # Fix: add the missing key to CLI_TOOL_NAMES for all CLIs, or file an issue:
    # https://github.com/ahundt/autorun/issues

    def _collect_suggestion_strings(self):
        """Return all (label, msg) pairs from suggestion strings and policy_blocked."""
        import re
        from autorun.config import DEFAULT_INTEGRATIONS, CONFIG
        items = []
        for cmd, intg in DEFAULT_INTEGRATIONS.items():
            msg = intg.get("suggestion", "")
            if msg:
                items.append((f"DEFAULT_INTEGRATIONS[{cmd!r}]['suggestion']", msg))
        for key, msg in CONFIG["policy_blocked"].items():
            items.append((f"CONFIG['policy_blocked'][{key!r}]", msg))
        return items

    def test_no_unreplaced_placeholders_claude(self):
        """All {tool_key} placeholders in suggestion strings must resolve for Claude.

        If this fails, either:
        - A new {key} was added to a suggestion string → add to CLI_TOOL_NAMES["claude"]
        - A new Claude tool exists not yet in the table → add it and file an issue
        https://github.com/ahundt/autorun/issues
        """
        import re
        placeholder_re = re.compile(r'\{[a-z_]+\}')
        for label, msg in self._collect_suggestion_strings():
            result = format_suggestion(msg, "claude")
            remaining = placeholder_re.findall(result)
            assert not remaining, (
                f"{label} has unreplaced placeholders for 'claude': {remaining}\n"
                f"Add to CLI_TOOL_NAMES['claude'] or update the string.\n"
                f"File an issue: https://github.com/ahundt/autorun/issues"
            )

    def test_no_unreplaced_placeholders_gemini(self):
        """All {tool_key} placeholders in suggestion strings must resolve for Gemini.

        If this fails, either:
        - A new {key} was added to a suggestion string → add to CLI_TOOL_NAMES["gemini"]
        - A new Gemini tool exists not yet in the table → add it and file an issue
        https://github.com/ahundt/autorun/issues
        """
        import re
        placeholder_re = re.compile(r'\{[a-z_]+\}')
        for label, msg in self._collect_suggestion_strings():
            result = format_suggestion(msg, "gemini")
            remaining = placeholder_re.findall(result)
            assert not remaining, (
                f"{label} has unreplaced placeholders for 'gemini': {remaining}\n"
                f"Add to CLI_TOOL_NAMES['gemini'] or update the string.\n"
                f"File an issue: https://github.com/ahundt/autorun/issues"
            )

    def test_format_suggestion_handles_shell_braces(self):
        """format_suggestion must not raise ValueError on shell syntax like xargs -I{} mv {}.

        Regression test for the bug where format_map() was used instead of str.replace(),
        causing ValueError when suggestion strings contained shell brace syntax such as
        `xargs -I{} mv {}` (used in git clean suggestions). The fix in commit c0e4367
        replaced format_map with str.replace() so shell braces are preserved unchanged.
        """
        from autorun.config import DEFAULT_INTEGRATIONS
        from autorun.core import format_suggestion

        # Find any suggestion string that contains shell braces (non-template braces)
        # These are braces that DON'T match {tool_key} pattern (e.g., xargs -I{} mv {})
        shell_brace_suggestions = {
            k: v["suggestion"]
            for k, v in DEFAULT_INTEGRATIONS.items()
            if "{}" in v.get("suggestion", "")
        }

        for cmd, msg in shell_brace_suggestions.items():
            # This should NOT raise ValueError (previous bug with format_map)
            try:
                result_claude = format_suggestion(msg, "claude")
                result_gemini = format_suggestion(msg, "gemini")
            except ValueError as e:
                raise AssertionError(
                    f"format_suggestion raised ValueError on shell syntax in '{cmd}': {e}\n"
                    f"Suggestion string: {msg!r}\n"
                    f"This is a regression — str.replace() should handle bare {{}} safely."
                ) from e

            # Verify shell syntax is preserved (not replaced or removed)
            assert "{}" in result_claude, (
                f"Shell brace {{}} was incorrectly removed from '{cmd}' suggestion for claude"
            )
            assert "{}" in result_gemini, (
                f"Shell brace {{}} was incorrectly removed from '{cmd}' suggestion for gemini"
            )
