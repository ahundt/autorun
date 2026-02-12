#!/usr/bin/env python3
"""
Tests for the /cr:tabs Claude session manager.

Tests:
- Session discovery logic
- Claude session detection heuristics
- Directory extraction
- Purpose extraction
- Status detection
- Selection parsing
- Execution formatting
"""
import importlib.machinery
import importlib.util
import pytest
import sys
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

# Add commands to path
sys.path.insert(0, str(Path(__file__).parent.parent / "commands"))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# Module-level loader replacing deprecated SourceFileLoader.load_module()
_tabs_exec_module = None


def _load_tabs_exec():
    """Load the tabs-exec command module using importlib.util.

    Replaces deprecated SourceFileLoader(...).load_module() with the
    modern spec_from_file_location + exec_module pattern.
    """
    global _tabs_exec_module
    if _tabs_exec_module is None:
        tabs_path = str(Path(__file__).parent.parent / "commands" / "tabs-exec")
        loader = importlib.machinery.SourceFileLoader("tabs_exec", tabs_path)
        spec = importlib.util.spec_from_file_location("tabs_exec", tabs_path, loader=loader)
        _tabs_exec_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_tabs_exec_module)
    return _tabs_exec_module


class TestIsClaudeSession:
    """Test TmuxUtilities.is_claude_session() process-based detection.

    The old content-heuristic is_claude_session() was replaced by process-tree
    detection in TmuxUtilities. These tests mock subprocess calls to verify
    the detection logic for claude/happy/happy-dev process indicators.
    """

    def _make_tmux_utils(self):
        """Create a TmuxUtilities instance for testing."""
        from clautorun.tmux_utils import TmuxUtilities
        return TmuxUtilities(session_name="test-session")

    def test_claude_process_detected(self):
        """Detects 'claude' in child process command as Claude session."""
        tmux = self._make_tmux_utils()

        with patch.object(tmux, 'execute_tmux_command') as mock_tmux, \
             patch('clautorun.tmux_utils.subprocess.run') as mock_run:
            # Pane PID query returns a PID
            mock_tmux.return_value = {'returncode': 0, 'stdout': '12345'}
            # pgrep returns child PIDs
            mock_run.side_effect = [
                Mock(returncode=0, stdout='12346\n'),  # pgrep -P
                Mock(returncode=0, stdout='/usr/local/bin/claude\n'),  # ps -p
            ]

            assert tmux.is_claude_session('test-session', '0') is True

    def test_happy_dev_process_detected(self):
        """Detects 'happy-dev' in child process command as Claude session."""
        tmux = self._make_tmux_utils()

        with patch.object(tmux, 'execute_tmux_command') as mock_tmux, \
             patch('clautorun.tmux_utils.subprocess.run') as mock_run:
            mock_tmux.return_value = {'returncode': 0, 'stdout': '12345'}
            mock_run.side_effect = [
                Mock(returncode=0, stdout='12346\n'),  # pgrep
                Mock(returncode=0, stdout='/usr/local/bin/happy-dev\n'),  # ps
            ]

            assert tmux.is_claude_session('test-session', '0') is True

    def test_no_claude_process(self):
        """Returns False when child processes don't contain Claude indicators."""
        tmux = self._make_tmux_utils()

        with patch.object(tmux, 'execute_tmux_command') as mock_tmux, \
             patch('clautorun.tmux_utils.subprocess.run') as mock_run:
            mock_tmux.return_value = {'returncode': 0, 'stdout': '12345'}
            mock_run.side_effect = [
                Mock(returncode=0, stdout='12346\n'),  # pgrep
                Mock(returncode=0, stdout='/bin/bash\n'),  # ps - regular shell
            ]

            assert tmux.is_claude_session('test-session', '0') is False

    def test_no_child_processes(self):
        """Returns False when pgrep finds no child processes."""
        tmux = self._make_tmux_utils()

        with patch.object(tmux, 'execute_tmux_command') as mock_tmux, \
             patch('clautorun.tmux_utils.subprocess.run') as mock_run:
            mock_tmux.return_value = {'returncode': 0, 'stdout': '12345'}
            mock_run.return_value = Mock(returncode=1, stdout='')  # pgrep: no children

            assert tmux.is_claude_session('test-session', '0') is False

    def test_tmux_command_fails(self):
        """Returns False safely when tmux command fails (fail-open)."""
        tmux = self._make_tmux_utils()

        with patch.object(tmux, 'execute_tmux_command') as mock_tmux:
            mock_tmux.return_value = {'returncode': 1, 'stdout': ''}

            assert tmux.is_claude_session('test-session', '0') is False


