#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Integration tests for clautorun hook functionality
"""
import pytest
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch
from io import StringIO

# Add src directory to Python path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from clautorun.main import main
from clautorun import build_hook_response


class TestHookIntegration:
    """Test hook integration with Claude Code"""

    @pytest.mark.hook
    @pytest.mark.integration
    def test_hook_handles_policy_commands(self, hook_input_data, capsys):
        """Test hook correctly handles file policy commands"""
        # Prepare input with policy command
        hook_input_data["prompt"] = "/afa"
        input_json = json.dumps(hook_input_data)

        # Mock session state and stdin
        mock_state = {}
        with patch('clautorun.main.session_state') as mock_session:
            mock_session.return_value.__enter__.return_value = mock_state
            mock_session.return_value.__exit__.return_value = None

            with patch('sys.stdin', StringIO(input_json)):
                main()

        # Check output
        captured = capsys.readouterr()
        output = json.loads(captured.out)

        assert output["continue"] is True, "Policy command should continue to AI (autorun5.py behavior)"
        assert "allow-all" in output["systemMessage"], "Response should contain policy info"

    @pytest.mark.hook
    @pytest.mark.integration
    def test_hook_handles_normal_commands(self, hook_input_data, capsys):
        """Test hook allows normal commands to continue to AI"""
        # Prepare input with normal command
        hook_input_data["prompt"] = "explain this python code"
        input_json = json.dumps(hook_input_data)

        # Mock stdin and run hook
        with patch('sys.stdin', StringIO(input_json)):
            main()

        # Check output
        captured = capsys.readouterr()
        output = json.loads(captured.out)

        assert output["continue"] is True, "Normal command should continue to AI"
        assert output["systemMessage"] == "", "Normal command should have no system message"

    @pytest.mark.hook
    @pytest.mark.integration
    def test_hook_response_format(self, hook_input_data, capsys):
        """Test hook response format matches Claude Code expectations"""
        hook_input_data["prompt"] = "/afs"
        input_json = json.dumps(hook_input_data)

        with patch('sys.stdin', StringIO(input_json)):
            main()

        captured = capsys.readouterr()
        output = json.loads(captured.out)

        # Check required Claude Code hook response fields
        required_fields = ["continue", "stopReason", "suppressOutput", "systemMessage"]
        for field in required_fields:
            assert field in output, f"Hook response should have '{field}' field"

        # Check field types
        assert isinstance(output["continue"], bool)
        assert isinstance(output["stopReason"], str)
        assert isinstance(output["suppressOutput"], bool)
        assert isinstance(output["systemMessage"], str)

    @pytest.mark.hook
    @pytest.mark.integration
    def test_hook_handles_all_commands(self, capsys):
        """Test hook handles all command types correctly"""
        test_cases = [
            ("/afs", True, "strict-search"),         # Policy commands continue to AI
            ("/afa", True, "allow-all"),
            ("/afj", True, "justify-create"),
            ("/afst", True, "Current policy"),
            ("/autostop", False, "Autorun stopped"),  # Stop commands do NOT continue to AI
            ("/estop", False, "Emergency stop activated"),
            ("normal command", True, ""),
            ("help me please", True, ""),
            ("what is this", True, "")
        ]

        for prompt, should_continue, expected_content in test_cases:
            input_data = {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "test_session",
                "prompt": prompt,
                "session_transcript": []
            }
            input_json = json.dumps(input_data)

            # Mock session state for each test case
            mock_state = {}
            with patch('clautorun.main.session_state') as mock_session:
                mock_session.return_value.__enter__.return_value = mock_state
                mock_session.return_value.__exit__.return_value = None

                with patch('sys.stdin', StringIO(input_json)):
                    main()

                captured = capsys.readouterr()
                output = json.loads(captured.out)

                assert output["continue"] == should_continue, f"Command '{prompt}' continue flag incorrect"
                if expected_content:
                    assert expected_content in output["systemMessage"], f"Command '{prompt}' response incorrect"

    @pytest.mark.hook
    @pytest.mark.integration
    def test_hook_maintains_session_state(self, capsys, mock_session_state):
        """Test hook maintains session state across commands"""
        session_id = "hook_test_session"

        # Mock session state that persists across commands
        mock_state = {}

        with patch('clautorun.main.session_state') as mock_session:
            mock_session.return_value.__enter__.return_value = mock_state
            mock_session.return_value.__exit__.return_value = None

            # First command - set policy
            input_data = {
                "hook_event_name": "UserPromptSubmit",
                "session_id": session_id,
                "prompt": "/afa",
                "session_transcript": []
            }
            input_json = json.dumps(input_data)

            with patch('sys.stdin', StringIO(input_json)):
                main()

            # Second command - check status
            input_data["prompt"] = "/afst"
            input_json = json.dumps(input_data)

            with patch('sys.stdin', StringIO(input_json)):
                main()

            captured = capsys.readouterr()
            lines = captured.out.strip().split('\n')
            second_output = json.loads(lines[-1])

            assert "allow-all" in second_output["systemMessage"], "Status should reflect previously set policy"

    @pytest.mark.hook
    @pytest.mark.integration
    def test_hook_handles_autorun_command(self, capsys):
        """Test hook handles autorun activation command"""
        input_data = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "test_session",
            "prompt": "/autorun create a new python module",
            "session_transcript": []
        }
        input_json = json.dumps(input_data)

        # Mock session state
        mock_state = {}
        with patch('clautorun.main.session_state') as mock_session:
            mock_session.return_value.__enter__.return_value = mock_state
            mock_session.return_value.__exit__.return_value = None

            with patch('sys.stdin', StringIO(input_json)):
                main()

            captured = capsys.readouterr()
            output = json.loads(captured.out)

            assert output["continue"] is False, "Autorun command should not continue to AI (injection template is complete)"
            assert "UNINTERRUPTED" in output["systemMessage"], "Response should contain injection template"


class TestHookErrorHandling:
    """Test hook error handling and edge cases"""

    @pytest.mark.hook
    def test_hook_handles_invalid_json(self, capsys):
        """Test hook handles invalid JSON input gracefully"""
        invalid_json = "not valid json {"

        with patch('sys.stdin', StringIO(invalid_json)):
            main()

        captured = capsys.readouterr()
        output = json.loads(captured.out)

        # Should handle error gracefully and return default response
        assert output["continue"] is True
        assert output["systemMessage"] == ""

    @pytest.mark.hook
    def test_hook_handles_missing_hook_event(self, capsys):
        """Test hook handles missing hook_event_name gracefully"""
        input_data = {
            # Missing "hook_event_name" field
            "session_id": "test_session",
            "prompt": "/afs",
            "session_transcript": []
        }
        input_json = json.dumps(input_data)

        with patch('sys.stdin', StringIO(input_json)):
            main()

        captured = capsys.readouterr()
        output = json.loads(captured.out)

        # Should handle gracefully and provide default response
        assert isinstance(output, dict), "Should return dict response"

    @pytest.mark.hook
    def test_hook_handles_empty_input(self, capsys):
        """Test hook handles empty input gracefully"""
        with patch('sys.stdin', StringIO("")):
            main()

        captured = capsys.readouterr()
        # Should not crash and produce some output
        assert captured.out != ""

    @pytest.mark.hook
    def test_hook_handles_different_hook_events(self, capsys):
        """Test hook handles different hook events"""
        hook_events = [
            "UserPromptSubmit",
            "PreToolUse",
            "PostToolUse",
            "Stop",
            "UnknownEvent"
        ]

        for event in hook_events:
            input_data = {
                "hook_event_name": event,
                "session_id": "test_session",
                "prompt": "/afs",
                "session_transcript": []
            }
            input_json = json.dumps(input_data)

            with patch('sys.stdin', StringIO(input_json)):
                main()

            captured = capsys.readouterr()
            output = json.loads(captured.out)

            # Should handle all events without crashing
            assert isinstance(output, dict), f"Event {event} should return dict response"


class TestHookResponseBuilder:
    """Test hook response builder function"""

    @pytest.mark.hook
    def test_build_hook_response_defaults(self):
        """Test build_hook_response with default values"""
        response = build_hook_response()

        assert response["continue"] is True
        assert response["stopReason"] == ""
        assert response["suppressOutput"] is False
        assert response["systemMessage"] == ""

    @pytest.mark.hook
    def test_build_hook_response_custom_values(self):
        """Test build_hook_response with custom values"""
        response = build_hook_response(
            continue_execution=False,
            stop_reason="test reason",
            system_message="test message"
        )

        assert response["continue"] is False
        assert response["stopReason"] == "test reason"
        assert response["suppressOutput"] is False  # Always False in current implementation
        assert response["systemMessage"] == "test message"

    @pytest.mark.hook
    def test_build_hook_response_partial_values(self):
        """Test build_hook_response with partial custom values"""
        response = build_hook_response(
            continue_execution=False,
            system_message="only custom message"
        )

        assert response["continue"] is False
        assert response["stopReason"] == ""  # Default
        assert response["suppressOutput"] is False  # Default
        assert response["systemMessage"] == "only custom message"

    @pytest.mark.hook
    def test_build_hook_response_types(self):
        """Test build_hook_response returns correct types"""
        response = build_hook_response()

        assert isinstance(response["continue"], bool)
        assert isinstance(response["stopReason"], str)
        assert isinstance(response["suppressOutput"], bool)
        assert isinstance(response["systemMessage"], str)


class TestHookPerformance:
    """Test hook performance characteristics"""

    @pytest.mark.hook
    @pytest.mark.integration
    def test_hook_response_speed(self, hook_input_data, capsys):
        """Test hook responds quickly (performance test)"""
        import time

        input_json = json.dumps(hook_input_data)

        # Measure response time
        start_time = time.time()
        with patch('sys.stdin', StringIO(input_json)):
            main()
        end_time = time.time()

        response_time = end_time - start_time

        # Should respond in reasonable time (< 1 second)
        assert response_time < 1.0, f"Hook response took {response_time:.3f}s, should be < 1.0s"

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert isinstance(output, dict), "Should return valid response even in performance test"

    @pytest.mark.hook
    @pytest.mark.integration
    def test_hook_handles_multiple_commands_quickly(self, capsys):
        """Test hook handles multiple commands quickly"""
        commands = [
            "/afs", "/afa", "/afj", "/afst", "/autostop", "/estop",
            "normal command 1", "normal command 2"
        ]

        total_start = time.time()
        responses = []

        for command in commands:
            input_data = {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "test_session",
                "prompt": command,
                "session_transcript": []
            }
            input_json = json.dumps(input_data)

            with patch('sys.stdin', StringIO(input_json)):
                main()

            captured = capsys.readouterr()
            responses.append(json.loads(captured.out))

        total_time = time.time() - total_start

        # Should handle all commands quickly (< 2 seconds total)
        assert total_time < 2.0, f"Multiple commands took {total_time:.3f}s, should be < 2.0s"
        assert len(responses) == len(commands), "Should have response for each command"