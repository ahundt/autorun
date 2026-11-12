#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unit tests for autorun core functionality
"""
import uuid
import pytest
from unittest.mock import patch
from autorun import CONFIG
from autorun.core import EventContext, ThreadSafeDB
from autorun import plugins

# COMMAND_HANDLERS removed — canonical path: EventContext + plugins.app.dispatch(ctx)
# Commands: /ar:file allow|justify|existing-only, /ar:status, /ar:stop,
#           /ar:estop, /ar:go <task>. Old names (/ar:f, /ar:st, /ar:x, /ar:sos)
#           still dispatch through core._RETIRED_COMMAND_SPELLINGS.


def _dispatch(prompt: str, session_id: str = None) -> dict:
    """Canonical dispatch via daemon-path. Replaces COMMAND_HANDLERS[cmd](state)."""
    sid = session_id or f"test-unit-{uuid.uuid4().hex[:8]}"
    ctx = EventContext(
        session_id=sid,
        event="UserPromptSubmit",
        prompt=prompt,
        tool_name="",
        tool_input={},
        store=ThreadSafeDB(),
    )
    return plugins.app.dispatch(ctx)


class TestConfiguration:
    """Test configuration constants and mappings"""

    @pytest.mark.unit
    def test_three_stage_confirmations(self):
        """Test three-stage confirmation markers are present and correct"""
        # Stage 1
        assert "stage1_instruction" in CONFIG
        assert "stage1_message" in CONFIG
        assert isinstance(CONFIG["stage1_message"], str)
        assert len(CONFIG["stage1_message"]) > 0

        # Stage 2
        assert "stage2_instruction" in CONFIG
        assert "stage2_message" in CONFIG
        assert isinstance(CONFIG["stage2_message"], str)
        assert len(CONFIG["stage2_message"]) > 0

        # Stage 3
        assert "stage3_instruction" in CONFIG
        assert "stage3_message" in CONFIG
        assert isinstance(CONFIG["stage3_message"], str)
        assert len(CONFIG["stage3_message"]) > 0

    @pytest.mark.unit
    def test_emergency_stop(self):
        """Test emergency stop is present"""
        assert "emergency_stop" in CONFIG
        assert isinstance(CONFIG["emergency_stop"], str)
        assert len(CONFIG["emergency_stop"]) > 0

    @pytest.mark.unit
    def test_policies_configuration(self):
        """Test file policies are properly configured"""
        assert "policies" in CONFIG
        policies = CONFIG["policies"]

        # Check required policies exist
        required_policies = ["ALLOW", "JUSTIFY", "SEARCH"]
        for policy in required_policies:
            assert policy in policies, f"Missing policy: {policy}"
            assert isinstance(policies[policy], tuple), f"Policy {policy} should be a tuple"
            assert len(policies[policy]) == 2, f"Policy {policy} should have 2 elements"

    @pytest.mark.unit
    def test_command_mappings(self):
        """Test command mappings are properly configured"""
        assert "command_mappings" in CONFIG
        mappings = CONFIG["command_mappings"]

        # Check essential commands
        essential_commands = ["/ar:status", "/ar:go", "/ar:stop", "/ar:estop"]
        for cmd in essential_commands:
            assert cmd in mappings, f"Missing command mapping: {cmd}"
            assert mappings[cmd], f"Command {cmd} should map to an action"

    @pytest.mark.unit
    def test_injection_template_present(self):
        """Test injection template is present and contains three-stage placeholders"""
        assert "injection_template" in CONFIG
        template = CONFIG["injection_template"]

        # Check for required three-stage placeholders
        required_placeholders = [
            "{emergency_stop}",
            "{stage1_instruction}",
            "{stage1_message}",
            "{stage2_instruction}",
            "{stage2_message}",
            "{stage3_instruction}",
            "{stage3_message}",
            "{policy_instructions}"
        ]

        for placeholder in required_placeholders:
            assert placeholder in template, f"Missing placeholder: {placeholder}"


class TestCommandHandlers:
    """Test command handler functions.

    Migrated from COMMAND_HANDLERS[cmd](state) to canonical daemon-path:
    EventContext + plugins.app.dispatch(ctx)
    """

    @pytest.mark.unit
    def test_command_handlers_exist(self):
        """Test all required command handlers exist in plugins.app.command_handlers."""
        required_aliases = {
            "file policy": "/ar:file",
            "STATUS": "/ar:status",
            "STOP": "/ar:stop",
            "EMERGENCY_STOP": "/ar:estop",
            "activate": "/ar:go",
        }
        handlers = plugins.app.command_handlers
        for logical_name, canonical_alias in required_aliases.items():
            assert canonical_alias in handlers, \
                f"Missing handler for {logical_name}: {canonical_alias} not in app.command_handlers"
            assert callable(handlers[canonical_alias]), \
                f"Handler for {logical_name} ({canonical_alias}) should be callable"

    @pytest.mark.unit
    def test_policy_handlers_update_state(self, mock_session_state):
        """Test policy handlers update session state correctly via canonical dispatch."""
        session_id = f"unit-policy-{uuid.uuid4().hex[:8]}"

        # Test SEARCH handler (canonical: /ar:f)
        result = _dispatch("/ar:f", session_id)
        assert "strict-search" in result["systemMessage"].lower()
        assert result["continue"] is True

        # Test ALLOW handler (canonical: /ar:a)
        result = _dispatch("/ar:a", session_id)
        assert "allow-all" in result["systemMessage"].lower()
        assert result["continue"] is True

        # Test JUSTIFY handler (canonical: /ar:j)
        result = _dispatch("/ar:j", session_id)
        assert "justify" in result["systemMessage"].lower()
        assert result["continue"] is True

    @pytest.mark.unit
    def test_status_handler_reports_current_state(self, mock_session_state):
        """Test status handler reports current policy correctly via canonical dispatch."""
        session_id = f"unit-status-{uuid.uuid4().hex[:8]}"

        # Set ALLOW policy first
        _dispatch("/ar:a", session_id)

        # Test status handler (canonical: /ar:st)
        result = _dispatch("/ar:st", session_id)
        assert "allow-all" in result["systemMessage"].lower()

    @pytest.mark.unit
    def test_stop_handlers(self, mock_session_state):
        """Test stop handlers produce correct responses via canonical dispatch."""
        # Test STOP handler (canonical: /ar:x)
        result = _dispatch("/ar:x")
        assert "Stopped" in result["systemMessage"]
        assert result["continue"] is False

        # Test EMERGENCY_STOP handler (canonical: /ar:sos)
        result = _dispatch("/ar:sos")
        assert "EMERGENCY STOP" in result["systemMessage"]
        assert result["continue"] is False

    @pytest.mark.unit
    def test_activate_handler(self, mock_session_state):
        """Test activate handler returns injection template via canonical dispatch."""
        result = _dispatch("/ar:go test task")

        # Should return injection template
        assert "UNINTERRUPTED" in result["systemMessage"] or "Autorun" in result["systemMessage"]
        assert result["continue"] is True


class TestCommandDetection:
    """Test command detection logic.

    COMMAND_HANDLERS removed — canonical path: EventContext + plugins.app.dispatch(ctx).
    Logical names (SEARCH/ALLOW/activate/etc.) from command_mappings values map to
    canonical aliases in plugins.app.command_handlers; detection is
    plugins.app._find_command, the lookup dispatch uses.
    """

    @pytest.mark.unit
    def test_command_mapping_detection(self, sample_commands):
        """Policy and control commands are found by the dispatcher's lookup."""
        for cmd in sample_commands["policy_commands"] + sample_commands["control_commands"]:
            match = plugins.app._find_command(cmd)
            assert match is not None, f"Command {cmd} should be detected"
            assert callable(match.handler), f"Command {cmd} should reach a callable handler"

    @pytest.mark.unit
    def test_normal_commands_not_detected(self, sample_commands):
        """Test normal commands are not detected as autorun commands"""
        mappings = CONFIG["command_mappings"]

        for cmd in sample_commands["normal_commands"]:
            found = next((v for k, v in mappings.items() if k == cmd), None)
            assert found is None, f"Normal command '{cmd}' should not be detected as autorun command"

    @pytest.mark.unit
    def test_autorun_command_detection(self, sample_commands):
        """Test autorun command detection"""
        match = plugins.app._find_command(sample_commands["autorun_command"])
        assert match is not None and match.handler is plugins.handle_activate, \
            "Autorun command should reach the activate handler"
        assert match.arguments == "test task description"
