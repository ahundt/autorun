#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Integration tests for autorun plugin functionality.

Migrated from autorun.main.main(_exit=False) + mocked session_state to canonical daemon-path:
  EventContext + plugins.app.dispatch(ctx)

Canonical path: EventContext + plugins.app.dispatch(ctx) → registered command handlers.
Legacy plugin_module.main(_exit=False) was removed — use run_direct() in __main__.py.
"""
import uuid
import pytest
import sys
from pathlib import Path

# Add src directory to Python path
src_path = str(Path(__file__).parent.parent / "src")
sys.path.insert(0, src_path)

from autorun.core import EventContext, ThreadSafeDB
from autorun import plugins


def _make_ctx(prompt: str, session_id: str = None, event: str = "UserPromptSubmit") -> EventContext:
    """Create EventContext for testing. Canonical replacement for plugin_module.main() + mock."""
    sid = session_id or f"test-plugin-{uuid.uuid4().hex[:8]}"
    return EventContext(
        session_id=sid,
        event=event,
        prompt=prompt,
        tool_name="",
        tool_input={},
        store=ThreadSafeDB(),
    )


def _dispatch(prompt: str, session_id: str = None) -> dict:
    """Dispatch via canonical daemon-path. Returns response dict."""
    return plugins.app.dispatch(_make_ctx(prompt, session_id))


class TestPluginIntegration:
    """Test plugin integration with Claude Code.

    Migrated from plugin_module.main(_exit=False) + mocked session_state to EventContext + dispatch.

    Canonical string changes from migration:
      - Stop: "✅ Stopped" (was "Autorun stopped")
      - Emergency stop: "⚠️ EMERGENCY STOP\n..." (was "Emergency stop activated")
    """

    @pytest.mark.plugin
    @pytest.mark.integration
    def test_plugin_handles_policy_commands(self):
        """Test plugin correctly handles file policy commands."""
        result = _dispatch("/afs")

        assert result["continue"] is True, "Policy command should continue to AI"
        assert "strict-search" in result["systemMessage"], \
            "systemMessage should contain policy info"

    @pytest.mark.plugin
    @pytest.mark.integration
    def test_plugin_handles_normal_commands(self):
        """Test plugin allows normal commands to continue to AI."""
        result = _dispatch("help me understand this code")

        assert result["continue"] is True, "Normal command should continue to AI"
        assert result.get("stopReason", "") == "", "Normal command should have empty stopReason"
        assert result.get("systemMessage", "") == "", "Normal command should have empty systemMessage"

    @pytest.mark.plugin
    @pytest.mark.integration
    def test_plugin_handles_all_policy_commands(self):
        """Test plugin handles all different policy commands."""
        policy_commands = [
            ("/afs", "strict-search"),
            ("/afa", "allow-all"),
            ("/afj", "justify-create"),
            ("/afst", "AutoFile policy:"),  # Fresh session default policy shown
        ]

        for command, expected_content in policy_commands:
            # Use different session_id for each command to avoid state conflicts
            result = _dispatch(command)

            assert result["continue"] is True, f"{command} should continue to AI"
            assert expected_content in result["systemMessage"], \
                f"{command} systemMessage should contain {expected_content!r}"

    @pytest.mark.plugin
    @pytest.mark.integration
    def test_plugin_handles_control_commands(self):
        """Test plugin handles control commands.

        Canonical string changes:
          - /autostop → "✅ Stopped" (canonical handle_stop response)
          - /estop → "⚠️ EMERGENCY STOP\n..." (canonical handle_sos response)
        """
        control_commands = [
            ("/autostop", "Stopped"),       # canonical: "✅ Stopped"
            ("/estop", "EMERGENCY STOP"),   # canonical: "⚠️ EMERGENCY STOP"
        ]

        for command, expected_content in control_commands:
            result = _dispatch(command)

            assert result["continue"] is False, f"{command} should not continue to AI"
            assert expected_content in result["systemMessage"], \
                f"{command} systemMessage should contain {expected_content!r}"

    @pytest.mark.plugin
    @pytest.mark.integration
    def test_plugin_handles_autorun_command(self):
        """Test plugin handles autorun activation command."""
        result = _dispatch("/autorun test task description")

        assert result["continue"] is True, \
            "Autorun command should continue to AI with injection template"
        assert "UNINTERRUPTED" in result["systemMessage"] or "Autorun" in result["systemMessage"], \
            "systemMessage should contain injection template or task acknowledgment"

    @pytest.mark.plugin
    @pytest.mark.integration
    def test_plugin_maintains_session_state(self):
        """Test plugin maintains session state across commands.

        Uses shared session_id + ThreadSafeDB so policy persists between dispatches.
        """
        session_id = f"plugin-test-{uuid.uuid4().hex[:8]}"

        # First command: set strict-search policy
        result1 = _dispatch("/afs", session_id)
        assert "strict-search" in result1["systemMessage"], "Policy must be set"

        # Second command: check status — should reflect the set policy
        result2 = _dispatch("/afst", session_id)
        assert "strict-search" in result2["systemMessage"], \
            "Status should reflect previously set policy"

    @pytest.mark.plugin
    @pytest.mark.integration
    def test_plugin_handles_invalid_command_gracefully(self):
        """Test plugin handles invalid/unknown commands as pass-through to AI."""
        result = _dispatch("not a valid /ar: command")

        # Unknown prompts are pass-through (continue to AI)
        assert result["continue"] is True, "Unknown command should pass-through to AI"

    @pytest.mark.plugin
    @pytest.mark.integration
    def test_plugin_handles_missing_fields(self):
        """Test plugin handles minimal context gracefully."""
        # EventContext with only required fields, no store
        ctx = EventContext(
            session_id=f"test-min-{uuid.uuid4().hex[:8]}",
            event="UserPromptSubmit",
            prompt="",
            tool_name="",
            tool_input={},
        )
        result = plugins.app.dispatch(ctx)

        assert result["continue"] is True
        assert result.get("stopReason", "") == ""
        assert result.get("systemMessage", "") == ""


class TestPluginJsonOutput:
    """Test plugin JSON output format — canonical dict-based assertions."""

    @pytest.mark.plugin
    def test_output_format_is_valid_dict(self):
        """Test plugin output is a valid dict (canonical path returns dict, not JSON string)."""
        result = _dispatch("/ar:st")

        assert isinstance(result, dict), "Plugin output should be a dict"

    @pytest.mark.plugin
    def test_output_has_required_fields(self):
        """Test plugin output has required fields."""
        result = _dispatch("/ar:st")

        required_fields = ["continue", "stopReason", "suppressOutput", "systemMessage"]
        for field in required_fields:
            assert field in result, f"Output should have '{field}' field"

    @pytest.mark.plugin
    def test_output_types_are_correct(self):
        """Test plugin output fields have correct types."""
        result = _dispatch("/ar:st")

        assert isinstance(result["continue"], bool), "continue field should be boolean"
        assert isinstance(result["stopReason"], str), "stopReason field should be string"
        assert isinstance(result["suppressOutput"], bool), "suppressOutput field should be boolean"
        assert isinstance(result["systemMessage"], str), "systemMessage field should be string"


class TestPluginErrorHandling:
    """Test plugin error handling and edge cases."""

    @pytest.mark.plugin
    def test_plugin_handles_empty_input(self):
        """Test plugin handles empty prompt gracefully (pass-through to AI)."""
        result = _dispatch("")

        # Empty prompt is a non-command → pass-through (continue=True)
        assert result["continue"] is True
        assert isinstance(result, dict), "Should return dict response"

    @pytest.mark.plugin
    def test_plugin_handles_unicode_characters(self):
        """Test plugin handles unicode characters in prompts."""
        result = _dispatch("help with café résumé 📝")

        # Unicode non-command → pass-through to AI
        assert result["continue"] is True

    @pytest.mark.plugin
    def test_plugin_handles_long_prompts(self):
        """Test plugin handles very long prompts."""
        long_prompt = "test " * 1000  # Very long prompt
        result = _dispatch(long_prompt)

        # Long non-command → pass-through to AI
        assert result["continue"] is True
