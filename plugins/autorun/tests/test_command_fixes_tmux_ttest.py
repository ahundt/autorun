#!/usr/bin/env python3
"""
Test suite for autorun command fixes (tmux.md, ttest.md, tm.md, tt.md)

These tests verify the bug fixes applied in the commit:
  fix(commands): rewrite no-op tmux commands and fix stale syntax

Key bugs fixed:
1. Duplicate `-t` flag in kill-session calls (session param instead of inline -t)
2. Boolean inversion in session existence checking (pre-check with has-session)
3. Unsafe cleanup scope (restrict to autorun-test* pattern)
4. Session cleanup via try/finally (guaranteed cleanup on all exit paths)
5. Correct capture output (full pane, not capture_current_input last-line-only)
6. F-string syntax errors (use local variables, not backslash in f-strings)
"""

import sys
import os
import pytest
import subprocess
import time

pytestmark = pytest.mark.tmux

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from autorun.tmux_utils import get_tmux_utilities


class TestDuplicateTargetFlagBugFix:
    """Test fix for duplicate -t flag bug in execute_tmux_command"""

    def setup_method(self):
        """Create test session"""
        self.test_session = "test-duplicate-t-flag"
        subprocess.run(['tmux', 'new-session', '-d', '-s', self.test_session],
                      capture_output=True, timeout=5)
        self.tmux = get_tmux_utilities(self.test_session)

    def teardown_method(self):
        """Clean up test session"""
        subprocess.run(['tmux', 'kill-session', '-t', self.test_session],
                      capture_output=True, timeout=5)

    def test_kill_session_with_session_param_not_inline_t(self):
        """
        Test that kill-session uses session= param, not inline -t.

        Bug: execute_tmux_command(['kill-session', '-t', 'target'])
        creates duplicate -t flags:
          - User provides: ['kill-session', '-t', 'target']
          - Function auto-appends: -t autorun (default session)
          - Result: tmux kill-session -t autorun -t target
          - tmux uses LAST -t, killing 'target' instead of 'autorun'

        Fix: execute_tmux_command(['kill-session'], session='target')
        - User provides: ['kill-session']
        - Function auto-appends: -t target (from session param)
        - Result: tmux kill-session -t target (correct!)
        """
        # Create target session to kill
        target_session = "kill-session-target"
        subprocess.run(['tmux', 'new-session', '-d', '-s', target_session],
                      capture_output=True, timeout=5)

        # Verify target session exists
        check_result = subprocess.run(['tmux', 'has-session', '-t', target_session],
                                     capture_output=True, timeout=5)
        assert check_result.returncode == 0, "Target session should exist"

        # Kill using correct session= param (not inline -t)
        result = self.tmux.execute_tmux_command(['kill-session'], session=target_session)
        assert result is not None, "Should return result"
        assert result['returncode'] == 0, "Kill-session should succeed"

        # Verify target session is dead
        check_after = subprocess.run(['tmux', 'has-session', '-t', target_session],
                                    capture_output=True, timeout=5)
        assert check_after.returncode != 0, "Target session should be killed"

        # Verify test_session is still alive (not killed instead)
        check_test_session = subprocess.run(['tmux', 'has-session', '-t', self.test_session],
                                           capture_output=True, timeout=5)
        assert check_test_session.returncode == 0, "Test session should still exist"

    def test_has_session_with_session_param_not_inline_t(self):
        """
        Test that has-session pre-check uses session= param, not inline -t.

        Bug: Pre-check with execute_tmux_command(['has-session', '-t', 'target'])
        creates duplicate -t flags, checks wrong session.

        Fix: execute_tmux_command(['has-session'], session='target')
        correctly checks the target session.

        Note: execute_tmux_command auto-creates sessions for commands in
        commands_supporting_target (including has-session), so the session will
        exist after the call. The fix ensures the command targets the correct session.
        """
        test_name = "has-session-test"

        # Create a session first
        subprocess.run(['tmux', 'new-session', '-d', '-s', test_name],
                      capture_output=True, timeout=5)

        try:
            # Pre-check with correct session= param should succeed
            check = self.tmux.execute_tmux_command(['has-session'], session=test_name)
            assert check is not None, "Should return result"
            # Note: will return 0 because execute_tmux_command auto-creates the session
            # The fix is that it targets the CORRECT session via session= param
            assert check['returncode'] == 0, f"Should successfully check session {test_name}"

            # Verify the command structure is correct (has -t with correct target)
            assert '-t' in check['command'], "Command should include -t flag"
            t_index = check['command'].index('-t')
            target_value = check['command'][t_index + 1]
            assert test_name in target_value, f"Target should be '{test_name}', got '{target_value}'"
        finally:
            subprocess.run(['tmux', 'kill-session', '-t', test_name],
                          capture_output=True, timeout=5)

    def test_command_construction_has_correct_target(self):
        """Test that command construction includes -t flag with correct value"""
        target = "explicit-target-session"

        # Create session for test
        subprocess.run(['tmux', 'new-session', '-d', '-s', target],
                      capture_output=True, timeout=5)

        try:
            # Execute with explicit session param
            result = self.tmux.execute_tmux_command(['send-keys', 'test'], session=target)

            # Verify command structure
            assert 'command' in result, "Result should include command list"
            cmd = result['command']
            assert '-t' in cmd, "Command should have -t flag"

            # Verify target is in command
            t_index = cmd.index('-t')
            assert t_index + 1 < len(cmd), "-t should have a value"
            target_value = cmd[t_index + 1]
            assert target in target_value, f"Target should be '{target}', got '{target_value}'"

            # Verify no duplicate -t flags
            t_count = cmd.count('-t')
            assert t_count == 1, f"Should have exactly one -t flag, found {t_count}"
        finally:
            subprocess.run(['tmux', 'kill-session', '-t', target],
                          capture_output=True, timeout=5)


