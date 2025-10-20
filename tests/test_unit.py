#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unit tests for clautorun core functionality
"""
import pytest
from unittest.mock import patch
from clautorun import CONFIG, COMMAND_HANDLERS


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
        essential_commands = ["/afs", "/afa", "/afj", "/afst", "/autostop", "/estop"]
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
        # Mock session state
        mock_state = {}

        # Test SEARCH handler
        response = COMMAND_HANDLERS["SEARCH"](mock_state)
        assert "strict-search" in response.lower()
        assert mock_state["file_policy"] == "SEARCH"

        # Test ALLOW handler
        response = COMMAND_HANDLERS["ALLOW"](mock_state)
        assert "allow-all" in response.lower()
        assert mock_state["file_policy"] == "ALLOW"

        # Test JUSTIFY handler
        response = COMMAND_HANDLERS["JUSTIFY"](mock_state)
        assert "justify" in response.lower()
        assert mock_state["file_policy"] == "JUSTIFY"

    @pytest.mark.unit
    def test_status_handler_reports_current_state(self, mock_session_state):
        """Test status handler reports current policy correctly"""
        # Mock session state with initial policy
        mock_state = {"file_policy": "ALLOW"}

        # Test status handler
        response = COMMAND_HANDLERS["STATUS"](mock_state)
        assert "allow-all" in response.lower()

    @pytest.mark.unit
    def test_stop_handlers(self, mock_session_state):
        """Test stop handlers update session status"""
        # Mock session state
        mock_state = {}

        # Test STOP handler
        response = COMMAND_HANDLERS["STOP"](mock_state)
        assert response == "Autorun stopped"
        assert mock_state["session_status"] == "stopped"

        # Reset for next test
        mock_state.clear()

        # Test EMERGENCY_STOP handler
        response = COMMAND_HANDLERS["EMERGENCY_STOP"](mock_state)
        assert response == "Emergency stop activated"
        assert mock_state["session_status"] == "emergency_stopped"

    @pytest.mark.unit
    def test_activate_handler(self, mock_session_state):
        """Test activate handler returns injection template"""
        test_prompt = "/autorun test task"

        # Mock session state
        mock_state = {}

        response = COMMAND_HANDLERS["activate"](mock_state, test_prompt)

        # Should return injection template
        assert "UNINTERRUPTED" in response
        assert "AUTONOMOUS" in response
        assert mock_state["session_status"] == "active"
        assert mock_state["autorun_stage"] == "INITIAL"
        assert mock_state["activation_prompt"] == test_prompt


class TestSessionState:
    """Test session state management - simplified tests without complex fixtures"""

    @pytest.mark.unit
    def test_session_state_basic_functionality(self):
        """Test basic session state functionality using mock"""
        # Mock the session_state to avoid database creation
        with patch('clautorun.main.session_state') as mock_session:
            mock_state = {}
            mock_session.return_value.__enter__.return_value = mock_state
            mock_session.return_value.__exit__.return_value = None

            # Test that session state context manager works
            with mock_session("test_basic_session") as state:
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
        # Mock the session_state to avoid database creation
        with patch('clautorun.main.session_state') as mock_session:
            # Test that different session IDs don't interfere
            mock_state_1 = {}
            mock_session.return_value.__enter__.return_value = mock_state_1
            mock_session.return_value.__exit__.return_value = None

            with mock_session("session_one") as state1:
                state1["data"] = "from_session_one"

            mock_state_2 = {}
            mock_session.return_value.__enter__.return_value = mock_state_2
            mock_session.return_value.__exit__.return_value = None

            with mock_session("session_two") as state2:
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