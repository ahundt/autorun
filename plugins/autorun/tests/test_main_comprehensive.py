#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Comprehensive tests for main.py to achieve 80% coverage.
Tests core functions, handlers, and CONFIG processing.
"""
import pytest
import sys
from pathlib import Path
from unittest.mock import patch, Mock

# Add src directory to Python path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from autorun.main import (
    CONFIG, COMMAND_HANDLERS, build_hook_response,
    handle_search, handle_allow, handle_justify, handle_status,
    handle_stop, handle_emergency_stop, handle_activate,
    log_info, inject_continue_prompt, inject_verification_prompt,
    is_premature_stop, stop_handler,
    claude_code_handler
)
from autorun.core import EventContext, ThreadSafeDB
from autorun import plugins
from autorun.session_manager import session_state


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


class TestBuildHookResponse:
    """Test build_hook_response function"""

    def test_default_response(self):
        """Test default hook response"""
        response = build_hook_response()
        assert response["continue"]
        assert response["stopReason"] == ""
        assert not response["suppressOutput"]
        assert response["systemMessage"] == ""

    def test_custom_response(self):
        """Test custom hook response"""
        response = build_hook_response(
            continue_execution=False,
            stop_reason="test reason",
            system_message="test message"
        )
        assert not response["continue"]
        assert response["stopReason"] == "test reason"
        assert response["systemMessage"] == "test message"

    def test_partial_custom_response(self):
        """Test partial custom response"""
        response = build_hook_response(system_message="only message")
        assert response["continue"]
        assert response["systemMessage"] == "only message"


class TestBuildPretoolUseResponse:
    """Test daemon-path response building via EventContext.deny/allow"""

    def _make_ctx(self, tool_name="Bash", command="echo test"):
        return EventContext(
            session_id=f"test-resp-{id(self)}",
            event="PreToolUse",
            tool_name=tool_name,
            tool_input={"command": command},
            store=ThreadSafeDB(),
        )

    def test_default_allow_response(self):
        """Test default allow response via ctx.allow()"""
        ctx = self._make_ctx()
        response = ctx.allow()
        assert response["hookSpecificOutput"]["permissionDecision"] == "allow"
        assert response["continue"]

    def test_deny_response(self):
        """Test deny response via ctx.deny()"""
        ctx = self._make_ctx()
        response = ctx.deny("blocked by policy")
        assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "blocked by policy" in response["hookSpecificOutput"]["permissionDecisionReason"]

    def test_allow_with_reason(self):
        """Test allow with reason via ctx.allow()"""
        ctx = self._make_ctx()
        response = ctx.allow("permitted action")
        assert response["hookSpecificOutput"]["permissionDecision"] == "allow"
        assert "permitted action" in response["hookSpecificOutput"]["permissionDecisionReason"]


class TestPolicyHandlers:
    """Test file policy handler functions"""

    def test_handle_search(self):
        """Test SEARCH policy handler"""
        state = {}
        response = handle_search(state)
        assert "strict-search" in response.lower()
        assert state["file_policy"] == "SEARCH"

    def test_handle_allow(self):
        """Test ALLOW policy handler"""
        state = {}
        response = handle_allow(state)
        assert "allow-all" in response.lower()
        assert state["file_policy"] == "ALLOW"

    def test_handle_justify(self):
        """Test JUSTIFY policy handler"""
        state = {}
        response = handle_justify(state)
        assert "justify" in response.lower()
        assert state["file_policy"] == "JUSTIFY"

    def test_handle_status_default_policy(self):
        """Test STATUS handler with default policy"""
        state = {}  # No policy set - should default to ALLOW
        response = handle_status(state)
        assert "allow-all" in response.lower()

    def test_handle_status_with_search_policy(self):
        """Test STATUS handler with SEARCH policy"""
        state = {"file_policy": "SEARCH"}
        response = handle_status(state)
        assert "strict-search" in response.lower()


class TestControlHandlers:
    """Test control handler functions"""

    def test_handle_stop(self):
        """Test stop handler"""
        state = {}
        response = handle_stop(state)
        assert response == "Autorun stopped"
        assert state["session_status"] == "stopped"

    def test_handle_emergency_stop(self):
        """Test emergency stop handler"""
        state = {}
        response = handle_emergency_stop(state)
        assert response == "Emergency stop activated"
        assert state["session_status"] == "emergency_stopped"

    def test_handle_activate(self):
        """Test activate handler"""
        state = {"session_id": "test_session"}
        prompt = "/autorun test task description"
        response = handle_activate(state, prompt)

        assert "UNINTERRUPTED, FULLY AUTONOMOUS" in response
        assert state["session_status"] == "active"
        assert state["autorun_stage"] == "INITIAL"
        assert state["activation_prompt"] == prompt


class TestCONFIGStructure:
    """Test CONFIG dictionary structure"""

    def test_stage_confirmations_exist(self):
        """Test all stage confirmations exist"""
        assert "stage1_message" in CONFIG
        assert "stage2_message" in CONFIG
        assert "stage3_message" in CONFIG

    def test_stage_instructions_exist(self):
        """Test all stage instructions exist"""
        assert "stage1_instruction" in CONFIG
        assert "stage2_instruction" in CONFIG
        assert "stage3_instruction" in CONFIG

    def test_emergency_stop_exists(self):
        """Test emergency stop key exists with DESCRIPTIVE value"""
        assert "emergency_stop" in CONFIG
        # NOTE: emergency_stop should be DESCRIPTIVE (describing what the AI is doing)
        assert CONFIG["emergency_stop"] == "AUTORUN_STATE_PRESERVATION_EMERGENCY_STOP"

    def test_policies_structure(self):
        """Test policies structure"""
        assert "policies" in CONFIG
        for policy in ["ALLOW", "JUSTIFY", "SEARCH"]:
            assert policy in CONFIG["policies"]
            assert isinstance(CONFIG["policies"][policy], tuple)
            assert len(CONFIG["policies"][policy]) == 2

    def test_command_mappings_structure(self):
        """Test command mappings structure"""
        assert "command_mappings" in CONFIG
        expected_commands = ["/afs", "/afa", "/afj", "/afst", "/autostop", "/estop", "/autorun"]
        for cmd in expected_commands:
            assert cmd in CONFIG["command_mappings"]

    def test_timing_values(self):
        """Test timing configuration values"""
        assert CONFIG["max_recheck_count"] == 3
        assert CONFIG["monitor_stop_delay_seconds"] == 300
        assert CONFIG["stage3_countdown_calls"] == 5


class TestInjectionTemplates:
    """Test injection template functions"""

    def test_injection_template_has_placeholders(self):
        """Test injection template has required placeholders"""
        template = CONFIG["injection_template"]
        required = [
            "{stage1_instruction}", "{stage1_message}",
            "{stage2_instruction}", "{stage2_message}",
            "{stage3_instruction}", "{stage3_message}",
            "{emergency_stop}", "{policy_instructions}"
        ]
        for placeholder in required:
            assert placeholder in template, f"Missing {placeholder}"

    def test_recheck_template_has_placeholders(self):
        """Test recheck template has required placeholders"""
        template = CONFIG["recheck_template"]
        required = ["{activation_prompt}", "{recheck_count}", "{max_recheck_count}"]
        for placeholder in required:
            assert placeholder in template, f"Missing {placeholder}"


class TestInjectContinuePrompt:
    """Test inject_continue_prompt function"""

    def test_inject_continue_with_active_state(self):
        """Test continue prompt injection with active state"""
        state = {
            "session_status": "active",
            "file_policy": "ALLOW",
            "autorun_stage": "INITIAL",
            "hook_call_count": 0
        }

        response = inject_continue_prompt(state)

        # Should return a hook response dict
        assert "continue" in response
        assert "systemMessage" in response
        assert response["continue"]


class TestPretoolUseHandler:
    """Test daemon-path PreToolUse chain (enforce_file_policy + check_blocked_commands)"""

    def setup_method(self):
        """Unique session per test to avoid state leakage."""
        import uuid
        self.session_id = f"test-main-ptu-{uuid.uuid4().hex[:8]}"

    def _ctx(self, tool_name, tool_input):
        return EventContext(
            session_id=self.session_id,
            event="PreToolUse",
            tool_name=tool_name,
            tool_input=tool_input,
            store=ThreadSafeDB(),
        )

    def test_non_write_tool_allowed(self):
        """Test non-Write tool is allowed regardless of policy"""
        with session_state(self.session_id) as s:
            s["file_policy"] = "SEARCH"
        ctx = self._ctx("Read", {"file_path": "/some/path"})
        response = _pretooluse(ctx)
        assert response["hookSpecificOutput"]["permissionDecision"] == "allow"

    def test_write_existing_file_allowed(self):
        """Test Write to existing file is allowed even under SEARCH policy"""
        import tempfile, os
        with session_state(self.session_id) as s:
            s["file_policy"] = "SEARCH"
        # Create a real temp file that exists
        with tempfile.NamedTemporaryFile(delete=False) as f:
            existing_path = f.name
        try:
            ctx = self._ctx("Write", {"file_path": existing_path})
            response = _pretooluse(ctx)
            assert response["hookSpecificOutput"]["permissionDecision"] == "allow"
        finally:
            os.unlink(existing_path)

    def test_write_new_file_blocked_by_search_policy(self):
        """Test Write to new file is blocked under SEARCH policy"""
        with session_state(self.session_id) as s:
            s["file_policy"] = "SEARCH"
        ctx = self._ctx("Write", {"file_path": "/tmp/nonexistent_autorun_test_xyz_main_comp.txt"})
        response = _pretooluse(ctx)
        assert response["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_write_new_file_allowed_by_allow_policy(self):
        """Test Write to new file is allowed under ALLOW policy"""
        with session_state(self.session_id) as s:
            s["file_policy"] = "ALLOW"
        ctx = self._ctx("Write", {"file_path": "/tmp/nonexistent_autorun_test_xyz_main_comp.txt"})
        response = _pretooluse(ctx)
        assert response["hookSpecificOutput"]["permissionDecision"] == "allow"


class TestClaudeCodeHandler:
    """Test claude_code_handler function"""

    def test_policy_command_handling(self):
        """Test policy command is handled correctly"""
        ctx = Mock()
        ctx.prompt = "/afs"
        ctx.session_id = "test_session"
        ctx.session_transcript = []

        with patch('autorun.main.session_state') as mock_session:
            mock_state = {}
            mock_session.return_value.__enter__.return_value = mock_state
            mock_session.return_value.__exit__.return_value = None

            response = claude_code_handler(ctx)

        assert response["continue"]
        assert "strict-search" in response["systemMessage"].lower()

    def test_normal_command_passthrough(self):
        """Test normal commands pass through to AI"""
        ctx = Mock()
        ctx.prompt = "explain this code"
        ctx.session_id = "test_session"
        ctx.session_transcript = []

        with patch('autorun.main.session_state') as mock_session:
            mock_state = {}
            mock_session.return_value.__enter__.return_value = mock_state
            mock_session.return_value.__exit__.return_value = None

            response = claude_code_handler(ctx)

        assert response["continue"]
        assert response["systemMessage"] == ""


class TestStopHandler:
    """Test stop_handler function"""

    def test_non_autorun_session(self):
        """Test non-autorun session is handled correctly"""
        ctx = Mock()
        ctx.session_id = "test_session"
        ctx.session_transcript = []

        with patch('autorun.main.session_state') as mock_session:
            mock_state = {"session_status": "inactive"}
            mock_session.return_value.__enter__.return_value = mock_state
            mock_session.return_value.__exit__.return_value = None

            response = stop_handler(ctx)

        # Non-autorun sessions should continue normally
        assert response["continue"]

    def test_active_session_continue(self):
        """Test active autorun session continues"""
        ctx = Mock()
        ctx.session_id = "test_session"
        ctx.session_transcript = ["working on task"]

        with patch('autorun.main.session_state') as mock_session:
            mock_state = {
                "session_status": "active",
                "autorun_stage": "INITIAL",
                "hook_call_count": 0
            }
            mock_session.return_value.__enter__.return_value = mock_state
            mock_session.return_value.__exit__.return_value = None

            response = stop_handler(ctx)

        assert response["continue"]


class TestLogInfo:
    """Test log_info function"""

    def test_log_info_no_error(self):
        """Test log_info doesn't raise errors"""
        # Should not raise any exception
        log_info("Test message")
        log_info("Another test message with special chars: !@#$%^&*()")