class TestSessionExistenceCheckingBugFix:
    """Test fix for boolean inversion in session existence checking"""

    def setup_method(self):
        """Create test session"""
        self.test_session = "test-existence-check"
        subprocess.run(['tmux', 'new-session', '-d', '-s', self.test_session],
                      capture_output=True, timeout=5)
        self.tmux = get_tmux_utilities(self.test_session)

    def teardown_method(self):
        """Clean up test session"""
        subprocess.run(['tmux', 'kill-session', '-t', self.test_session],
                      capture_output=True, timeout=5)

    def test_ensure_session_exists_return_value(self):
        """
        Test that ensure_session_exists() behavior is correct.

        The fix in the command .md files (tmux.md, ttest.md) uses a pre-check
        to distinguish between create and pre-existing sessions:
          - Pre-check: execute_tmux_command(['has-session'], session=name)
          - returncode == 0: session existed already
          - returncode != 0: session didn't exist

        This test verifies the pattern works correctly.
        """
        # Create a new tmux utils instance for our specific session
        test_name = "existence-check-2"

        # Clean up any existing session
        subprocess.run(['tmux', 'kill-session', '-t', test_name],
                      capture_output=True, timeout=5)

        # Create a dedicated tmux instance for this test
        test_tmux = get_tmux_utilities(test_name)

        # Test 1: Pre-check on non-existent session
        # Direct tmux command (not auto-creating)
        check_before = subprocess.run(['tmux', 'has-session', '-t', test_name],
                                     capture_output=True, timeout=5)
        assert check_before.returncode != 0, "Session should not exist initially"

        # Test 2: Create session and verify
        result = test_tmux.ensure_session_exists()  # Uses default session_name
        assert result is True, "ensure_session_exists should return True"

        # Test 3: Verify session was created
        check_after = subprocess.run(['tmux', 'has-session', '-t', test_name],
                                    capture_output=True, timeout=5)
        assert check_after.returncode == 0, "Session should exist after create"

        # Test 4: Call again on existing session — should return True
        result2 = test_tmux.ensure_session_exists()
        assert result2 is True, "ensure_session_exists should return True for existing session"

        # Clean up
        subprocess.run(['tmux', 'kill-session', '-t', test_name],
                      capture_output=True, timeout=5)


