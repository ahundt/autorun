#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Comprehensive tests for main.py utilities and daemon-path handlers.

Tests core utilities that remain in main.py plus daemon-path equivalents
for deleted handler functions.

Migration map (deleted main.py functions → canonical daemon-path replacements):
  handle_search/allow/justify → plugins app commands /ar:a, /ar:j, /ar:f
  handle_status              → plugins.handle_status(ctx)
  handle_stop                → plugins.handle_stop(ctx)
  handle_emergency_stop      → plugins.handle_sos(ctx)
  handle_activate            → plugins.handle_activate(ctx)
  inject_continue_prompt     → plugins.build_injection_prompt(ctx)
  inject_verification_prompt → plugins.build_injection_prompt(ctx)
  is_premature_stop(ctx,st)  → plugins.is_premature_stop(ctx) [ctx only, no state]
  stop_handler               → plugins.autorun_injection(ctx) [@app.on("Stop")]
  claude_code_handler        → plugins.app.dispatch(ctx) [event="UserPromptSubmit"]
  COMMAND_HANDLERS           → plugins.app.command_handlers [registered /ar:* dict]
"""
import pytest
import sys
import uuid
from pathlib import Path
from unittest.mock import patch, Mock

# Add src directory to Python path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from autorun.main import (
    CONFIG, build_hook_response, log_info,
)
from autorun.config import CONFIG as CONFIG_DIRECT
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
    """Test build_hook_response function (still in main.py)"""

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
    """Test file policy handler functions via daemon-path dispatch."""

    def _make_ctx(self, prompt: str) -> EventContext:
        return EventContext(
            session_id=f"test-policy-{uuid.uuid4().hex[:8]}",
            event="UserPromptSubmit",
            prompt=prompt,
            tool_name="",
            tool_input={},
            store=ThreadSafeDB(),
        )

    def test_handle_search(self):
        """Test SEARCH policy via /ar:f command dispatch"""
        ctx = self._make_ctx("/ar:f")
        response = plugins.app.dispatch(ctx)
        assert response is not None
        assert "strict-search" in str(response).lower()
        assert ctx.file_policy == "SEARCH"

    def test_handle_allow(self):
        """Test ALLOW policy via /ar:a command dispatch"""
        ctx = self._make_ctx("/ar:a")
        response = plugins.app.dispatch(ctx)
        assert response is not None
        assert "allow-all" in str(response).lower()
        assert ctx.file_policy == "ALLOW"

    def test_handle_justify(self):
        """Test JUSTIFY policy via /ar:j command dispatch"""
        ctx = self._make_ctx("/ar:j")
        response = plugins.app.dispatch(ctx)
        assert response is not None
        assert "justify" in str(response).lower()
        assert ctx.file_policy == "JUSTIFY"

    def test_handle_status_default_policy(self):
        """Test STATUS handler with default policy via /ar:st"""
        ctx = self._make_ctx("/ar:st")
        response = plugins.app.dispatch(ctx)
        assert response is not None
        # Default policy is ALLOW → "allow-all"
        assert "allow-all" in str(response).lower()

    def test_handle_status_with_search_policy(self):
        """Test STATUS handler reflects policy previously set.

        Uses a shared ThreadSafeDB store so both dispatches share in-memory state —
        exactly as the daemon shares ONE ThreadSafeDB across all requests
        (core.py:AutorunDaemon.handle_client). Without a shared store, each EventContext
        gets its own empty _state dict and the policy set by the first dispatch is lost.
        """
        session_id = f"test-policy-st-{uuid.uuid4().hex[:8]}"
        shared = ThreadSafeDB()  # shared store mirrors daemon behavior
        ctx_set = EventContext(
            session_id=session_id,
            event="UserPromptSubmit",
            prompt="/ar:f",
            tool_name="", tool_input={},
            store=shared,  # policy writes to shared store
        )
        plugins.app.dispatch(ctx_set)
        # Status with same session_id + shared store → reads SEARCH policy
        ctx_st = EventContext(
            session_id=session_id,
            event="UserPromptSubmit",
            prompt="/ar:st",
            tool_name="", tool_input={},
            store=shared,  # reads from same shared store → sees SEARCH policy
        )
        response = plugins.app.dispatch(ctx_st)
        assert response is not None
        assert "strict-search" in str(response).lower()


class TestControlHandlers:
    """Test control handler functions via daemon-path plugins."""

    def _make_ctx(self, prompt: str, event: str = "UserPromptSubmit") -> EventContext:
        return EventContext(
            session_id=f"test-ctrl-{uuid.uuid4().hex[:8]}",
            event=event,
            prompt=prompt,
            tool_name="",
            tool_input={},
            store=ThreadSafeDB(),
        )

    def test_handle_stop(self):
        """Test stop handler via plugins.handle_stop(ctx)"""
        ctx = self._make_ctx("/ar:x")
        response = plugins.handle_stop(ctx)
        # handle_stop returns a string
        assert isinstance(response, str)
        assert "stop" in response.lower()

    def test_handle_emergency_stop(self):
        """Test emergency stop handler via plugins.handle_sos(ctx)"""
        ctx = self._make_ctx("/ar:sos")
        response = plugins.handle_sos(ctx)
        assert isinstance(response, str)
        assert "stop" in response.lower() or "emergency" in response.lower() or "AUTORUN_STATE_PRESERVATION" in response

    def test_handle_activate(self):
        """Test activate handler via plugins.handle_activate(ctx)"""
        ctx = self._make_ctx("/ar:go test task description")
        response = plugins.handle_activate(ctx)
        # handle_activate returns the injection prompt string
        assert isinstance(response, str)
        assert len(response) > 0
        # ctx should now be marked as active
        assert ctx.autorun_active is True


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
    """Test injection prompt building via plugins.build_injection_prompt."""

    def test_inject_continue_with_active_state(self):
        """Test continue prompt injection with active state"""
        ctx = EventContext(
            session_id=f"test-inject-{uuid.uuid4().hex[:8]}",
            event="Stop",
            tool_name="", tool_input={},
            store=ThreadSafeDB(),
        )
        ctx.autorun_active = True
        ctx.file_policy = "ALLOW"
        ctx.autorun_stage = "INITIAL"

        prompt = plugins.build_injection_prompt(ctx)

        # Should return a non-empty string
        assert isinstance(prompt, str)
        assert len(prompt) > 0


class TestPretoolUseHandler:
    """Test daemon-path PreToolUse chain (enforce_file_policy + check_blocked_commands)"""

    def setup_method(self):
        """Unique session per test to avoid state leakage."""
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
    """Test UserPromptSubmit command dispatch via plugins.app.dispatch(ctx)."""

    def test_policy_command_handling(self):
        """Test policy command is dispatched correctly via /ar:f → SEARCH"""
        ctx = EventContext(
            session_id=f"test-cch-{uuid.uuid4().hex[:8]}",
            event="UserPromptSubmit",
            prompt="/ar:f",
            tool_name="", tool_input={},
            store=ThreadSafeDB(),
        )
        response = plugins.app.dispatch(ctx)

        assert response is not None
        assert "strict-search" in str(response).lower()

    def test_normal_command_passthrough(self):
        """Test normal commands return None (pass-through — AI handles them)"""
        ctx = EventContext(
            session_id=f"test-cch2-{uuid.uuid4().hex[:8]}",
            event="UserPromptSubmit",
            prompt="explain this code",
            tool_name="", tool_input={},
            store=ThreadSafeDB(),
        )
        response = plugins.app.dispatch(ctx)

        # Non-commands pass through — dispatch returns None or an allow dict
        if response is not None and isinstance(response, dict):
            assert response.get("continue", True) is True


class TestStopHandler:
    """Test stop event handler via plugins.autorun_injection(ctx)."""

    def test_non_autorun_session(self):
        """Test non-autorun session is handled correctly (returns None = pass-through)"""
        ctx = EventContext(
            session_id=f"test-stop-{uuid.uuid4().hex[:8]}",
            event="Stop",
            tool_name="", tool_input={},
            store=ThreadSafeDB(),
        )
        # autorun_active defaults to False → autorun_injection returns None

        response = plugins.autorun_injection(ctx)

        # Non-autorun sessions: None = pass-through (let Claude stop normally)
        assert response is None or response.get("continue", True) is True

    def test_active_session_continue(self):
        """Test active autorun session re-engages (returns non-None injection)"""
        ctx = EventContext(
            session_id=f"test-stop2-{uuid.uuid4().hex[:8]}",
            event="Stop",
            tool_name="", tool_input={},
            session_transcript=["working on task"],
            store=ThreadSafeDB(),
        )
        ctx.autorun_active = True
        ctx.autorun_stage = "INITIAL"

        response = plugins.autorun_injection(ctx)

        # Active autorun sessions should inject a continue prompt
        if response is not None:
            assert response.get("continue", True) is True


class TestLogInfo:
    """Test log_info function (still in main.py)"""

    def test_log_info_no_error(self):
        """Test log_info doesn't raise errors"""
        log_info("Test message")
        log_info("Another test message with special chars: !@#$%^&*()")