class TestCommandHandlersRegistry:
    """Test COMMAND_HANDLERS registry"""

    def test_all_required_handlers_exist(self):
        """Test all required handlers exist in registry"""
        required = ["SEARCH", "ALLOW", "JUSTIFY", "STATUS", "status", "stop", "STOP",
                   "emergency_stop", "EMERGENCY_STOP", "activate"]

        for handler_name in required:
            assert handler_name in COMMAND_HANDLERS, f"Missing handler: {handler_name}"

    def test_handlers_are_callable(self):
        """Test all handlers are callable"""
        for name, handler in COMMAND_HANDLERS.items():
            assert callable(handler), f"Handler {name} is not callable"

    def test_policy_handlers_share_functions(self):
        """Test uppercase and lowercase versions share the same function"""
        # status and STATUS should be the same function
        assert COMMAND_HANDLERS["STATUS"] == COMMAND_HANDLERS["status"]
        assert COMMAND_HANDLERS["STOP"] == COMMAND_HANDLERS["stop"]
        assert COMMAND_HANDLERS["EMERGENCY_STOP"] == COMMAND_HANDLERS["emergency_stop"]


class TestIsPrematureStop:
    """Test is_premature_stop function"""

    def test_premature_stop_no_markers(self):
        """Test premature stop when no completion markers present"""
        ctx = Mock()
        ctx.session_transcript = ["working on task", "still working"]

        state = {"session_status": "active"}

        result = is_premature_stop(ctx, state)
        assert result  # No markers = premature

    def test_not_premature_with_stage1_marker(self):
        """Test not premature when stage1 marker present"""
        ctx = Mock()
        ctx.session_transcript = [f"Task complete {CONFIG['stage1_message']}"]

        state = {"session_status": "active"}

        result = is_premature_stop(ctx, state)
        assert not result  # Has marker = not premature

    def test_not_premature_with_stage2_marker(self):
        """Test not premature when stage2 marker present"""
        ctx = Mock()
        ctx.session_transcript = [f"Stage 2 complete {CONFIG['stage2_message']}"]

        state = {"session_status": "active"}

        result = is_premature_stop(ctx, state)
        assert not result

    def test_not_premature_with_stage3_marker(self):
        """Test not premature when stage3 marker present"""
        ctx = Mock()
        ctx.session_transcript = [f"All done {CONFIG['stage3_message']}"]

        state = {"session_status": "active"}

        result = is_premature_stop(ctx, state)
        assert not result

    def test_premature_with_inactive_session(self):
        """Test premature check with inactive session"""
        ctx = Mock()
        ctx.session_transcript = []

        state = {"session_status": "inactive"}

        result = is_premature_stop(ctx, state)
        # Inactive sessions should not be considered premature
        assert not result