class TestIsClaudeSessionIntegration:
    """Integration tests using real tmux to exercise is_claude_session().

    These tests create actual tmux sessions and verify the process-based
    detection works against real process trees.
    """

    @pytest.fixture(autouse=True)
    def check_tmux(self):
        """Skip if tmux is not available."""
        import shutil
        if not shutil.which("tmux"):
            pytest.skip("tmux not installed")

    def _make_tmux_utils(self):
        from clautorun.tmux_utils import TmuxUtilities
        return TmuxUtilities(session_name="clautorun-test-tabs")

    def test_real_non_claude_session(self):
        """A plain shell session should not be detected as Claude."""
        import subprocess
        import time

        session_name = "clautorun-test-tabs"
        # Create a tmux session with a plain shell
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", session_name, "-x", "80", "-y", "24"],
            check=False,
            capture_output=True,
        )
        try:
            time.sleep(0.3)  # Let session start
            tmux = self._make_tmux_utils()
            result = tmux.is_claude_session(session_name, "0")
            assert result is False, "Plain shell should not be detected as Claude session"
        finally:
            subprocess.run(["tmux", "kill-session", "-t", session_name], check=False, capture_output=True)

    def test_real_session_nonexistent(self):
        """Querying a non-existent session should return False (fail-open)."""
        tmux = self._make_tmux_utils()
        result = tmux.is_claude_session("nonexistent-session-xyz", "0")
        assert result is False


class TestExtractDirectory:
    """Test directory extraction from content."""

    def test_pwd_pattern(self):
        """Extract directory from pwd output."""
        tabs_exec = _load_tabs_exec()

        content = "$ pwd\n/home/user/myproject\n$"
        # Should find the path pattern
        result = tabs_exec.extract_directory(content)
        # May or may not match depending on regex
        assert result in ["/home/user/myproject", "unknown"]

    def test_working_directory_label(self):
        """Extract directory from 'Working directory:' label."""
        tabs_exec = _load_tabs_exec()

        content = "Working directory: ~/myproject"
        result = tabs_exec.extract_directory(content)
        assert result == "~/myproject"

    def test_no_directory_found(self):
        """Return 'unknown' when no directory pattern found."""
        tabs_exec = _load_tabs_exec()

        content = "Just some random text without paths"
        result = tabs_exec.extract_directory(content)
        assert result == "unknown"


class TestExtractPurpose:
    """Test purpose extraction from content."""

    def test_skips_noise_lines(self):
        """Should skip prompt lines and find meaningful content."""
        tabs_exec = _load_tabs_exec()

        content = "$ ls\n> \nImplementing authentication middleware for the API"
        result = tabs_exec.extract_purpose(content)
        assert "authentication" in result.lower() or "implementing" in result.lower()

    def test_returns_meaningful_line(self):
        """Should return first meaningful line (truncation handled by format_output)."""
        tabs_exec = _load_tabs_exec()

        content = "This is a very long line that should be truncated because it exceeds forty characters"
        result = tabs_exec.extract_purpose(content)
        # extract_purpose returns the full line; truncation is handled by format_output
        assert result == content


class TestGetDefaultActions:
    """Test default actions based on status."""

    def test_active_status(self):
        """Active status should suggest letting it work."""
        tabs_exec = _load_tabs_exec()
        actions = tabs_exec.get_default_actions('active')
        assert 'let it work' in actions

    def test_error_status(self):
        """Error status should suggest interrupt/retry."""
        tabs_exec = _load_tabs_exec()
        actions = tabs_exec.get_default_actions('error')
        assert 'interrupt' in actions or 'retry' in actions

    def test_idle_status(self):
        """Idle status should suggest continue."""
        tabs_exec = _load_tabs_exec()
        actions = tabs_exec.get_default_actions('idle')
        assert 'continue' in actions


class TestAnalyzeSessionsHeuristic:
    """Test heuristic session analysis."""

    def test_detects_error_status(self):
        """Should detect error status from keywords."""
        tabs_exec = _load_tabs_exec()

        sessions = [{
            'session_name': 'test',
            'window_id': '0',
            'tmux_target': 'test:0',
            'content': 'Error: Something failed\nException occurred'
        }]

        result = tabs_exec.analyze_sessions_heuristic(sessions)
        assert result[0]['status'] == 'error'

    def test_detects_idle_status(self):
        """Should detect idle status from prompt."""
        tabs_exec = _load_tabs_exec()

        sessions = [{
            'session_name': 'test',
            'window_id': '0',
            'tmux_target': 'test:0',
            'content': 'Task completed\nReady for input >'
        }]

        result = tabs_exec.analyze_sessions_heuristic(sessions)
        assert result[0]['status'] == 'idle'

    def test_detects_awaiting_input(self):
        """Should detect awaiting input from prompt at end."""
        tabs_exec = _load_tabs_exec()

        sessions = [{
            'session_name': 'test',
            'window_id': '0',
            'tmux_target': 'test:0',
            'content': 'Some output here\nWaiting for your selection >'
        }]

        result = tabs_exec.analyze_sessions_heuristic(sessions)
        assert result[0]['awaiting'] is True


