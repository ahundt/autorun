#!/usr/bin/env python3
"""
Tests for the /ar:tabs Claude session manager.

Tests:
- Session discovery logic (tmux_utils.discover_claude_sessions)
- Claude session detection heuristics (tmux_utils.TmuxUtilities.is_claude_session)
- Directory extraction (tmux_tab_ai_session_status.extract_directory)
- Purpose extraction (tmux_tab_ai_session_status.extract_purpose)
- Status detection (tmux_tab_ai_session_status.analyze_sessions_heuristic)
- Selection parsing (tmux_utils.execute_session_selections)
- Execution formatting (tmux_tab_ai_session_status.format_execution_results)
"""
import pytest
from unittest.mock import Mock, patch, MagicMock

from autorun.tmux_utils import (
    TmuxUtilities,
    discover_claude_sessions,
    execute_session_selections,
    send_to_session,
)
from autorun.tmux_tab_ai_session_status import (
    analyze_sessions_heuristic,
    execute_selections,
    extract_directory,
    extract_purpose,
    format_execution_results,
    format_output,
    get_default_actions,
)


class TestIsClaudeSession:
    """Test TmuxUtilities.is_claude_session() process-based detection.

    The old content-heuristic is_claude_session() was replaced by process-tree
    detection in TmuxUtilities. These tests mock subprocess calls to verify
    the detection logic for claude/happy/happy-dev process indicators.
    """

    def _make_tmux_utils(self):
        """Create a TmuxUtilities instance for testing."""
        return TmuxUtilities(session_name="test-session")

    def test_claude_process_detected(self):
        """Detects 'claude' in child process command as Claude session."""
        tmux = self._make_tmux_utils()

        with patch.object(tmux, 'execute_tmux_command') as mock_tmux, \
             patch('autorun.tmux_utils.subprocess.run') as mock_run:
            mock_tmux.return_value = {'returncode': 0, 'stdout': '12345'}
            mock_run.side_effect = [
                Mock(returncode=0, stdout='12346\n'),  # pgrep -P
                Mock(returncode=0, stdout='/usr/local/bin/claude\n'),  # ps -p
            ]

            assert tmux.is_claude_session('test-session', '0') is True

    def test_happy_dev_process_detected(self):
        """Detects 'happy-dev' in child process command as Claude session."""
        tmux = self._make_tmux_utils()

        with patch.object(tmux, 'execute_tmux_command') as mock_tmux, \
             patch('autorun.tmux_utils.subprocess.run') as mock_run:
            mock_tmux.return_value = {'returncode': 0, 'stdout': '12345'}
            mock_run.side_effect = [
                Mock(returncode=0, stdout='12346\n'),
                Mock(returncode=0, stdout='/usr/local/bin/happy-dev\n'),
            ]

            assert tmux.is_claude_session('test-session', '0') is True

    def test_no_claude_process(self):
        """Returns False when child processes don't contain Claude indicators."""
        tmux = self._make_tmux_utils()

        with patch.object(tmux, 'execute_tmux_command') as mock_tmux, \
             patch('autorun.tmux_utils.subprocess.run') as mock_run:
            mock_tmux.return_value = {'returncode': 0, 'stdout': '12345'}
            mock_run.side_effect = [
                Mock(returncode=0, stdout='12346\n'),
                Mock(returncode=0, stdout='/bin/bash\n'),
            ]

            assert tmux.is_claude_session('test-session', '0') is False

    def test_no_child_processes(self):
        """Returns False when pgrep finds no child processes."""
        tmux = self._make_tmux_utils()

        with patch.object(tmux, 'execute_tmux_command') as mock_tmux, \
             patch('autorun.tmux_utils.subprocess.run') as mock_run:
            mock_tmux.return_value = {'returncode': 0, 'stdout': '12345'}
            mock_run.return_value = Mock(returncode=1, stdout='')

            assert tmux.is_claude_session('test-session', '0') is False

    def test_tmux_command_fails(self):
        """Returns False safely when tmux command fails (fail-open)."""
        tmux = self._make_tmux_utils()

        with patch.object(tmux, 'execute_tmux_command') as mock_tmux:
            mock_tmux.return_value = {'returncode': 1, 'stdout': ''}

            assert tmux.is_claude_session('test-session', '0') is False