class TestInjectVerificationPrompt:
    """Test inject_verification_prompt function"""

    def test_inject_verification_returns_response(self):
        """Test inject_verification_prompt returns hook response"""
        state = {
            "activation_prompt": "/autorun test task",
            "autorun_stage": "INITIAL"
        }

        result = inject_verification_prompt(state)

        assert isinstance(result, dict)
        assert "continue" in result
        assert "systemMessage" in result


class TestAdditionalPolicyHandlers:
    """Additional tests for policy handlers"""

    def test_handle_search_returns_correct_message(self):
        """Test SEARCH handler message format"""
        state = {}
        response = handle_search(state)
        assert "strict-search" in response.lower()
        assert state["file_policy"] == "SEARCH"

    def test_handle_allow_returns_correct_message(self):
        """Test ALLOW handler message format"""
        state = {}
        response = handle_allow(state)
        assert "allow-all" in response.lower()
        assert state["file_policy"] == "ALLOW"

    def test_handle_justify_returns_correct_message(self):
        """Test JUSTIFY handler message format"""
        state = {}
        response = handle_justify(state)
        assert "justify" in response.lower()
        assert state["file_policy"] == "JUSTIFY"


class TestCONFIGPolicies:
    """Test CONFIG policies structure"""

    def test_policy_blocked_messages_exist(self):
        """Test policy blocked messages exist"""
        assert "policy_blocked" in CONFIG
        assert "SEARCH" in CONFIG["policy_blocked"]
        assert "JUSTIFY" in CONFIG["policy_blocked"]

    def test_policy_blocked_messages_content(self):
        """Test policy blocked messages contain expected content"""
        search_blocked = CONFIG["policy_blocked"]["SEARCH"]
        assert "Blocked" in search_blocked
        assert "STRICT SEARCH" in search_blocked

        justify_blocked = CONFIG["policy_blocked"]["JUSTIFY"]
        assert "Blocked" in justify_blocked
        assert "JUSTIFICATION" in justify_blocked