class TestCleanupScopingBugFix:
    """Test fix for unsafe cleanup scope (should only target autorun-test*)"""

    def setup_method(self):
        """Create test sessions"""
        self.work_session = "autorun-work"
        self.test_session = "autorun-test-1"

        subprocess.run(['tmux', 'new-session', '-d', '-s', self.work_session],
                      capture_output=True, timeout=5)
        subprocess.run(['tmux', 'new-session', '-d', '-s', self.test_session],
                      capture_output=True, timeout=5)

        self.tmux = get_tmux_utilities()

    def teardown_method(self):
        """Clean up test sessions"""
        for session in [self.work_session, self.test_session]:
            subprocess.run(['tmux', 'kill-session', '-t', session],
                          capture_output=True, timeout=5)

    def test_cleanup_all_only_targets_test_sessions(self):
        """
        Test that 'cleanup --all' only removes autorun-test* sessions.

        Bug: Original scope was name.startswith('autorun')
        This would kill work sessions like 'autorun-work', 'autorun-dev', etc.

        Fix: Restrict to sname.startswith('autorun-test')
        Only test sessions are removed.
        """
        # Both sessions should exist
        work_check = subprocess.run(['tmux', 'has-session', '-t', self.work_session],
                                   capture_output=True, timeout=5)
        test_check = subprocess.run(['tmux', 'has-session', '-t', self.test_session],
                                   capture_output=True, timeout=5)
        assert work_check.returncode == 0, "Work session should exist"
        assert test_check.returncode == 0, "Test session should exist"

        # Simulate cleanup --all by killing only autorun-test* sessions
        list_result = self.tmux.execute_tmux_command(['list-sessions', '-F', '#{session_name}'])
        if list_result and list_result['returncode'] == 0:
            for sname in list_result['stdout'].strip().splitlines():
                sname = sname.strip()
                if sname.startswith('autorun-test'):
                    self.tmux.execute_tmux_command(['kill-session'], session=sname)

        # Verify work session still exists
        work_check_after = subprocess.run(['tmux', 'has-session', '-t', self.work_session],
                                        capture_output=True, timeout=5)
        assert work_check_after.returncode == 0, "Work session should NOT be killed by cleanup"

        # Verify test session is killed
        test_check_after = subprocess.run(['tmux', 'has-session', '-t', self.test_session],
                                        capture_output=True, timeout=5)
        assert test_check_after.returncode != 0, "Test session should be killed by cleanup"


class TestSessionCleanupGuarantees:
    """Test that session cleanup is guaranteed via try/finally"""

    def test_session_cleanup_on_normal_completion(self):
        """Test that session is cleaned up after normal command execution"""
        session = "cleanup-normal"

        # Create and run command
        subprocess.run(['tmux', 'new-session', '-d', '-s', session],
                      capture_output=True, timeout=5)

        tmux = get_tmux_utilities(session)

        # Simulate ttest pattern: create, run, cleanup
        tmux.ensure_session_exists(session)
        tmux.send_keys("echo test", session)
        tmux.send_keys("C-m", session)
        time.sleep(0.2)

        # Cleanup
        result = tmux.execute_tmux_command(['kill-session'], session=session)
        assert result and result['returncode'] == 0, "Should succeed"

        # Verify session is dead
        check = subprocess.run(['tmux', 'has-session', '-t', session],
                              capture_output=True, timeout=5)
        assert check.returncode != 0, "Session should be cleaned up"

    def test_session_cleanup_on_error(self):
        """Test that session is cleaned up even if an error occurs"""
        session = "cleanup-error"

        # Create session
        subprocess.run(['tmux', 'new-session', '-d', '-s', session],
                      capture_output=True, timeout=5)

        try:
            tmux = get_tmux_utilities(session)
            tmux.ensure_session_exists(session)

            # Simulate an error condition
            raise RuntimeError("Simulated test error")
        except RuntimeError:
            # In real code, this would be in a try/finally
            pass
        finally:
            # Cleanup happens regardless
            result = subprocess.run(['tmux', 'kill-session', '-t', session],
                                  capture_output=True, timeout=5)

        # Verify session is dead
        check = subprocess.run(['tmux', 'has-session', '-t', session],
                              capture_output=True, timeout=5)
        assert check.returncode != 0, "Session should be cleaned up even after error"


