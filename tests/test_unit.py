#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unit tests for clautorun core functionality
"""
import pytest
from clautorun.main import CONFIG, COMMAND_HANDLERS, session_state


class TestConfiguration:
    """Test configuration constants and mappings"""

    @pytest.mark.unit
    def test_completion_marker(self):
        """Test completion marker is present and correct"""
        assert "completion_marker" in CONFIG
        assert isinstance(CONFIG["completion_marker"], str)
        assert len(CONFIG["completion_marker"]) > 0

    @pytest.mark.unit
    def test_emergency_stop_phrase(self):
        """Test emergency stop phrase is present"""
        assert "emergency_stop_phrase" in CONFIG
        assert isinstance(CONFIG["emergency_stop_phrase"], str)
        assert len(CONFIG["emergency_stop_phrase"]) > 0

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
        essential_commands = ["/afs", "/afa", "/afj", "/afst", "/autostop ", "/estop "]
        for cmd in essential_commands:
            assert cmd in mappings, f"Missing command mapping: {cmd}"
            assert mappings[cmd], f"Command {cmd} should map to an action"

    @pytest.mark.unit
    def test_injection_template_present(self):
        """Test injection template is present and contains placeholders"""
        assert "injection_template" in CONFIG
        template = CONFIG["injection_template"]

        # Check for required placeholders
        required_placeholders = [
            "{emergency_stop_phrase}",
            "{completion_marker}",
            "{policy_instructions}"
        ]

        for placeholder in required_placeholders:
            assert placeholder in template, f"Missing placeholder: {placeholder}"

    @pytest.mark.unit
    def test_recheck_template_present(self):
        """Test recheck template is present"""
        assert "recheck_template" in CONFIG
        template = CONFIG["recheck_template"]

        # Check for required placeholders
        required_placeholders = [
            "{activation_prompt}",
            "{completion_marker}",
            "{recheck_count}",
            "{max_recheck_count}"
        ]

        for placeholder in required_placeholders:
            assert placeholder in template, f"Missing placeholder: {placeholder}"


class TestCommandHandlers:
    """Test command handler functions"""

    @pytest.mark.unit
    def test_command_handlers_exist(self):
        """Test all required command handlers exist"""
        required_handlers = [
            "SEARCH", "ALLOW", "JUSTIFY", "STATUS", "STOP", "EMERGENCY_STOP", "activate"
        ]

        for handler in required_handlers:
            assert handler in COMMAND_HANDLERS, f"Missing handler: {handler}"
            assert callable(COMMAND_HANDLERS[handler]), f"Handler {handler} should be callable"

    @pytest.mark.unit
    def test_policy_handlers_update_state(self, mock_session_state):
        """Test policy handlers update session state correctly"""
        session_id = "test_session"

        with session_state(session_id) as state:
            # Test SEARCH handler
            response = COMMAND_HANDLERS["SEARCH"](state)
            assert "strict-search" in response.lower()
            assert state["file_policy"] == "SEARCH"

            # Test ALLOW handler
            response = COMMAND_HANDLERS["ALLOW"](state)
            assert "allow-all" in response.lower()
            assert state["file_policy"] == "ALLOW"

            # Test JUSTIFY handler
            response = COMMAND_HANDLERS["JUSTIFY"](state)
            assert "justify" in response.lower()
            assert state["file_policy"] == "JUSTIFY"

    @pytest.mark.unit
    def test_status_handler_reports_current_state(self, mock_session_state):
        """Test status handler reports current policy correctly"""
        session_id = "test_session"

        with session_state(session_id) as state:
            # Set initial policy
            state["file_policy"] = "ALLOW"

            # Test status handler
            response = COMMAND_HANDLERS["STATUS"](state)
            assert "allow-all" in response.lower()

    @pytest.mark.unit
    def test_stop_handlers(self, mock_session_state):
        """Test stop handlers update session status"""
        session_id = "test_session"

        with session_state(session_id) as state:
            # Test STOP handler
            response = COMMAND_HANDLERS["STOP"](state)
            assert response == "Autorun stopped"
            assert state["session_status"] == "stopped"

            # Test EMERGENCY_STOP handler
            response = COMMAND_HANDLERS["EMERGENCY_STOP"](state)
            assert response == "Emergency stop activated"
            assert state["session_status"] == "emergency_stopped"

    @pytest.mark.unit
    def test_activate_handler(self, mock_session_state):
        """Test activate handler returns injection template"""
        session_id = "test_session"
        test_prompt = "/autorun test task"

        with session_state(session_id) as state:
            response = COMMAND_HANDLERS["activate"](state, test_prompt)

            # Should return injection template
            assert "UNINTERRUPTED" in response
            assert "AUTONOMOUS" in response
            assert state["session_status"] == "active"
            assert state["autorun_stage"] == "INITIAL"
            assert state["activation_prompt"] == test_prompt


class TestSessionState:
    """Test session state management - simplified tests without complex fixtures"""

    @pytest.mark.unit
    def test_session_state_basic_functionality(self):
        """Test basic session state functionality"""
        session_id = "test_basic_session"

        # Test that session state context manager works
        with session_state(session_id) as state:
            # Should be able to set and get values
            state["test_key"] = "test_value"
            assert state.get("test_key") == "test_value"

            # Should be able to check existence
            assert "test_key" in state
            assert "nonexistent_key" not in state

        # Session should close without errors
        assert True  # If we reach here, the context manager worked

    @pytest.mark.unit
    def test_multiple_sessions_basic(self):
        """Test basic multiple session functionality"""
        session_id_1 = "session_one"
        session_id_2 = "session_two"

        # Test that different session IDs don't interfere
        with session_state(session_id_1) as state1:
            state1["data"] = "from_session_one"

        with session_state(session_id_2) as state2:
            state2["data"] = "from_session_two"
            # Should only see data from session two
            assert state2.get("data") == "from_session_two"


class TestCommandDetection:
    """Test command detection logic"""

    @pytest.mark.unit
    def test_command_mapping_detection(self, sample_commands):
        """Test command detection works correctly"""
        mappings = CONFIG["command_mappings"]

        # Test policy commands
        for cmd in sample_commands["policy_commands"]:
            found = next((v for k, v in mappings.items() if k == cmd), None)
            assert found is not None, f"Command {cmd} should be detected"
            assert found in COMMAND_HANDLERS, f"Command {cmd} should have handler"

        # Test control commands (note: some have trailing spaces in mapping)
        for cmd in sample_commands["control_commands"]:
            found = next((v for k, v in mappings.items() if k == cmd), None)
            if found is None:
                # Try with trailing space
                found = next((v for k, v in mappings.items() if k == f"{cmd} "), None)
            assert found is not None, f"Command {cmd} should be detected (with or without trailing space)"
            assert found in COMMAND_HANDLERS, f"Command {cmd} should have handler"

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
        mappings = CONFIG["command_mappings"]

        cmd = sample_commands["autorun_command"]
        found = next((v for k, v in mappings.items() if cmd.startswith(k)), None)
        assert found == "activate", f"Autorun command should be detected as 'activate'"
        assert "activate" in COMMAND_HANDLERS, f"Activate handler should exist"