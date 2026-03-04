#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Integration tests for autorun hook functionality.

Migrated from main(_exit=False) + mocked session_state to canonical daemon-path:
  EventContext + plugins.app.dispatch(ctx)

Canonical path: EventContext + plugins.app.dispatch(ctx) → registered command handlers.
Legacy main(_exit=False) was removed — it now delegates to run_hook_handler() → daemon.
"""
import uuid
import time
import pytest
import sys
from pathlib import Path

# Add src directory to Python path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from autorun.core import EventContext, ThreadSafeDB
from autorun import plugins, build_hook_response


def _make_ctx(prompt: str, session_id: str = None, event: str = "UserPromptSubmit") -> EventContext:
    """Create EventContext for testing. Canonical replacement for main(_exit=False) + mock."""
    sid = session_id or f"test-hook-{uuid.uuid4().hex[:8]}"
    return EventContext(
        session_id=sid,
        event=event,
        prompt=prompt,
        tool_name="",
        tool_input={},
        store=ThreadSafeDB(),
    )


def _dispatch(prompt: str, session_id: str = None) -> dict:
    """Dispatch via canonical daemon-path. Returns response dict.

    Canonical replacement for: main(_exit=False) + capsys → json.loads(captured.out)
    """
    return plugins.app.dispatch(_make_ctx(prompt, session_id))


class TestHookIntegration:
    """Test hook integration with Claude Code.

    Migrated from main(_exit=False) + mocked session_state to EventContext + dispatch.
    """

    @pytest.mark.hook
    @pytest.mark.integration
    def test_hook_handles_policy_commands(self):
        """Test hook correctly handles file policy commands."""
        result = _dispatch("/ar:a")  # canonical: /afa alias also registered

        assert result["continue"] is True, "Policy command should continue to AI"
        assert "allow-all" in result["systemMessage"], "Response should contain policy info"

    @pytest.mark.hook
    @pytest.mark.integration
    def test_hook_handles_normal_commands(self):
        """Test hook allows normal commands to continue to AI."""
        result = _dispatch("explain this python code")

        assert result["continue"] is True, "Normal command should continue to AI"
        assert result.get("systemMessage", "") == "", "Normal command should have no system message"

    @pytest.mark.hook
    @pytest.mark.integration
    def test_hook_response_format(self):
        """Test hook response format matches Claude Code expectations."""
        result = _dispatch("/ar:f")  # canonical: /afs alias also registered

        # Check required Claude Code hook response fields
        required_fields = ["continue", "stopReason", "suppressOutput", "systemMessage"]
        for field in required_fields:
            assert field in result, f"Hook response should have '{field}' field"

        assert isinstance(result["continue"], bool)
        assert isinstance(result["stopReason"], str)
        assert isinstance(result["suppressOutput"], bool)
        assert isinstance(result["systemMessage"], str)

    @pytest.mark.hook
    @pytest.mark.integration
    def test_hook_handles_all_commands(self):
        """Test hook handles all command types correctly.

        Canonical strings after migration:
          - Stop: "Stopped" (was "Autorun stopped")
          - Emergency stop: "EMERGENCY STOP" (was "Emergency stop activated")
        All legacy aliases (/afs, /afa, /afj, /afst, /autostop, /estop) still registered.
        """
        test_cases = [
            ("/afs", True, "strict-search"),
            ("/afa", True, "allow-all"),
            ("/afj", True, "justify-create"),
            ("/afst", True, "AutoFile policy:"),
            ("/autostop", False, "Stopped"),       # canonical: "✅ Stopped"
            ("/estop", False, "EMERGENCY STOP"),   # canonical: "⚠️ EMERGENCY STOP"
            ("normal command", True, ""),
            ("help me please", True, ""),
            ("what is this", True, ""),
        ]

        for prompt, should_continue, expected_content in test_cases:
            result = _dispatch(prompt)

            assert result["continue"] == should_continue, \
                f"Command {prompt!r} continue flag incorrect (got {result['continue']})"
            if expected_content:
                assert expected_content in result["systemMessage"], \
                    f"Command {prompt!r}: expected {expected_content!r} in systemMessage"

    @pytest.mark.hook
    @pytest.mark.integration
    def test_hook_maintains_session_state(self):
        """Test hook maintains session state across commands.

        Uses shared session_id so policy set by first dispatch persists
        to the second dispatch (EventContext → ThreadSafeDB → session_state JSON).
        """
        session_id = f"hook-session-{uuid.uuid4().hex[:8]}"

        # First command: set strict-search policy
        result1 = _dispatch("/ar:f", session_id)  # /afs alias
        assert "strict-search" in result1["systemMessage"], "Policy must be set to strict-search"

        # Second command: check status — should reflect the set policy
        result2 = _dispatch("/ar:st", session_id)  # /afst alias
        assert "strict-search" in result2["systemMessage"], \
            "Status should reflect previously set policy"

    @pytest.mark.hook
    @pytest.mark.integration
    def test_hook_handles_autorun_command(self):
        """Test hook handles autorun activation command."""
        result = _dispatch("/autorun create a new python module")

        assert result["continue"] is True, \
            "Autorun activation should continue to AI with injection template"
        assert "UNINTERRUPTED" in result["systemMessage"] or "Autorun" in result["systemMessage"], \
            "Response should contain injection template or task acknowledgment"


class TestHookErrorHandling:
    """Test hook error handling and edge cases.

    Tests now verify EventContext dispatch robustness instead of JSON-parse errors
    from stdin (those are covered in test_hook_entry.py subprocess tests).
    """

    @pytest.mark.hook
    def test_hook_handles_empty_prompt(self):
        """Test hook handles empty prompt gracefully (canonical: pass-through to AI)."""
        result = _dispatch("")

        # Empty/non-command prompt → pass-through (continue to AI)
        assert result["continue"] is True, "Empty prompt should continue to AI"

    @pytest.mark.hook
    def test_hook_handles_missing_session_no_crash(self):
        """Test hook dispatch doesn't crash with minimal context."""
        # EventContext with only required fields
        ctx = EventContext(
            session_id=f"test-min-{uuid.uuid4().hex[:8]}",
            event="UserPromptSubmit",
            prompt="/ar:st",
            tool_name="",
            tool_input={},
        )
        result = plugins.app.dispatch(ctx)

        assert isinstance(result, dict), "Should return dict response"
        assert "continue" in result, "Response must have continue field"

    @pytest.mark.hook
    def test_hook_handles_different_hook_events(self):
        """Test hook handles different hook events without crashing."""
        event_types = [
            ("UserPromptSubmit", "/ar:st"),
            ("PreToolUse", ""),
            ("PostToolUse", ""),
            ("Stop", ""),
        ]

        for event, prompt in event_types:
            ctx = EventContext(
                session_id=f"test-event-{uuid.uuid4().hex[:8]}",
                event=event,
                prompt=prompt,
                tool_name="Bash" if event in ("PreToolUse", "PostToolUse") else "",
                tool_input={"command": "echo test"} if event in ("PreToolUse", "PostToolUse") else {},
            )
            # Should not crash for any event type
            result = plugins.app.dispatch(ctx)
            # Result may be None (pass-through) or a dict
            assert result is None or isinstance(result, dict), \
                f"Event {event!r} should return None or dict, got {type(result)}"