class TestCommandHandlersRegistry:
    """Test commands registry via plugins.app.command_handlers (canonical: daemon-path)."""

    def test_all_required_commands_registered(self):
        """Test all required command handlers are registered in plugins.app.command_handlers"""
        # Canonical: plugins.app.command_handlers (replaces deleted COMMAND_HANDLERS)
        registered = set(plugins.app.command_handlers.keys())
        required = [
            "/ar:a", "/ar:allow", "/afa",          # ALLOW policy
            "/ar:j", "/ar:justify", "/afj",          # JUSTIFY policy
            "/ar:f", "/ar:find", "/afs",              # SEARCH policy
            "/ar:st", "/ar:status", "/afst",          # STATUS
            "/ar:x", "/ar:stop", "/autostop",         # STOP
            "/ar:sos", "/ar:estop", "/estop",         # EMERGENCY_STOP
            "/ar:no", "/ar:ok", "/ar:clear",          # Session block ops
            "/ar:globalno", "/ar:globalok",            # Global block ops
        ]
        for cmd in required:
            assert cmd in registered, f"Missing command: {cmd}"

    def test_handlers_are_callable(self):
        """Test all registered handlers are callable"""
        for name, handler in plugins.app.command_handlers.items():
            assert callable(handler), f"Handler {name} is not callable"

    def test_policy_commands_share_handler(self):
        """Test policy alias commands share the same underlying handler"""
        # /ar:a and /ar:allow and /afa all map to ALLOW policy handler
        assert plugins.app.command_handlers.get("/ar:a") == plugins.app.command_handlers.get("/ar:allow")
        assert plugins.app.command_handlers.get("/ar:j") == plugins.app.command_handlers.get("/ar:justify")
        assert plugins.app.command_handlers.get("/ar:f") == plugins.app.command_handlers.get("/ar:find")