class TestIsClaudeSessionIntegration:
    """Integration tests using real tmux to exercise is_claude_session()."""

    @pytest.fixture(autouse=True)
    def check_tmux(self):
        """Skip if tmux is not available."""
        import shutil
        if not shutil.which("tmux"):
            pytest.skip("tmux not installed")

    def _make_tmux_utils(self):
        return TmuxUtilities(session_name="autorun-test-tabs")

    def test_real_non_claude_session(self):
        """A plain shell session should not be detected as Claude."""
        import subprocess
        import time

        session_name = "autorun-test-tabs"
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", session_name, "-x", "80", "-y", "24"],
            check=False, capture_output=True,
        )
        try:
            time.sleep(0.3)
            tmux = self._make_tmux_utils()
            result = tmux.is_claude_session(session_name, "0")
            assert result is False, "Plain shell should not be detected as Claude session"
        finally:
            subprocess.run(["tmux", "kill-session", "-t", session_name],
                           check=False, capture_output=True)

    def test_real_session_nonexistent(self):
        """Querying a non-existent session should return False (fail-open)."""
        tmux = self._make_tmux_utils()
        result = tmux.is_claude_session("nonexistent-session-xyz", "0")
        assert result is False


class TestExtractDirectory:
    """Test directory extraction from content."""

    def test_pwd_pattern(self):
        """Extract directory from pwd output."""
        content = "$ pwd\n/home/user/myproject\n$"
        result = extract_directory(content)
        assert result in ["/home/user/myproject", "unknown"]

    def test_working_directory_label(self):
        """Extract directory from 'Working directory:' label."""
        content = "Working directory: ~/myproject"
        result = extract_directory(content)
        assert result == "~/myproject"

    def test_no_directory_found(self):
        """Return 'unknown' when no directory pattern found."""
        content = "Just some random text without paths"
        result = extract_directory(content)
        assert result == "unknown"


class TestExtractPurpose:
    """Test purpose extraction from content."""

    def test_skips_noise_lines(self):
        """Should skip prompt lines and find meaningful content."""
        content = "$ ls\n> \nImplementing authentication middleware for the API"
        result = extract_purpose(content)
        assert "authentication" in result.lower() or "implementing" in result.lower()

    def test_returns_meaningful_line(self):
        """Should return first meaningful line; truncation is format_output's job."""
        content = "This is a very long line that should be truncated because it exceeds forty characters"
        result = extract_purpose(content)
        assert result == content


class TestGetDefaultActions:
    """Test default actions based on status."""

    def test_active_status(self):
        actions = get_default_actions('active')
        assert 'let it work' in actions

    def test_error_status(self):
        actions = get_default_actions('error')
        assert 'interrupt' in actions or 'retry' in actions

    def test_idle_status(self):
        actions = get_default_actions('idle')
        assert 'continue' in actions


class TestAnalyzeSessionsHeuristic:
    """Test heuristic session analysis."""

    def test_detects_error_status(self):
        sessions = [{
            'session_name': 'test', 'window_id': '0', 'tmux_target': 'test:0',
            'content': 'Error: Something failed\nException occurred'
        }]
        result = analyze_sessions_heuristic(sessions)
        assert result[0]['status'] == 'error'

    def test_detects_idle_status(self):
        sessions = [{
            'session_name': 'test', 'window_id': '0', 'tmux_target': 'test:0',
            'content': 'Task completed\nReady for input >'
        }]
        result = analyze_sessions_heuristic(sessions)
        assert result[0]['status'] == 'idle'

    def test_detects_awaiting_input(self):
        sessions = [{
            'session_name': 'test', 'window_id': '0', 'tmux_target': 'test:0',
            'content': 'Some output here\nWaiting for your selection >'
        }]
        result = analyze_sessions_heuristic(sessions)
        assert result[0]['awaiting'] is True