class TestHookResponseBuilder:
    """Test hook response builder function — unchanged from old tests."""

    @pytest.mark.hook
    def test_build_hook_response_defaults(self):
        """Test build_hook_response with default values."""
        response = build_hook_response()

        assert response["continue"] is True
        assert response["stopReason"] == ""
        assert response["suppressOutput"] is False
        assert response["systemMessage"] == ""

    @pytest.mark.hook
    def test_build_hook_response_custom_values(self):
        """Test build_hook_response with custom values."""
        response = build_hook_response(
            continue_execution=False,
            stop_reason="test reason",
            system_message="test message"
        )

        assert response["continue"] is False
        assert response["stopReason"] == "test reason"
        assert response["suppressOutput"] is False
        assert response["systemMessage"] == "test message"

    @pytest.mark.hook
    def test_build_hook_response_partial_values(self):
        """Test build_hook_response with partial custom values."""
        response = build_hook_response(
            continue_execution=False,
            system_message="only custom message"
        )

        assert response["continue"] is False
        assert response["stopReason"] == ""
        assert response["suppressOutput"] is False
        assert response["systemMessage"] == "only custom message"

    @pytest.mark.hook
    def test_build_hook_response_types(self):
        """Test build_hook_response returns correct types."""
        response = build_hook_response()

        assert isinstance(response["continue"], bool)
        assert isinstance(response["stopReason"], str)
        assert isinstance(response["suppressOutput"], bool)
        assert isinstance(response["systemMessage"], str)


class TestHookPerformance:
    """Test hook performance characteristics using canonical dispatch."""

    @pytest.mark.hook
    @pytest.mark.integration
    def test_hook_response_speed(self):
        """Test hook responds quickly using canonical dispatch (performance test)."""
        start_time = time.time()
        result = _dispatch("/ar:st")
        response_time = time.time() - start_time

        # Should respond in reasonable time (< 1 second, no daemon round-trip)
        assert response_time < 1.0, f"Hook response took {response_time:.3f}s, should be < 1.0s"
        assert isinstance(result, dict), "Should return valid response even in performance test"

    @pytest.mark.hook
    @pytest.mark.integration
    def test_hook_handles_multiple_commands_quickly(self):
        """Test hook handles multiple commands quickly using canonical dispatch."""
        commands = [
            "/afs", "/afa", "/afj", "/afst", "/autostop", "/estop",
            "normal command 1", "normal command 2"
        ]

        total_start = time.time()
        results = []

        for command in commands:
            results.append(_dispatch(command))

        total_time = time.time() - total_start

        # Should handle all commands quickly (< 2 seconds total, no daemon)
        assert total_time < 2.0, f"Multiple commands took {total_time:.3f}s, should be < 2.0s"
        assert len(results) == len(commands), "Should have response for each command"