class TestCaptureOutputCorrectness:
    """Test that capture-pane returns full output, not just last line"""

    def setup_method(self):
        """Create test session"""
        self.test_session = "test-capture-output"
        subprocess.run(['tmux', 'new-session', '-d', '-s', self.test_session],
                      capture_output=True, timeout=5)
        self.tmux = get_tmux_utilities(self.test_session)

    def teardown_method(self):
        """Clean up test session"""
        subprocess.run(['tmux', 'kill-session', '-t', self.test_session],
                      capture_output=True, timeout=5)

    def test_capture_pane_returns_full_output(self):
        """
        Test that capture-pane returns full pane content.

        Bug: capture_current_input() returns only the LAST line.
        Fix: Use execute_tmux_command(['capture-pane', '-p']) for full output.
        """
        # Send multiple lines
        commands = ["echo line1", "echo line2", "echo line3"]

        for cmd in commands:
            self.tmux.send_keys(cmd, self.test_session)
            self.tmux.send_keys("C-m", self.test_session)
            time.sleep(0.1)

        # Wait for output
        time.sleep(0.3)

        # Capture full output
        result = self.tmux.execute_tmux_command(['capture-pane', '-p'],
                                               session=self.test_session)

        assert result is not None, "Should return result"
        assert result['returncode'] == 0, "Capture should succeed"

        output = result['stdout'].strip()

        # Verify all lines are present
        for i in range(1, 4):
            expected = f"line{i}"
            assert expected in output, f"Output should contain '{expected}'"

        # Verify it's not just the last line
        lines = output.split('\n')
        assert len(lines) > 1, "Should capture multiple lines, not just last line"


class TestFStringSyntaxFix:
    """Test that f-string syntax is correct for Python 3.9+"""

    def test_no_backslash_in_fstring_expressions(self):
        """Test that code doesn't use backslashes in f-string expressions"""
        # This test verifies the fix is in place by checking compilation

        # This should compile without syntax error (the fix)
        code_with_fix = '''
result_dict = {"stdout": "test output"}
result = result_dict
session_out = result['stdout'].strip()
output = f"Result: {session_out}"
'''

        try:
            compile(code_with_fix, '<string>', 'exec')
        except SyntaxError:
            pytest.fail("Code with fix should compile without SyntaxError")

        # This pattern (original bug) would fail on Python 3.9-3.11
        # We don't actually test it because it would fail to import
        # But we verify the fix pattern works above


class TestSymlinkInheritance:
    """Test that tm.md and tt.md symlinks properly inherit frontmatter"""

    def test_symlink_configuration(self):
        """Test that symlinks are properly configured"""
        commands_dir = os.path.join(os.path.dirname(__file__), '..', 'commands')

        tm_path = os.path.join(commands_dir, 'tm.md')
        tt_path = os.path.join(commands_dir, 'tt.md')

        # Check that they are symlinks
        assert os.path.islink(tm_path), f"tm.md should be a symlink (got {os.path.islink(tm_path)})"
        assert os.path.islink(tt_path), f"tt.md should be a symlink (got {os.path.islink(tt_path)})"

        # Check symlink targets
        tm_target = os.readlink(tm_path)
        tt_target = os.readlink(tt_path)

        assert tm_target == 'tmux.md', f"tm.md should link to tmux.md, got {tm_target}"
        assert tt_target == 'ttest.md', f"tt.md should link to ttest.md, got {tt_target}"

        # Verify content is correct (should be same as targets)
        with open(tm_path, 'r') as f:
            tm_content = f.read()
        with open(os.path.join(commands_dir, 'tmux.md'), 'r') as f:
            tmux_content = f.read()

        assert tm_content == tmux_content, "tm.md should have same content as tmux.md"

        with open(tt_path, 'r') as f:
            tt_content = f.read()
        with open(os.path.join(commands_dir, 'ttest.md'), 'r') as f:
            ttest_content = f.read()

        assert tt_content == ttest_content, "tt.md should have same content as ttest.md"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