class TestPretoolUseEdgeCases:
    """Test daemon-path PreToolUse edge cases"""

    def setup_method(self):
        import uuid
        self.session_id = f"test-main-edge-{uuid.uuid4().hex[:8]}"

    def _ctx(self, tool_name, tool_input, transcript=None):
        return EventContext(
            session_id=self.session_id,
            event="PreToolUse",
            tool_name=tool_name,
            tool_input=tool_input,
            session_transcript=transcript or [],
            store=ThreadSafeDB(),
        )

    def test_justify_policy_with_justification(self):
        """Test JUSTIFY policy allows when justification found"""
        with session_state(self.session_id) as s:
            s["file_policy"] = "JUSTIFY"
        ctx = self._ctx(
            "Write",
            {"file_path": "/tmp/nonexistent_autorun_test_xyz_edge.py"},
            ["<AUTOFILE_JUSTIFICATION>Need new file</AUTOFILE_JUSTIFICATION>"],
        )
        response = _pretooluse(ctx)

        # Should allow because justification is in transcript
        assert response["hookSpecificOutput"]["permissionDecision"] == "allow"

    def test_justify_policy_without_justification(self):
        """Test JUSTIFY policy blocks when no justification"""
        with session_state(self.session_id) as s:
            s["file_policy"] = "JUSTIFY"
        ctx = self._ctx(
            "Write",
            {"file_path": "/tmp/nonexistent_autorun_test_xyz_edge.py"},
            ["Just creating a file"],
        )
        response = _pretooluse(ctx)

        # Should deny because no justification
        assert response["hookSpecificOutput"]["permissionDecision"] == "deny"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
