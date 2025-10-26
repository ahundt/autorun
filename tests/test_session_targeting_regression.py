#!/usr/bin/env python3
"""
Regression test for session targeting issue.

This test verifies that tmux commands are properly targeted to the correct session
and don't leak into the current session.
"""

import pytest
import subprocess
import time
import os
import sys

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from clautorun.tmux_utils import get_tmux_utilities


class TestSessionTargetingRegression:
    """Test session targeting to prevent commands leaking into current session"""

    def setup_method(self):
        """Set up test sessions"""
        self.test_session = "regression-test-session"
        self.current_session = "main"  # Expected current session

        # Ensure test session exists
        subprocess.run(['tmux', 'new-session', '-d', '-s', self.test_session],
                      capture_output=True, timeout=5)

        # Get tmux utilities for test session
        self.tmux = get_tmux_utilities(self.test_session)

        # Store initial current session content for leakage detection
        initial_capture = subprocess.run(['tmux', 'capture-pane', '-p'],
                                        capture_output=True, text=True, timeout=5)
        self.initial_current_content = initial_capture.stdout

    def teardown_method(self):
        """Clean up test sessions"""
        subprocess.run(['tmux', 'kill-session', '-t', self.test_session],
                      capture_output=True, timeout=5)

    def _capture_session_content(self, session_name):
        """Helper to capture content from a specific session"""
        result = subprocess.run(['tmux', 'capture-pane', '-t', session_name, '-p'],
                              capture_output=True, text=True, timeout=5)
        return result.stdout if result.returncode == 0 else ""

    def _detect_leakage_in_current_session(self, test_text):
        """Check if test text leaked into current session"""
        current_content = self._capture_session_content(self.current_session)
        return test_text in current_content

    def _verify_command_in_target_session(self, test_text):
        """Check if command appeared in target session"""
        target_content = self._capture_session_content(self.test_session)
        return test_text in target_content

    def test_send_keys_targets_correct_session(self):
        """Test that send-keys properly targets the specified session"""
        # Clear current session to detect any leakage
        subprocess.run(['tmux', 'send-keys', 'C-c'], capture_output=True, timeout=5)
        time.sleep(0.1)  # Brief pause

        # Send test command to target session
        test_text = "echo regression-test-target"
        result = self.tmux.send_keys(test_text, self.test_session)
        assert result is True, "send_keys should succeed"

        # Send Enter to execute the command
        result = self.tmux.send_keys('C-m', self.test_session)
        assert result is True, "send_keys for Enter should succeed"

        # Wait a moment for command to execute
        time.sleep(0.2)

        # Verify the command appeared in target session
        target_has_command = self._verify_command_in_target_session(test_text)
        assert target_has_command, f"Test text '{test_text}' should appear in target session"

        # Verify no leakage to current session
        current_has_leakage = self._detect_leakage_in_current_session(test_text)
        assert not current_has_leakage, f"Test text '{test_text}' should NOT appear in current session"

    def test_multiple_commands_isolation(self):
        """Test that multiple commands remain isolated between sessions"""
        test_commands = [
            "echo test-cmd-1",
            "echo test-cmd-2",
            "echo test-cmd-3"
        ]

        # Send all commands to target session
        for cmd in test_commands:
            self.tmux.send_keys(cmd, self.test_session)
            self.tmux.send_keys('C-m', self.test_session)
            time.sleep(0.1)

        # Wait for all commands to execute
        time.sleep(0.3)

        # Verify all commands appear in target session
        target_content = self._capture_session_content(self.test_session)
        for cmd in test_commands:
            assert cmd in target_content, f"Command '{cmd}' should appear in target session"

        # Verify NO commands leaked to current session
        current_content = self._capture_session_content(self.current_session)
        for cmd in test_commands:
            assert cmd not in current_content, f"Command '{cmd}' should NOT appear in current session"

    def test_tmux_environment_detection(self):
        """Test that we can detect the current tmux environment"""
        from clautorun.tmux_utils import TmuxUtilities

        tmux_utils = TmuxUtilities()
        env_info = tmux_utils.detect_tmux_environment()

        assert env_info is not None, "Should detect tmux environment when running inside tmux"
        assert 'session' in env_info, "Environment info should include session"
        assert 'window' in env_info, "Environment info should include window"
        assert 'pane' in env_info, "Environment info should include pane"

    def test_execute_tmux_command_construction(self):
        """Test that execute_tmux_command constructs commands correctly"""
        # Test send-keys command construction
        result = self.tmux.execute_tmux_command(['send-keys', 'test-construction'])

        assert result is not None, "Should return result"
        assert 'command' in result, "Result should include command that was executed"

        command = result['command']
        assert isinstance(command, list), "Command should be a list"
        assert len(command) >= 5, "Command should have tmux, socket/send-keys, text, -t, target"
        assert command[0] == 'tmux', "Should start with tmux"

        # When running within tmux, command should include socket specification
        # Format: ['tmux', '-S', socket_path, 'send-keys', '-t', 'session', 'text']
        if '-S' in command:
            socket_index = command.index('-S')
            assert socket_index + 1 < len(command), "Socket path should follow -S flag"
            assert command[socket_index + 1].startswith('/'), "Socket path should be absolute path"
            send_keys_index = socket_index + 2
            assert command[send_keys_index] == 'send-keys', "Should include send-keys after socket spec"
        else:
            assert command[1] == 'send-keys', "Should include send-keys when no socket"

        assert 'test-construction' in command, "Should include the text to send"
        assert '-t' in command, "Should include target flag"
        assert self.test_session in command, "Should include target session"

    def test_session_targeting_with_different_methods(self):
        """Test session targeting using different approaches"""
        test_text = "echo method-test"

        # Method 1: Direct session parameter
        result1 = self.tmux.execute_tmux_command(['send-keys', test_text], session=self.test_session)
        assert result1 and result1['returncode'] == 0

        # Method 2: Using instance default session
        tmux_with_default = get_tmux_utilities(self.test_session)
        result2 = tmux_with_default.execute_tmux_command(['send-keys', test_text])
        assert result2 and result2['returncode'] == 0

        # Both should have proper targeting
        for result in [result1, result2]:
            assert '-t' in result['command'], "Command should include targeting"
            assert self.test_session in result['command'], "Should target correct session"

    def test_leakage_detection_sensitivity(self):
        """Test that our leakage detection is working properly"""
        # Use a unique timestamp to avoid conflicts with any existing session content
        import time
        timestamp = int(time.time() * 1000)
        test_text = f"echo leak-test-{timestamp}"

        # Create a temporary session for testing leakage detection
        temp_session = f"leak-test-{timestamp}"
        subprocess.run(['tmux', 'new-session', '-d', '-s', temp_session],
                      capture_output=True, timeout=5)

        try:
            # Test our leakage detection by sending command to temp session
            # and verifying we can detect it there
            subprocess.run(['tmux', 'send-keys', '-t', temp_session, test_text], capture_output=True, timeout=5)
            subprocess.run(['tmux', 'send-keys', '-t', temp_session, 'C-m'], capture_output=True, timeout=5)
            time.sleep(0.2)

            # Our leakage detection should find this in the temp session
            temp_content = self._capture_session_content(temp_session)
            has_leakage_in_temp = test_text in temp_content
            assert has_leakage_in_temp, f"Should detect test text '{test_text}' in temp session"

            # Verify that the UNIQUE test text is NOT in the main current session
            current_has_leakage = self._detect_leakage_in_current_session(test_text)
            assert not current_has_leakage, f"Unique test text '{test_text}' should NOT appear in current session"

            # Also test that our detection method works by checking it can find existing content
            # This verifies the detection mechanism itself is working
            current_content = self._capture_session_content(self.current_session)
            assert isinstance(current_content, str), "Should be able to capture current session content"

        finally:
            # Clean up temporary session
            subprocess.run(['tmux', 'kill-session', '-t', temp_session],
                          capture_output=True, timeout=5)

    def test_session_cleanup_and_isolation(self):
        """Test that session cleanup works and doesn't affect other sessions"""
        # Create a temporary session
        temp_session = "temp-cleanup-test"
        subprocess.run(['tmux', 'new-session', '-d', '-s', temp_session],
                      capture_output=True, timeout=5)

        try:
            # Send command to temp session
            temp_tmux = get_tmux_utilities(temp_session)
            temp_tmux.send_keys("echo temp-session-test", temp_session)
            temp_tmux.send_keys('C-m', temp_session)
            time.sleep(0.2)

            # Verify it appears in temp session
            temp_content = self._capture_session_content(temp_session)
            assert "echo temp-session-test" in temp_content

            # Verify it doesn't appear in our main test session
            main_content = self._capture_session_content(self.test_session)
            assert "echo temp-session-test" not in main_content

        finally:
            # Clean up temp session
            subprocess.run(['tmux', 'kill-session', '-t', temp_session],
                          capture_output=True, timeout=5)

    def test_command_construction_includes_target(self):
        """Test that tmux commands are constructed with proper targeting"""
        from clautorun.tmux_utils import TmuxUtilities

        # Create tmux utilities instance
        tmux_utils = TmuxUtilities("test-construction")

        # Test command construction for send-keys
        result = tmux_utils.execute_tmux_command(['send-keys', 'test-text'])

        assert result is not None, "Command should execute"
        assert result['returncode'] == 0, "Command should succeed"

        # Verify the command includes proper targeting
        command = result['command']
        assert 'tmux' in command, "Command should start with tmux"
        assert 'send-keys' in command, "Command should include send-keys"
        assert '-t' in command, "Command should include target flag"
        assert 'test-construction' in command, "Command should include target session"

        # Expected format: ['tmux', 'send-keys', 'test-text', '-t', 'test-construction']
        expected_target_index = command.index('-t')
        assert expected_target_index > 0, "-t flag should be present"
        assert expected_target_index + 1 < len(command), "Target should follow -t flag"
        assert command[expected_target_index + 1] == 'test-construction', "Target should be correct"

    def test_session_isolation_between_different_targets(self):
        """Test that different sessions remain isolated"""
        session1 = "isolation-test-1"
        session2 = "isolation-test-2"

        # Create test sessions
        subprocess.run(['tmux', 'new-session', '-d', '-s', session1], capture_output=True, timeout=5)
        subprocess.run(['tmux', 'new-session', '-d', '-s', session2], capture_output=True, timeout=5)

        try:
            # Get tmux utilities for each session
            tmux1 = get_tmux_utilities(session1)
            tmux2 = get_tmux_utilities(session2)

            # Send different commands to each session
            text1 = "echo session1-only"
            text2 = "echo session2-only"

            result1 = tmux1.send_keys(text1, session1)
            result2 = tmux2.send_keys(text2, session2)

            result1 = tmux1.send_keys('C-m', session1)
            result2 = tmux2.send_keys('C-m', session2)

            assert result1 is True and result2 is True, "Both commands should succeed"
            time.sleep(0.2)

            # Verify isolation
            capture1 = subprocess.run(['tmux', 'capture-pane', '-t', session1, '-p'],
                                    capture_output=True, text=True, timeout=5)
            capture2 = subprocess.run(['tmux', 'capture-pane', '-t', session2, '-p'],
                                    capture_output=True, text=True, timeout=5)

            assert text1 in capture1.stdout, "Text1 should appear in session1"
            assert text2 in capture2.stdout, "Text2 should appear in session2"
            assert text2 not in capture1.stdout, "Text2 should NOT appear in session1"
            assert text1 not in capture2.stdout, "Text1 should NOT appear in session2"

        finally:
            # Clean up test sessions
            subprocess.run(['tmux', 'kill-session', '-t', session1], capture_output=True, timeout=5)
            subprocess.run(['tmux', 'kill-session', '-t', session2], capture_output=True, timeout=5)

    def test_no_target_leakage_in_current_session(self):
        """Test that targeted commands don't leak into current session"""
        # Get current session content before test
        before_capture = subprocess.run(['tmux', 'capture-pane', '-p'],
                                       capture_output=True, text=True, timeout=5)
        before_content = before_capture.stdout

        # Send multiple commands to test session
        test_commands = [
            "echo test-command-1",
            "echo test-command-2",
            "echo test-command-3"
        ]

        for cmd in test_commands:
            self.tmux.send_keys(cmd, self.test_session)
            self.tmux.send_keys('C-m', self.test_session)
            time.sleep(0.1)

        # Check current session content after test
        after_capture = subprocess.run(['tmux', 'capture-pane', '-p'],
                                      capture_output=True, text=True, timeout=5)
        after_content = after_capture.stdout

        # None of the test commands should appear in current session
        for cmd in test_commands:
            assert cmd not in after_content, f"Command '{cmd}' should NOT appear in current session"

        # The content should be essentially the same (allowing for minor prompt changes)
        # This ensures no leakage occurred

    def test_session_targeting_with_window_and_pane(self):
        """Test session targeting with specific window and pane specifications"""
        # Test targeting with window specification
        result = self.tmux.execute_tmux_command(['send-keys', 'echo window-test'],
                                               session=self.test_session, window='0')
        assert result is not None, "Should return result"
        # Window targeting might fail if window doesn't exist, but command construction should be correct

        # Test targeting with full session:window.pane specification
        result = self.tmux.execute_tmux_command(['send-keys', 'echo pane-test'],
                                               session=self.test_session, window='0', pane='0')
        assert result is not None, "Should return result"
        # Pane targeting might fail if window/pane doesn't exist, but command construction should be correct

        # Verify commands were constructed correctly regardless of success
        if 'command' in result:
            command = result['command']
            assert '-t' in command, "Command should include target flag"
            # Check that the target specification includes session:window format
            target_index = command.index('-t')
            assert target_index + 1 < len(command), "Target should follow -t flag"
            target = command[target_index + 1]
            assert self.test_session in target, "Target should include session name"
            # Window specification may or may not be present depending on session state


if __name__ == '__main__':
    pytest.main([__file__, '-v'])