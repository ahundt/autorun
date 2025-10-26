#!/usr/bin/env python3
"""Unit tests for tmux injector system"""

import pytest
import tempfile
import subprocess
import time
from pathlib import Path
import sys
import os
from unittest.mock import Mock, patch, MagicMock

# Add src directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from clautorun.tmux_injector import TmuxInjector, DualChannelInjector


class TestTmuxInjector:
    """Test suite for tmux injector functionality"""

    def setup_method(self):
        """Set up test environment"""
        self.injector = TmuxInjector("test_session")

    def test_tmux_injector_initialization(self):
        """Test tmux injector initialization"""
        injector = TmuxInjector("custom_session")
        assert injector.session_id == "custom_session"
        assert injector.tmux_session is None
        assert injector.tmux_window is None
        assert injector.tmux_pane is None

    @patch('subprocess.run')
    def test_detect_tmux_environment_success(self, mock_run):
        """Test successful tmux environment detection"""
        # Mock successful tmux detection
        mock_run.side_effect = [
            Mock(returncode=0, stdout="session0:0.0"),
            Mock(returncode=0, stdout="session0")
        ]

        injector = TmuxInjector("test_session")
        result = injector.detect_tmux_environment()

        assert result is not None
        assert result["session"] == "session0"
        assert result["window"] == "0"
        assert result["pane"] == "0"

        # Verify injector state was updated
        assert injector.tmux_session == "session0"
        assert injector.tmux_window == "0"
        assert injector.tmux_pane == "0"

    @patch('subprocess.run')
    def test_detect_tmux_environment_no_tmux(self, mock_run):
        """Test tmux environment detection when tmux is not available"""
        # Mock tmux not found
        mock_run.side_effect = [
            subprocess.CalledProcessError("which: tmux: not found"),
        ]

        injector = TmuxGenerator("test_session")
        result = injector.detect_tmux_environment()

        assert result is None
        assert injector.tmux_session is None

    @patch('subprocess.run')
    def test_detect_tmux_environment_partial_info(self, mock_run):
        """Test tmux environment detection with partial information"""
        # Mock tmux returning only session info
        mock_run.return_value = Mock(
            returncode=0,
            stdout="session0"
        )

        injector = TmuxInjector("test_session")
        result = injector.detect_tmux_environment()

        assert result is not None
        assert result["session"] == "session0"
        assert result["window"] == "0"  # Default values
        assert result["pane"] == "0"    # Default values

    @patch('subprocess.run')
    def test_capture_current_input_with_tmux(self, mock_run):
        """Test capturing current input from tmux"""
        # Mock tmux capture-pane output
        mock_run.return_value = Mock(
            returncode=0,
            stdout="prompt> current command here\nprevious line\nanother line"
        )

        # Set up tmux environment
        mock_run.side_effect = [
            Mock(returncode=0, stdout="session0:0.0"),
        ]
        injector = TmuxInjector("test_session")
        injector.detect_tmux_environment()

        input_text = injector.capture_current_input()
        assert input_text == "prompt> current command here"

    def test_capture_current_input_no_tmux(self):
        """Test capturing input when tmux is not available"""
        injector = TmuxGenerator("test_session")
        # tmux_session is None

        input_text = injector.capture_current_input()
        assert input_text == ""

    @patch('subprocess.run')
    def test_is_user_typing_with_activity(self, mock_run, mock_time):
        """Test user typing detection when user is actively typing"""
        # Mock time.sleep to not actually wait
        mock_time.side_effect = lambda x: None

        # Mock different capture results
        mock_run.side_effect = [
            Mock(returncode=0, stdout="session0:0.0"),
            Mock(returncode=0, stdout="prompt> comman"),  # Input changed
        ]

        # Set up tmux environment
        mock_run.side_effect = [
            Mock(returncode=0, stdout="session0:0.0"),
        ]
        injector = TmuxInjector("test_session")
        injector.detect_tmux_environment()

        # Mock capture to return different results
        def mock_capture_side_effect(*args, **kwargs):
            if "capture-pane" in args[0]:
                return Mock(returncode=0, stdout="prompt> comman")

        with patch.object(injector, 'capture_current_input', side_effect=mock_capture_side_effect):
            result = injector.is_user_typing(0.1)  # Short wait time
            assert result is True

    @patch('subprocess.run')
    def test_is_user_typing_no_activity(self, mock_run, mock_time):
        """Test user typing detection when user is not typing"""
        # Mock time.sleep to not actually wait
        mock_time.side_effect = lambda x: None

        # Mock same capture results (no change)
        mock_run.return_value = Mock(
            returncode=0,
            stdout="prompt> command"
        )

        # Set up tmux environment
        mock_run.side_effect = [
            Mock(returncode=0, stdout="session0:0.0"),
        ]
        injector = TmuxInjector("test_session")
        injector.detect_tmux_environment()

        result = injector.is_user_typing(0.1)
        assert result is False

    def test_is_user_typing_no_tmux(self):
        """Test user typing detection when tmux is not available"""
        injector = TmuxGenerator("test_session")
        # tmux_session is None

        result = injector.is_user_typing()
        assert result is False

    @patch('subprocess.run')
    def test_clear_command_line_success(self, mock_run):
        """Test successful command line clearing"""
        # Mock successful tmux commands
        mock_run.return_value = Mock(returncode=0)

        # Set up tmux environment
        mock_run.side_effect = [
            Mock(returncode=0, stdout="session0:0.0"),
        ]
        injector = TmuxInjector("test_session")
        injector.detect_tmux_environment()

        result = injector.clear_command_line()
        assert result is True

        # Verify tmux commands were called
        clear_calls = [call for call in mock_run.call_args_list
                      if 'send-keys' in call[0] and 'C-u' in call[0]]
        assert len(clear_calls) >= 1

    @patch('subprocess.run')
    def test_clear_command_line_failure(self, mock_run):
        """Test command line clearing failure"""
        # Mock tmux command failure
        mock_run.return_value = Mock(returncode=1)

        # Set up tmux environment
        mock_run.side_effect = [
            Mock(returncode=0, stdout="session0:0.0"),
        ]
        injector = TmuxInjector("test_session")
        injector.detect_tmux_environment()

        result = injector.clear_command_line()
        assert result is False

    def test_clear_command_line_no_tmux(self):
        """Test command line clearing when tmux is not available"""
        injector = TmuxGenerator("test_session")
        # tmux_session is None

        result = injector.clear_command_line()
        assert result is False

    @patch('subprocess.run')
    def test_send_command_success(self, mock_run):
        """Test successful command sending"""
        # Mock successful tmux commands
        mock_run.return_value = Mock(returncode=0)

        # Set up tmux environment
        mock_run.side_effect = [
            Mock(returncode=0, stdout="session0:0.0"),
        ]
        injector = TmuxInjector("test_session")
        injector.detect_tmux_environment()

        result = injector.send_command("test command")
        assert result is True

        # Verify tmux commands were called
        send_calls = [call for call in mock_run.call_args_list
                       if 'send-keys' in call[0] and 'test command' in call[0]]
        assert len(send_calls) >= 1

        # Check Enter was sent
        enter_calls = [call for call in mock_run.call_args_list
                        if 'send-keys' in call[0] and 'Enter' in call[0]]
        assert len(enter_calls) >= 1

    def test_send_command_no_tmux(self):
        """Test command sending when tmux is not available"""
        injector = TmuxGenerator("test_session")
        # tmux_session is None

        result = injector.send_command("test command")
        assert result is False

    @patch('subprocess.run')
    def test_restore_input_success(self, mock_run):
        """Test successful input restoration"""
        # Mock successful tmux command
        mock_run.return_value = Mock(returncode=0)

        # Set up tmux environment
        mock_run.side_effect = [
            Mock(returncode=0, stdout="session0:0.0"),
        ]
        injector = TmuxInjector("test_session")
        injector.detect_tmux_environment()

        result = injector.restore_input("restored text")
        assert result is True

        # Verify tmux command was called with correct text
        restore_calls = [call for call in mock_run.call_args_list
                         if 'send-keys' in call[0] and 'restored text' in call[0]]
        assert len(restore_calls) >= 1

    def test_restore_input_no_tmux(self):
        """Test input restoration when tmux is not available"""
        injector = TmuxGenerator("test_session")
        # tmux_session is None

        result = injector.restore_input("some text")
        assert result is False

    def test_restore_input_empty(self):
        """Test input restoration with empty input"""
        injector = TmuxGenerator("test_session")
        # tmux_session is None

        result = injector.restore_input("")
        assert result is False

    @patch('subprocess.run')
    def test_inject_prompt_full_workflow(self, mock_run, mock_time):
        """Test complete prompt injection workflow"""
        # Mock time.sleep to not actually wait
        mock_time.side_effect = lambda x: None

        # Mock tmux environment and no user activity
        mock_run.side_effect = [
            Mock(returncode=0, stdout="session0:0.0"),
        ]

        # Mock successful operations
        def mock_capture_side_effect(*args, **kwargs):
            return Mock(returncode=0, stdout="prompt> previous")

        def mock_clear_side_effect(*args, **kwargs):
            return Mock(returncode=0)

        def mock_send_side_effect(*args, **kwargs):
            return Mock(returncode=0)

        def mock_restore_side_effect(*args, **kwargs):
            return Mock(returncode=0)

        # Set up injector with mocked methods
        injector = TmuxInjector("test_session")
        injector.detect_tmux_environment()

        with patch.object(injector, 'capture_current_input', side_effect=mock_capture_side_effect), \
             patch.object(injector, 'clear_command_line', side_effect=mock_clear_side_effect), \
             patch.object(injector, 'send_command', side_effect=mock_send_side_effect), \
             patch.object(injector, 'restore_input', side_effect=mock_restore_side_effect):

            result = injector.inject_prompt("test prompt")

        assert result[0] is True  # Success
        assert "tmux session session0" in result[1]  # Success message
        assert result[2] == "tmux"  # Channel used

    @patch('subprocess.run')
    def test_inject_prompt_user_typing(self, mock_run, mock_time):
        """Test prompt injection when user is actively typing"""
        # Mock time.sleep to not actually wait
        mock_time.side_effect = lambda x: None

        # Mock user typing detection
        def mock_capture_side_effect(*args, **kwargs):
            return Mock(returncode=0, stdout="prompt> comman")  # Input changed

        # Mock tmux environment
        mock_run.side_effect = [
            Mock(returncode=0, stdout="session0:0.0"),
        ]

        injector = TmuxInjector("test_session")
        injector.detect_tmux_environment()

        with patch.object(injector, 'capture_current_input', side_effect=mock_capture_side_effect):
            result = injector.inject_prompt("test prompt")

        assert result[0] is False  # Failed
        assert "User actively typing" in result[1]

    @patch('subprocess.run')
    def test_inject_prompt_clear_failure(self, mock_run):
        """Test prompt injection when command line clearing fails"""
        # Mock tmux environment detection
        mock_run.side_effect = [
            Mock(returncode=0, stdout="session0:0.0"),
        ]

        # Mock command line clearing failure
        def mock_clear_side_effect(*args, **kwargs):
            return Mock(returncode=1)

        # Mock successful other operations
        def mock_send_side_effect(*args, **kwargs):
            return Mock(returncode=0)

        injector = TmuxInjector("test_session")
        injector.detect_tmux_environment()

        with patch.object(injector, 'clear_command_line', side_effect=mock_clear_side_effect), \
             patch.object(injector, 'send_command', side_effect=mock_send_side_effect):

            result = injector.inject_prompt("test prompt")

        assert result[0] is False  # Failed
        assert "Failed to clear command line" in result[1]

    @patch('subprocess.run')
    def test_verify_tmux_session_health_success(self, mock_run):
        """Test tmux session health verification"""
        # Mock successful health check
        mock_run.return_value = Mock(returncode=0, stdout="session0 is healthy")

        # Set up tmux environment
        mock_run.side_effect = [
            Mock(returncode=0, stdout="session0:0.0"),
        ]
        injector = TmuxInjector("test_session")
        injector.detect_tmux_environment()

        result = injector.verify_tmux_session_health()
        assert result is True

    @patch('subprocess.run')
    def test_verify_tmux_session_health_failure(self, mock_run):
        """Test tmux session health verification failure"""
        # Mock health check failure
        mock_run.return_value = Mock(returncode=1, stderr="session not found")

        # Set up tmux environment
        mock_run.side_effect = [
            Mock(returncode=0, stdout="session0:0.0"),
        ]
        injector = TmuxInjector("test_session")
        injector.detect_tmux_environment()

        result = injector.verify_tmux_session_health()
        assert result is False

    def test_verify_tmux_session_health_no_session(self):
        """Test session health verification when no session is detected"""
        injector = TmuxGenerator("test_session")
        # tmux_session is None

        result = injector.verify_tmux_session_health()
        assert result is False

    def test_get_tmux_session_info(self):
        """Test getting tmux session information"""
        injector = TmuxInjector("test_session")
        # No session detected yet

        info = injector.get_tmux_session_info()
        assert info["session"] == "unknown"
        assert info["window"] == "unknown"
        assert info["pane"] == "unknown"

        # Mock session detection
        with patch.object(injector, 'detect_tmux_environment') as mock_detect:
            mock_detect.return_value = {
                "session": "test_session",
                "window": "1",
                "pane": "2"
            }
            injector.detect_tmux_environment()

            info = injector.get_tmux_session_info()
            assert info["session"] == "test_session"
            assert info["window"] == "1"
            assert info["pane"] == "2"


