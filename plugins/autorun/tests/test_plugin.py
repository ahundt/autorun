#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Integration tests for clautorun plugin functionality
"""
import pytest
import json
import sys
from pathlib import Path
from unittest.mock import patch
from io import StringIO

# Add src directory to Python path
src_path = str(Path(__file__).parent.parent / "src")
sys.path.insert(0, src_path)

# Also add clautorun package path
clautorun_path = str(Path(__file__).parent.parent / "src" / "clautorun")
if clautorun_path not in sys.path:
    sys.path.insert(0, clautorun_path)

import clautorun.main as plugin_module


class TestPluginIntegration:
    """Test plugin integration with Claude Code"""

    @pytest.mark.plugin
    @pytest.mark.integration
    def test_plugin_handles_policy_commands(self, plugin_input_data, capsys):
        """Test plugin correctly handles file policy commands"""
        # Prepare input with policy command - must include hook_event_name for dispatch
        plugin_input_data["prompt"] = "/afs"
        plugin_input_data["hook_event_name"] = "UserPromptSubmit"
        input_json = json.dumps(plugin_input_data)

        # Mock session state and stdin
        mock_state = {}
        with patch.object(plugin_module, 'session_state') as mock_session:
            mock_session.return_value.__enter__.return_value = mock_state
            mock_session.return_value.__exit__.return_value = None

            with patch('sys.stdin', StringIO(input_json)):
                plugin_module.main(_exit=False)

        # Check output - policy commands use systemMessage for response text
        # and continue=True (policy is set locally, AI continues)
        captured = capsys.readouterr()
        output = json.loads(captured.out)

        assert output["continue"] is True, "Policy command should continue to AI"
        assert "strict-search" in output["systemMessage"], "systemMessage should contain policy info"

    @pytest.mark.plugin
    @pytest.mark.integration
    def test_plugin_handles_normal_commands(self, plugin_input_data, capsys):
        """Test plugin allows normal commands to continue to AI"""
        # Prepare input with normal command - must include hook_event_name for dispatch
        plugin_input_data["prompt"] = "help me understand this code"
        plugin_input_data["hook_event_name"] = "UserPromptSubmit"
        input_json = json.dumps(plugin_input_data)

        # Mock stdin and run plugin
        with patch('sys.stdin', StringIO(input_json)):
            plugin_module.main(_exit=False)

        # Check output - actual format uses stopReason/systemMessage, not "response"
        captured = capsys.readouterr()
        output = json.loads(captured.out)

        assert output["continue"] is True, "Normal command should continue to AI"
        assert output["stopReason"] == "", "Normal command should have empty stopReason"
        assert output["systemMessage"] == "", "Normal command should have empty systemMessage"

    @pytest.mark.plugin
    @pytest.mark.integration
    def test_plugin_handles_all_policy_commands(self, capsys):
        """Test plugin handles all different policy commands"""
        policy_commands = [
            ("/afs", "strict-search"),
            ("/afa", "allow-all"),
            ("/afj", "justify-create"),
            ("/afst", "Current policy: allow-all")  # Fresh session defaults to ALLOW policy
        ]

        for i, (command, expected_content) in enumerate(policy_commands):
            # Use different session_id for each command to avoid state conflicts
            # Must include hook_event_name for UserPromptSubmit dispatch
            input_data = {
                "hook_event_name": "UserPromptSubmit",
                "prompt": command,
                "session_id": f"test_session_{i}",
                "session_transcript": []
            }
            input_json = json.dumps(input_data)

            # Mock session state for each command
            mock_state = {}
            with patch.object(plugin_module, 'session_state') as mock_session:
                mock_session.return_value.__enter__.return_value = mock_state
                mock_session.return_value.__exit__.return_value = None

                with patch('sys.stdin', StringIO(input_json)):
                    plugin_module.main(_exit=False)

                captured = capsys.readouterr()
                output = json.loads(captured.out)

                # Policy commands set continue=True (policy applied locally, AI continues)
                # Response text is in systemMessage, not "response"
                assert output["continue"] is True, f"{command} should continue to AI"
                assert expected_content in output["systemMessage"], f"{command} systemMessage should contain {expected_content}"

    @pytest.mark.plugin
    @pytest.mark.integration
    def test_plugin_handles_control_commands(self, capsys):
        """Test plugin handles control commands"""
        control_commands = [
            ("/autostop", "Autorun stopped"),
            ("/estop", "Emergency stop activated")
        ]

        for command, expected_response in control_commands:
            input_data = {
                "hook_event_name": "UserPromptSubmit",
                "prompt": command,
                "session_id": "test_session",
                "session_transcript": []
            }
            input_json = json.dumps(input_data)

            # Mock session state for each command
            mock_state = {}
            with patch.object(plugin_module, 'session_state') as mock_session:
                mock_session.return_value.__enter__.return_value = mock_state
                mock_session.return_value.__exit__.return_value = None

                with patch('sys.stdin', StringIO(input_json)):
                    plugin_module.main(_exit=False)

                captured = capsys.readouterr()
                output = json.loads(captured.out)

                # Stop commands set continue=False and put response in systemMessage
                assert output["continue"] is False, f"{command} should not continue to AI"
                assert expected_response in output["systemMessage"], f"{command} systemMessage should contain {expected_response}"

    @pytest.mark.plugin
    @pytest.mark.integration
    def test_plugin_handles_autorun_command(self, capsys):
        """Test plugin handles autorun activation command"""
        input_data = {
            "hook_event_name": "UserPromptSubmit",
            "prompt": "/autorun test task description",
            "session_id": "test_session",
            "session_transcript": []
        }
        input_json = json.dumps(input_data)

        # Mock session state
        mock_state = {"session_id": "test_session"}  # Required for activate handler
        with patch.object(plugin_module, 'session_state') as mock_session:
            mock_session.return_value.__enter__.return_value = mock_state
            mock_session.return_value.__exit__.return_value = None

            with patch('sys.stdin', StringIO(input_json)):
                plugin_module.main(_exit=False)

            captured = capsys.readouterr()
            output = json.loads(captured.out)

            # Autorun activation: continue=True so AI processes injection template as context
            assert output["continue"] is True, "Autorun command should continue to AI with injection template"
            assert "UNINTERRUPTED" in output["systemMessage"], "systemMessage should contain injection template"
            assert "AUTONOMOUS" in output["systemMessage"], "systemMessage should contain injection template"

    @pytest.mark.plugin
    @pytest.mark.integration
    def test_plugin_maintains_session_state(self, capsys, mock_session_state):
        """Test plugin maintains session state across commands"""
        session_id = "plugin_test_session"

        # Mock session state that persists across commands
        mock_state = {}

        with patch.object(plugin_module, 'session_state') as mock_session:
            mock_session.return_value.__enter__.return_value = mock_state
            mock_session.return_value.__exit__.return_value = None

            # First command - set policy (must include hook_event_name)
            input_data = {
                "hook_event_name": "UserPromptSubmit",
                "prompt": "/afs",
                "session_id": session_id,
                "session_transcript": []
            }
            input_json = json.dumps(input_data)

            with patch('sys.stdin', StringIO(input_json)):
                plugin_module.main(_exit=False)

            # Second command - check status
            input_data["prompt"] = "/afst"
            input_json = json.dumps(input_data)

            with patch('sys.stdin', StringIO(input_json)):
                plugin_module.main(_exit=False)

            captured = capsys.readouterr()
            lines = captured.out.strip().split('\n')
            second_output = json.loads(lines[-1])

            # Response text is in systemMessage, not "response"
            assert "strict-search" in second_output["systemMessage"], "Status should reflect previously set policy"

    @pytest.mark.plugin
    @pytest.mark.integration
    def test_plugin_handles_invalid_json(self, capsys):
        """Test plugin handles invalid JSON input gracefully"""
        invalid_json = "not valid json {"

        # main() prints a default hook response then calls sys.exit(1) on parse errors
        with patch('sys.stdin', StringIO(invalid_json)):
            with pytest.raises(SystemExit) as exc_info:
                plugin_module.main()

        assert exc_info.value.code == 1, "Should exit with code 1 on invalid JSON"

        captured = capsys.readouterr()
        output = json.loads(captured.out)

        # On error, main() outputs a default build_hook_response() with continue=True
        assert output["continue"] is True, "Should default to continue on error"
        assert output["stopReason"] == "", "stopReason should be empty on error fallback"

    @pytest.mark.plugin
    @pytest.mark.integration
    def test_plugin_handles_missing_fields(self, capsys):
        """Test plugin handles missing required fields gracefully"""
        # Input with missing prompt field - no hook_event_name so default_handler is used
        input_data = {
            "session_id": "test_session"
            # Missing "prompt" field
        }
        input_json = json.dumps(input_data)

        with patch('sys.stdin', StringIO(input_json)):
            plugin_module.main(_exit=False)

        captured = capsys.readouterr()
        output = json.loads(captured.out)

        # Should default to continuing to AI when prompt is missing
        # Actual output keys: continue, stopReason, suppressOutput, systemMessage
        assert output["continue"] is True
        assert output["stopReason"] == ""
        assert output["systemMessage"] == ""


class TestPluginJsonOutput:
    """Test plugin JSON output format"""

    @pytest.mark.plugin
    def test_output_format_is_valid_json(self, plugin_input_data, capsys):
        """Test plugin output is valid JSON"""
        input_json = json.dumps(plugin_input_data)

        with patch('sys.stdin', StringIO(input_json)):
            plugin_module.main(_exit=False)

        captured = capsys.readouterr()

        # Should be able to parse output as JSON without errors
        try:
            json.loads(captured.out)
        except json.JSONDecodeError:
            pytest.fail("Plugin output should be valid JSON")

    @pytest.mark.plugin
    def test_output_has_required_fields(self, plugin_input_data, capsys):
        """Test plugin output has required fields"""
        input_json = json.dumps(plugin_input_data)

        with patch('sys.stdin', StringIO(input_json)):
            plugin_module.main(_exit=False)

        captured = capsys.readouterr()
        output = json.loads(captured.out)

        # Check required fields exist - actual format uses stopReason/systemMessage
        required_fields = ["continue", "stopReason", "suppressOutput", "systemMessage"]
        for field in required_fields:
            assert field in output, f"Output should have '{field}' field"

    @pytest.mark.plugin
    def test_output_types_are_correct(self, plugin_input_data, capsys):
        """Test plugin output fields have correct types"""
        input_json = json.dumps(plugin_input_data)

        with patch('sys.stdin', StringIO(input_json)):
            plugin_module.main(_exit=False)

        captured = capsys.readouterr()
        output = json.loads(captured.out)

        assert isinstance(output["continue"], bool), "continue field should be boolean"
        assert isinstance(output["stopReason"], str), "stopReason field should be string"
        assert isinstance(output["suppressOutput"], bool), "suppressOutput field should be boolean"
        assert isinstance(output["systemMessage"], str), "systemMessage field should be string"


class TestPluginErrorHandling:
    """Test plugin error handling and edge cases"""

    @pytest.mark.plugin
    def test_plugin_handles_empty_input(self, capsys):
        """Test plugin handles empty input gracefully"""
        # main() prints a default hook response then calls sys.exit(1) on empty input
        with patch('sys.stdin', StringIO("")):
            with pytest.raises(SystemExit) as exc_info:
                plugin_module.main()

        assert exc_info.value.code == 1, "Should exit with code 1 on empty input"

        captured = capsys.readouterr()
        # Should not crash and produce some output (default hook response)
        assert captured.out != ""
        output = json.loads(captured.out)
        assert output["continue"] is True

    @pytest.mark.plugin
    def test_plugin_handles_unicode_characters(self, capsys):
        """Test plugin handles unicode characters in prompts"""
        input_data = {
            "prompt": "help with café résumé 📝",
            "session_id": "test_session",
            "session_transcript": []
        }
        input_json = json.dumps(input_data, ensure_ascii=False)

        with patch('sys.stdin', StringIO(input_json)):
            plugin_module.main(_exit=False)

        captured = capsys.readouterr()
        output = json.loads(captured.out)

        # Should handle unicode without errors
        assert output["continue"] is True  # Normal command should continue

    @pytest.mark.plugin
    def test_plugin_handles_long_prompts(self, capsys):
        """Test plugin handles very long prompts"""
        long_prompt = "test " * 1000  # Create a very long prompt
        input_data = {
            "prompt": long_prompt,
            "session_id": "test_session",
            "session_transcript": []
        }
        input_json = json.dumps(input_data)

        with patch('sys.stdin', StringIO(input_json)):
            plugin_module.main(_exit=False)

        captured = capsys.readouterr()
        output = json.loads(captured.out)

        # Should handle long prompts without issues
        assert output["continue"] is True