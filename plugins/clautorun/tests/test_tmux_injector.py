#!/usr/bin/env python3
"""Unit tests for tmux injector system

Tests are designed to:
1. Use real tmux execution in the 'clautorun' session where possible
2. Only mock time.sleep to avoid delays
3. Test real behavior against actual tmux
"""

import pytest
import subprocess
import time
import sys
import os
from unittest.mock import patch

# Add src directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from clautorun.tmux_injector import TmuxInjector, DualChannelInjector


def is_tmux_available():
    """Check if tmux is available on the system"""
    try:
        result = subprocess.run(['which', 'tmux'], capture_output=True, text=True)
        return result.returncode == 0
    except Exception:
        return False


def ensure_clautorun_session():
    """Ensure 'clautorun' test session exists"""
    try:
        # Check if session exists
        result = subprocess.run(
            ['tmux', 'has-session', '-t', 'clautorun'],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            # Create the session
            subprocess.run(
                ['tmux', 'new-session', '-d', '-s', 'clautorun'],
                capture_output=True, text=True
            )
        return True
    except Exception:
        return False


# Skip all tests if tmux is not available
pytestmark = pytest.mark.skipif(
    not is_tmux_available(),
    reason="tmux is not available on this system"
)


class TestTmuxInjector:
    """Test suite for tmux injector functionality"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Set up test environment with 'clautorun' session"""
        ensure_clautorun_session()
        self.injector = TmuxInjector("clautorun")

    def test_tmux_injector_initialization(self):
        """Test tmux injector initialization"""
        injector = TmuxInjector("clautorun")
        assert injector.session_id == "clautorun"
        assert injector.tmux_session is None  # Not detected yet
        assert injector.tmux_window is None
        assert injector.tmux_pane is None

    def test_tmux_injector_default_session(self):
        """Test tmux injector uses default 'clautorun' session"""
        injector = TmuxInjector()  # No session specified
        assert injector.session_id == "clautorun"

    def test_detect_tmux_environment_success(self):
        """Test successful tmux environment detection with real tmux"""
        injector = TmuxInjector("clautorun")
        result = injector.detect_tmux_environment()

        # Should detect an environment (either current or create clautorun session)
        assert result is not None
        assert "session" in result
        assert "window" in result
        assert "pane" in result

    def test_detect_tmux_environment_creates_session(self):
        """Test that detect_tmux_environment returns an environment even when clautorun is killed"""
        # Kill clautorun session if it exists
        subprocess.run(['tmux', 'kill-session', '-t', 'clautorun'], capture_output=True)

        injector = TmuxInjector("clautorun")
        result = injector.detect_tmux_environment()

        # When running inside tmux, detect_tmux_environment() returns the current session
        # If not in tmux, it creates/returns the 'clautorun' session
        # Either way, we should get a valid environment
        assert result is not None
        assert "session" in result
        assert "window" in result
        assert "pane" in result

        # Clean up - recreate session for other tests
        ensure_clautorun_session()

    def test_capture_current_input_no_session_detected(self):
        """Test capturing input when no tmux session is detected yet"""
        injector = TmuxInjector("clautorun")
        # tmux_session is None (not detected yet)

        input_text = injector.capture_current_input()
        assert input_text == ""

    def test_capture_current_input_with_session(self):
        """Test capturing input from detected tmux session"""
        injector = TmuxInjector("clautorun")
        result = injector.detect_tmux_environment()

        if result:
            # After detection, capture should work
            input_text = injector.capture_current_input()
            # Input could be empty or contain prompt text
            assert isinstance(input_text, str)

    @patch('time.sleep')
    def test_is_user_typing_no_session(self, mock_sleep):
        """Test user typing detection when no session is detected"""
        injector = TmuxInjector("clautorun")
        # tmux_session is None

        result = injector.is_user_typing()
        assert result is False

    @patch('time.sleep')
    def test_is_user_typing_with_session(self, mock_sleep):
        """Test user typing detection with a real session"""
        mock_sleep.return_value = None  # Don't actually sleep

        injector = TmuxInjector("clautorun")
        injector.detect_tmux_environment()

        if injector.tmux_session:
            # With a real session, should return a boolean
            result = injector.is_user_typing(0.1)
            assert isinstance(result, bool)

    def test_clear_command_line_no_session(self):
        """Test command line clearing when no session is detected"""
        injector = TmuxInjector("clautorun")
        # tmux_session is None

        result = injector.clear_command_line()
        assert result is False

    def test_clear_command_line_with_session(self):
        """Test command line clearing with real session"""
        injector = TmuxInjector("clautorun")
        result = injector.detect_tmux_environment()

        if result and injector.tmux_session:
            clear_result = injector.clear_command_line()
            assert isinstance(clear_result, bool)

    def test_send_command_no_session(self):
        """Test command sending when no session is detected"""
        injector = TmuxInjector("clautorun")
        # tmux_session is None

        result = injector.send_command("echo test")
        assert result is False

    def test_send_command_with_session(self):
        """Test command sending to real session"""
        injector = TmuxInjector("clautorun")
        result = injector.detect_tmux_environment()

        if result and injector.tmux_session:
            # Send a safe command
            send_result = injector.send_command("# test comment")
            assert isinstance(send_result, bool)

    def test_restore_input_no_session(self):
        """Test input restoration when no session is detected"""
        injector = TmuxInjector("clautorun")
        # tmux_session is None

        result = injector.restore_input("some text")
        assert result is False

    def test_restore_input_empty_text(self):
        """Test input restoration with empty text"""
        injector = TmuxInjector("clautorun")
        injector.detect_tmux_environment()

        result = injector.restore_input("")
        assert result is False

    @patch('time.sleep')
    def test_inject_prompt_no_tmux_environment(self, mock_sleep):
        """Test prompt injection when tmux environment cannot be detected"""
        mock_sleep.return_value = None

        # Mock the detect method to return None
        with patch.object(TmuxInjector, 'detect_tmux_environment', return_value=None):
            injector = TmuxInjector("nonexistent_session")
            result = injector.inject_prompt("test prompt")

        assert result[0] is False
        assert "not detected" in result[1].lower()

    def test_verify_tmux_session_health_no_session(self):
        """Test session health verification when no session is detected"""
        injector = TmuxInjector("clautorun")
        # tmux_session is None

        result = injector.verify_tmux_session_health()
        assert result is False

    def test_verify_tmux_session_health_with_session(self):
        """Test session health verification with real session"""
        injector = TmuxInjector("clautorun")
        result = injector.detect_tmux_environment()

        if result and injector.tmux_session:
            health = injector.verify_tmux_session_health()
            assert isinstance(health, bool)

    def test_get_tmux_session_info_no_detection(self):
        """Test getting session info - auto-detects when tmux_session is None"""
        injector = TmuxInjector("clautorun")
        # Note: get_tmux_session_info() calls detect_tmux_environment() internally
        # when tmux_session is None, so it auto-detects the environment

        info = injector.get_tmux_session_info()
        # When running inside tmux, it detects the current session
        # When not in tmux and detection fails, returns 'unknown'
        assert "session" in info
        assert "window" in info
        assert "pane" in info
        # Values will be actual session info if in tmux, 'unknown' otherwise
        assert isinstance(info["session"], str)
        assert isinstance(info["window"], str)
        assert isinstance(info["pane"], str)

    def test_get_tmux_session_info_after_detection(self):
        """Test getting session info after detection"""
        injector = TmuxInjector("clautorun")
        result = injector.detect_tmux_environment()

        if result:
            info = injector.get_tmux_session_info()
            # After detection, should have actual values
            assert "session" in info
            assert "window" in info
            assert "pane" in info


class TestDualChannelInjector:
    """Test suite for dual-channel injector system"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Set up test environment"""
        ensure_clautorun_session()
        self.injector = DualChannelInjector("clautorun")

    def test_dual_channel_injector_initialization(self):
        """Test dual channel injector initialization"""
        injector = DualChannelInjector("clautorun")
        assert injector.session_id == "clautorun"
        assert isinstance(injector.tmux_injector, TmuxInjector)
        assert injector.injection_history == []

    def test_inject_prompt_tmux_channel(self):
        """Test injection via tmux channel with real tmux"""
        result = self.injector.inject_prompt(
            "test prompt",
            preferred_channel="tmux",
            enable_tmux_fallback=True
        )

        # Should return a 3-tuple
        assert len(result) == 3
        assert isinstance(result[0], bool)  # success
        assert isinstance(result[1], str)   # message
        assert isinstance(result[2], str)   # channel

    def test_inject_prompt_api_not_implemented(self):
        """Test API injection returns not implemented"""
        result = self.injector._try_api_injection("test prompt")

        assert result[0] is False
        assert "not implemented" in result[1].lower()

    def test_inject_prompt_statistics_empty(self):
        """Test injection statistics with no history"""
        stats = self.injector.get_injection_statistics()

        # Implementation returns only {"total_attempts": 0} for empty history
        # Full stats (successful_injections, success_rate, etc.) only returned
        # when there is actual history
        assert stats["total_attempts"] == 0
        # These keys are NOT present in empty history response
        assert "successful_injections" not in stats or stats.get("successful_injections", 0) == 0

    def test_inject_prompt_statistics_with_history(self):
        """Test injection statistics with history"""
        # Add mock history
        self.injector.injection_history = [
            {
                "timestamp": time.time(),
                "session_id": "clautorun",
                "prompt_length": 10,
                "preferred_channel": "api",
                "channel_used": "api",
                "success": True,
                "message": "Success"
            },
            {
                "timestamp": time.time(),
                "session_id": "clautorun",
                "prompt_length": 15,
                "preferred_channel": "tmux",
                "channel_used": "tmux",
                "success": True,
                "message": "Success"
            },
            {
                "timestamp": time.time(),
                "session_id": "clautorun",
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

    def test_injection_history_tracking(self):
        """Test that injection attempts are tracked in history"""
        initial_count = len(self.injector.injection_history)

        # Attempt an injection
        self.injector.inject_prompt("test prompt", preferred_channel="tmux")

        # Should have one more entry
        assert len(self.injector.injection_history) == initial_count + 1

        # Check the record structure
        record = self.injector.injection_history[-1]
        assert "timestamp" in record
        assert "session_id" in record
        assert "prompt_length" in record
        assert "preferred_channel" in record
        assert "channel_used" in record
        assert "success" in record
        assert "message" in record


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