class TestDualChannelInjector:
    """Test suite for dual-channel injector system"""

    def setup_method(self):
        """Set up test environment"""
        self.injector = DualChannelInjector("test_session")

    def test_dual_channel_injector_initialization(self):
        """Test dual channel injector initialization"""
        injector = DualChannelInjector("custom_session")
        assert injector.session_id == "custom_session"
        assert isinstance(injector.tmux_injector, TmuxInjector)
        assert injector.injection_history == []

    @patch('clautorun.tmux_injector.TmuxInjector.inject_prompt')
    def test_inject_prompt_api_success(self, mock_inject):
        """Test successful API injection (mocked)"""
        # Mock API injection success
        mock_inject.return_value = (True, "API injection successful")

        result = self.injector.inject_prompt("test prompt", preferred_channel="api", enable_tmux_fallback=False)

        assert result[0] is True
        assert result[1] == "API injection successful"
        assert result[2] == "api"

        # Check injection history
        assert len(self.injector.injection_history) == 1
        record = self.injector.injection_history[0]
        assert record["session_id"] == "test_session"
        assert record["prompt_length"] == len("test prompt")
        assert record["preferred_channel"] == "api"
        assert record["channel_used"] == "api"
        assert record["success"] is True

    @patch('clautorun.tmux_injector.TmuxInjector.inject_prompt')
    def test_inject_prompt_api_failure_with_fallback(self, mock_inject):
        """Test API injection failure with tmux fallback"""
        # Mock API injection failure
        mock_inject.return_value = (False, "API injection failed")

        # Mock tmux injection success
        def mock_tmux_injector_init():
            mock_instance = Mock()
            mock_instance.inject_prompt.return_value = (True, "tmux injection successful")
            return mock_instance

        with patch('clautorun.tmux_injector.TmuxInjector', mock_tmux_injector_init):
            self.injector = DualChannelInjector("test_session")
            result = self.injector.inject_prompt("test prompt", preferred_channel="api", enable_tmux_fallback=True)

        assert result[0] is True
        assert result[1] == "tmux injection successful"
        assert result[2] == "tmux"

        # Check injection history
        assert len(self.injector.injection_history) == 1
        record = self.injector.injection_history[0]
        assert record["channel_used"] == "tmux"

    @patch('clautorun.tmux_injector.TmuxInjector.inject_prompt')
    def test_inject_prompt_both_channels_fail(self, mock_inject):
        """Test both API and tmux injection failure"""
        # Mock both injection methods failing
        mock_inject.return_value = (False, "Injection failed")

        result = self.injector.inject_prompt("test prompt", preferred_channel="api", enable_tmux_fallback=True)

        assert result[0] is False
        assert "No injection channel available" in result[1]
        assert result[2] == "none"

        # Check injection history
        assert len(self.injector.injection_history) == 1
        record = self.injector.injection_history[0]
        assert record["success"] is False

    @patch('clautorun.tmux_injector.TmuxInjector.inject_prompt')
    def test_inject_prompt_tmux_only(self, mock_inject):
        """Test tmux-only injection"""
        # Mock tmux injection success
        mock_inject.return_value = (True, "tmux injection successful")

        result = self.injector.inject_prompt("test prompt", preferred_channel="tmux", enable_tmux_fallback=True)

        assert result[0] is True
        assert result[1] == "tmux injection successful"
        assert result[2] == "tmux"

    def test_inject_prompt_statistics_empty(self):
        """Test injection statistics with no history"""
        stats = self.injector.get_injection_statistics()
        assert stats["total_attempts"] == 0
        assert stats["successful_injections"] == 0
        assert stats["success_rate"] == 0
        assert "channel_statistics" in stats
        assert "tmux_session_info" in stats

    def test_inject_prompt_statistics_with_history(self):
        """Test injection statistics with history"""
        # Mock some injection history
        self.injector.injection_history = [
            {
                "timestamp": time.time(),
                "session_id": "test_session",
                "prompt_length": 10,
                "preferred_channel": "api",
                "channel_used": "api",
                "success": True,
                "message": "Success"
            },
            {
                "timestamp": time.time(),
                "session_id": "test_session",
                "prompt_length": 15,
                "preferred_channel": "tmux",
                "channel_used": "tmux",
                "success": True,
                "message": "Success"
            },
            {
                "timestamp": time.time(),
                "session_id": "test_session",
                "prompt_length": 8,
                "preferred_channel": "api",
                "channel_used": "none",
                "success": False,
                "message": "Failed"
            }
        ]

        stats = self.injector.get_injection_statistics()
        assert stats["total_attempts"] == 3
        assert stats["successful_injections"] == 2
        assert stats["success_rate"] == 2/3
        assert "channel_statistics" in stats
        assert stats["channel_statistics"]["api"]["attempts"] == 2
        assert stats["channel_statistics"]["api"]["successes"] == 1
        assert stats["channel_statistics"]["tmux"]["attempts"] == 1
        assert stats["channel_statistics"]["tmux"]["successes"] == 1

    def test_try_api_injection_placeholder(self):
        """Test API injection placeholder implementation"""
        result = self.injector._try_api_injection("test prompt")
        assert result[0] is False
        assert "API injection not implemented" in result[1]


if __name__ == "__main__":
    pytest.main([__file__])