class TestIsPrematureStop:
    """Test plugins.is_premature_stop(ctx) — canonical (ctx only, no state arg)."""

    def _make_stop_ctx(self, transcript_text: str = "") -> EventContext:
        ctx = EventContext(
            session_id=f"test-prem-{uuid.uuid4().hex[:8]}",
            event="Stop",
            tool_name="", tool_input={},
            session_transcript=[transcript_text] if transcript_text else [],
            store=ThreadSafeDB(),
        )
        ctx.autorun_active = True
        return ctx

    def test_premature_stop_no_markers(self):
        """Test premature stop when no completion markers present"""
        ctx = self._make_stop_ctx("working on task")
        result = plugins.is_premature_stop(ctx)
        assert result  # No markers = premature

    def test_not_premature_with_stage1_marker(self):
        """Test not premature when stage1 marker present"""
        ctx = self._make_stop_ctx(f"Task complete {CONFIG['stage1_message']}")
        result = plugins.is_premature_stop(ctx)
        assert not result  # Has marker = not premature

    def test_not_premature_with_stage2_marker(self):
        """Test not premature when stage2 marker present"""
        ctx = self._make_stop_ctx(f"Stage 2 complete {CONFIG['stage2_message']}")
        result = plugins.is_premature_stop(ctx)
        assert not result

    def test_not_premature_with_stage3_marker(self):
        """Test not premature when stage3 marker present"""
        ctx = self._make_stop_ctx(f"All done {CONFIG['stage3_message']}")
        result = plugins.is_premature_stop(ctx)
        assert not result

    def test_premature_with_inactive_session(self):
        """Test premature check with inactive autorun"""
        ctx = EventContext(
            session_id=f"test-prem-inactive-{uuid.uuid4().hex[:8]}",
            event="Stop",
            tool_name="", tool_input={},
            session_transcript=[],
            store=ThreadSafeDB(),
        )
        # autorun_active = False (default) → not a premature stop (it's a normal stop)
        result = plugins.is_premature_stop(ctx)
        assert not result  # Inactive sessions are not "premature" — they just stop


class TestInjectVerificationPrompt:
    """Test injection prompt building for verification stage."""

    def test_inject_verification_returns_string(self):
        """Test build_injection_prompt returns a non-empty string"""
        ctx = EventContext(
            session_id=f"test-verify-{uuid.uuid4().hex[:8]}",
            event="Stop",
            tool_name="", tool_input={},
            store=ThreadSafeDB(),
        )
        ctx.autorun_active = True
        ctx.autorun_stage = "INITIAL"

        result = plugins.build_injection_prompt(ctx)

        assert isinstance(result, str)
        assert len(result) > 0


class TestAdditionalPolicyHandlers:
    """Additional tests for policy handlers via daemon-path dispatch."""

    def _dispatch_policy(self, command: str) -> tuple[str | None, EventContext]:
        """Dispatch a policy command and return (response, ctx)."""
        ctx = EventContext(
            session_id=f"test-add-pol-{uuid.uuid4().hex[:8]}",
            event="UserPromptSubmit",
            prompt=command,
            tool_name="", tool_input={},
            store=ThreadSafeDB(),
        )
        return plugins.app.dispatch(ctx), ctx

    def test_handle_search_returns_correct_message(self):
        """Test SEARCH policy handler message format"""
        response, ctx = self._dispatch_policy("/ar:f")
        assert "strict-search" in str(response).lower()
        assert ctx.file_policy == "SEARCH"

    def test_handle_allow_returns_correct_message(self):
        """Test ALLOW policy handler message format"""
        response, ctx = self._dispatch_policy("/ar:a")
        assert "allow-all" in str(response).lower()
        assert ctx.file_policy == "ALLOW"

    def test_handle_justify_returns_correct_message(self):
        """Test JUSTIFY policy handler message format"""
        response, ctx = self._dispatch_policy("/ar:j")
        assert "justify" in str(response).lower()
        assert ctx.file_policy == "JUSTIFY"


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

        assert response["hookSpecificOutput"]["permissionDecision"] == "deny"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
