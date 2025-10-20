#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Simplified unit tests for clautorun core functionality
"""
import pytest
import sys
from pathlib import Path

# Add src directory to Python path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

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
            "SEARCH", "ALLOW", "JUSTIFY", "STATUS", "stop", "emergency_stop", "activate"
        ]

        for handler in required_handlers:
            assert handler in COMMAND_HANDLERS, f"Missing handler: {handler}"
            assert callable(COMMAND_HANDLERS[handler]), f"Handler {handler} should be callable"

    @pytest.mark.unit
    def test_policy_handlers_return_strings(self):
        """Test policy handlers return correct string responses"""
        # Test each policy handler returns a string
        for policy in ["SEARCH", "ALLOW", "JUSTIFY"]:
            handler = COMMAND_HANDLERS[policy]
            result = handler({})
            assert isinstance(result, str), f"Handler {policy} should return string"
            assert len(result) > 0, f"Handler {policy} should return non-empty string"

    @pytest.mark.unit
    def test_status_handler(self):
        """Test status handler works without session state"""
        handler = COMMAND_HANDLERS["STATUS"]
        result = handler({})
        assert isinstance(result, str), "STATUS handler should return string"
        assert len(result) > 0, "STATUS handler should return non-empty string"

    @pytest.mark.unit
    def test_stop_handlers(self):
        """Test stop handlers return correct responses"""
        for handler_key in ["stop", "emergency_stop"]:
            handler = COMMAND_HANDLERS[handler_key]
            result = handler({})
            assert isinstance(result, str), f"{handler_key} handler should return string"
            assert len(result) > 0, f"{handler_key} handler should return non-empty string"

    @pytest.mark.unit
    def test_activate_handler_returns_injection_template(self):
        """Test activate handler returns injection template content"""
        handler = COMMAND_HANDLERS["activate"]
        result = handler({}, "/autorun test task")

        assert isinstance(result, str), "activate handler should return string"
        assert "UNINTERRUPTED" in result, "Response should contain injection template"
        assert "AUTONOMOUS" in result, "Response should contain injection template"


class TestCommandDetection:
    """Test command detection logic"""

    @pytest.mark.unit
    def test_policy_commands_detected(self):
        """Test policy commands are detected correctly"""
        mappings = CONFIG["command_mappings"]
        policy_commands = ["/afs", "/afa", "/afj", "/afst"]

        for cmd in policy_commands:
            found = next((v for k, v in mappings.items() if k == cmd), None)
            assert found is not None, f"Command {cmd} should be detected"
            assert found in COMMAND_HANDLERS, f"Command {cmd} should have handler"

    @pytest.mark.unit
    def test_control_commands_detected(self):
        """Test control commands are detected correctly"""
        mappings = CONFIG["command_mappings"]
        control_commands = ["/autostop", "/estop"]

        for cmd in control_commands:
            found = next((v for k, v in mappings.items() if k == cmd), None)
            assert found is not None, f"Command {cmd} should be detected"
            assert found in ["stop", "emergency_stop"], f"Command {cmd} should map to stop or emergency_stop"

    @pytest.mark.unit
    def test_normal_commands_not_detected(self):
        """Test normal commands are not detected as autorun commands"""
        mappings = CONFIG["command_mappings"]
        normal_commands = ["help me", "what is this", "test file"]

        for cmd in normal_commands:
            found = next((v for k, v in mappings.items() if k == cmd), None)
            assert found is None, f"Normal command '{cmd}' should not be detected as autorun command"

    @pytest.mark.unit
    def test_autorun_command_detection(self):
        """Test autorun command detection"""
        mappings = CONFIG["command_mappings"]
        autorun_cmd = "/autorun test task description"

        found = next((v for k, v in mappings.items() if autorun_cmd.startswith(k)), None)
        assert found == "activate", f"Autorun command should be detected as 'activate'"
        assert "activate" in COMMAND_HANDLERS, f"Activate handler should exist"


class TestBasicFunctionality:
    """Test basic functionality without session state"""

    @pytest.mark.unit
    def test_configuration_constants_are_strings(self):
        """Test configuration constants are strings"""
        for key in ["completion_marker", "emergency_stop_phrase"]:
            assert key in CONFIG
            assert isinstance(CONFIG[key], str)
            assert len(CONFIG[key]) > 0

    @pytest.mark.unit
    def test_policies_have_correct_structure(self):
        """Test policies have correct tuple structure"""
        policies = CONFIG["policies"]
        for policy_name, policy_data in policies.items():
            assert isinstance(policy_data, tuple), f"Policy {policy_name} should be tuple"
            assert len(policy_data) == 2, f"Policy {policy_name} should have 2 elements"
            assert isinstance(policy_data[0], str), f"Policy {policy_name} first element should be string"
            assert isinstance(policy_data[1], str), f"Policy {policy_name} second element should be string"

    @pytest.mark.unit
    def test_command_handlers_are_callable(self):
        """Test all command handlers are callable"""
        for handler_name, handler_func in COMMAND_HANDLERS.items():
            assert callable(handler_func), f"Handler {handler_name} should be callable"

    @pytest.mark.unit
    def test_command_handlers_accept_state_argument(self):
        """Test command handlers accept state argument"""
        # Test each handler with empty state dict
        for handler_name, handler_func in COMMAND_HANDLERS.items():
            try:
                # All handlers should accept at least one argument
                result = handler_func({})
                assert result is not None, f"Handler {handler_name} should return something"
            except TypeError as e:
                if "takes" in str(e) and "positional argument" in str(e):
                    pytest.fail(f"Handler {handler_name} should accept at least one argument")
                else:
                    raise