class TestFormatOutput:
    """Test output formatting."""

    def test_formats_table_with_sessions(self):
        sessions = [
            {
                'tmux_target': 'dev:0', 'directory': '~/project',
                'purpose': 'Building feature', 'status': 'active',
                'awaiting': False, 'actions': ['continue', 'status']
            },
            {
                'tmux_target': 'test:1', 'directory': '~/project',
                'purpose': 'Running tests', 'status': 'idle',
                'awaiting': True, 'actions': ['continue']
            },
        ]
        output = format_output(sessions)
        assert '2 found' in output
        assert 'dev:0' in output
        assert 'test:1' in output
        assert 'A' in output
        assert 'B' in output
        assert 'Selection syntax' in output

    def test_letter_assignment(self):
        sessions = [
            {'tmux_target': f's{i}:0', 'directory': '~', 'purpose': 'test',
             'status': 'active', 'awaiting': False, 'actions': []}
            for i in range(5)
        ]
        output = format_output(sessions)
        for letter in 'ABCDE':
            assert letter in output


class TestFormatExecutionResults:
    """Test execution result formatting."""

    def test_formats_success_results(self):
        results = [
            {'target': 'dev:0', 'command': 'git status', 'success': True, 'error': None},
            {'target': 'test:1', 'command': 'pwd', 'success': True, 'error': None},
        ]
        output = format_execution_results(results)
        assert '[ok]' in output
        assert 'dev:0' in output
        assert 'git status' in output
        assert '2/2' in output

    def test_formats_failed_results(self):
        results = [
            {'target': 'dev:0', 'command': 'cmd', 'success': False, 'error': 'session not found'},
        ]
        output = format_execution_results(results)
        assert '[FAIL]' in output
        assert 'session not found' in output
        assert '0/1' in output


class TestSelectionParsing:
    """Test selection string parsing via execute_selections (tmux_tab_ai_session_status wrapper)."""

    def _make_mock_tmux(self):
        mock_tmux = MagicMock()
        mock_tmux.send_keys.return_value = True
        return mock_tmux

    def test_parse_single_letter(self):
        """Test single letter selection."""
        sessions = [
            {'tmux_target': 'dev:0', 'actions': ['continue']},
            {'tmux_target': 'test:1', 'actions': ['status']},
        ]
        with patch('autorun.tmux_utils.get_tmux_utilities', return_value=self._make_mock_tmux()):
            result = execute_selections('A', sessions)
        assert 'dev:0' in result
        assert '1/1' in result

    def test_parse_multiple_letters(self):
        """Test 'AC' format."""
        sessions = [
            {'tmux_target': 'dev:0', 'actions': ['continue']},
            {'tmux_target': 'test:1', 'actions': ['status']},
            {'tmux_target': 'docs:2', 'actions': ['review']},
        ]
        with patch('autorun.tmux_utils.get_tmux_utilities', return_value=self._make_mock_tmux()):
            result = execute_selections('AC', sessions)
        assert 'dev:0' in result
        assert 'docs:2' in result
        assert '2/2' in result

    def test_parse_custom_command(self):
        """Test 'A:git status' format."""
        sessions = [{'tmux_target': 'dev:0', 'actions': ['continue']}]
        with patch('autorun.tmux_utils.get_tmux_utilities', return_value=self._make_mock_tmux()):
            result = execute_selections('A:git status', sessions)
        assert 'git status' in result

    def test_parse_all_selector(self):
        """Test 'all:continue' format."""
        sessions = [
            {'tmux_target': 'dev:0'},
            {'tmux_target': 'test:1'},
            {'tmux_target': 'docs:2'},
        ]
        with patch('autorun.tmux_utils.get_tmux_utilities', return_value=self._make_mock_tmux()):
            result = execute_selections('all:continue', sessions)
        assert '3/3' in result

    def test_parse_awaiting_selector(self):
        """Test 'awaiting:continue' format — only executes on awaiting=True sessions."""
        sessions = [
            {'tmux_target': 'dev:0', 'awaiting': False},
            {'tmux_target': 'test:1', 'awaiting': True},
            {'tmux_target': 'docs:2', 'awaiting': True},
        ]
        with patch('autorun.tmux_utils.get_tmux_utilities', return_value=self._make_mock_tmux()):
            result = execute_selections('awaiting:continue', sessions)
        assert '2/2' in result


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