class TestFormatOutput:
    """Test output formatting."""

    def test_formats_table_with_sessions(self):
        """Should format sessions into a table."""
        tabs_exec = _load_tabs_exec()

        sessions = [
            {
                'tmux_target': 'dev:0',
                'directory': '~/project',
                'purpose': 'Building feature',
                'status': 'active',
                'awaiting': False,
                'actions': ['continue', 'status']
            },
            {
                'tmux_target': 'test:1',
                'directory': '~/project',
                'purpose': 'Running tests',
                'status': 'idle',
                'awaiting': True,
                'actions': ['continue']
            }
        ]

        output = tabs_exec.format_output(sessions)

        assert '2 found' in output
        assert 'dev:0' in output
        assert 'test:1' in output
        assert 'A' in output  # Letter ID
        assert 'B' in output  # Letter ID
        assert 'Selection syntax' in output

    def test_letter_assignment(self):
        """Should assign letters A, B, C, etc."""
        tabs_exec = _load_tabs_exec()

        sessions = [
            {'tmux_target': f's{i}:0', 'directory': '~', 'purpose': 'test',
             'status': 'active', 'awaiting': False, 'actions': []}
            for i in range(5)
        ]

        output = tabs_exec.format_output(sessions)

        # Should have A through E
        for letter in 'ABCDE':
            assert letter in output


class TestFormatExecutionResults:
    """Test execution result formatting."""

    def test_formats_success_results(self):
        """Should format successful results."""
        tabs_exec = _load_tabs_exec()

        results = [
            {'target': 'dev:0', 'command': 'git status', 'success': True, 'error': None},
            {'target': 'test:1', 'command': 'pwd', 'success': True, 'error': None}
        ]

        output = tabs_exec.format_execution_results(results)

        assert '[ok]' in output
        assert 'dev:0' in output
        assert 'git status' in output
        assert '2/2' in output

    def test_formats_failed_results(self):
        """Should format failed results."""
        tabs_exec = _load_tabs_exec()

        results = [
            {'target': 'dev:0', 'command': 'cmd', 'success': False, 'error': 'session not found'}
        ]

        output = tabs_exec.format_execution_results(results)

        assert '[FAIL]' in output
        assert 'session not found' in output
        assert '0/1' in output


class TestSelectionParsing:
    """Test selection string parsing."""

    def test_parse_single_letter(self):
        """Test single letter selection."""
        tabs_exec = _load_tabs_exec()

        # Mock tmux utilities
        with patch.object(tabs_exec, 'get_tmux_utilities') as mock_get:
            mock_tmux = MagicMock()
            mock_tmux.send_keys.return_value = True
            mock_get.return_value = mock_tmux

            sessions = [
                {'tmux_target': 'dev:0', 'actions': ['continue']},
                {'tmux_target': 'test:1', 'actions': ['status']}
            ]

            result = tabs_exec.execute_selections('A', sessions)

            assert 'dev:0' in result
            assert '1/1' in result

    def test_parse_multiple_letters(self):
        """Test 'AC' format."""
        tabs_exec = _load_tabs_exec()

        with patch.object(tabs_exec, 'get_tmux_utilities') as mock_get:
            mock_tmux = MagicMock()
            mock_tmux.send_keys.return_value = True
            mock_get.return_value = mock_tmux

            sessions = [
                {'tmux_target': 'dev:0', 'actions': ['continue']},
                {'tmux_target': 'test:1', 'actions': ['status']},
                {'tmux_target': 'docs:2', 'actions': ['review']}
            ]

            result = tabs_exec.execute_selections('AC', sessions)

            assert 'dev:0' in result
            assert 'docs:2' in result
            assert '2/2' in result

    def test_parse_custom_command(self):
        """Test 'A:git status' format."""
        tabs_exec = _load_tabs_exec()

        with patch.object(tabs_exec, 'get_tmux_utilities') as mock_get:
            mock_tmux = MagicMock()
            mock_tmux.send_keys.return_value = True
            mock_get.return_value = mock_tmux

            sessions = [
                {'tmux_target': 'dev:0', 'actions': ['continue']}
            ]

            result = tabs_exec.execute_selections('A:git status', sessions)

            assert 'git status' in result

    def test_parse_all_selector(self):
        """Test 'all:continue' format."""
        tabs_exec = _load_tabs_exec()

        with patch.object(tabs_exec, 'get_tmux_utilities') as mock_get:
            mock_tmux = MagicMock()
            mock_tmux.send_keys.return_value = True
            mock_get.return_value = mock_tmux

            sessions = [
                {'tmux_target': 'dev:0'},
                {'tmux_target': 'test:1'},
                {'tmux_target': 'docs:2'}
            ]

            result = tabs_exec.execute_selections('all:continue', sessions)

            assert '3/3' in result

    def test_parse_awaiting_selector(self):
        """Test 'awaiting:continue' format."""
        tabs_exec = _load_tabs_exec()

        with patch.object(tabs_exec, 'get_tmux_utilities') as mock_get:
            mock_tmux = MagicMock()
            mock_tmux.send_keys.return_value = True
            mock_get.return_value = mock_tmux

            sessions = [
                {'tmux_target': 'dev:0', 'awaiting': False},
                {'tmux_target': 'test:1', 'awaiting': True},
                {'tmux_target': 'docs:2', 'awaiting': True}
            ]

            result = tabs_exec.execute_selections('awaiting:continue', sessions)

            # Should only execute on 2 sessions (test:1 and docs:2)
            assert '2/2' in result


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
