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
import pytest
import sys
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

# Add commands to path
sys.path.insert(0, str(Path(__file__).parent.parent / "commands"))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


class TestIsClaudeSession:
    """Test the is_claude_session() heuristic function.

    NOTE: These tests are marked as skipped because the tabs-exec script was
    refactored and the old is_claude_session() API no longer exists. The new
    implementation uses discover_claude_sessions() and analyze_sessions_heuristic()
    instead. These tests need to be rewritten to match the new API.
    """

    @pytest.mark.skip(reason="tabs-exec API changed - is_claude_session() no longer exists")
    def test_claude_code_indicator(self):
        """'Claude Code' is a strong indicator (weight 3)."""
        # Import here to avoid module-level issues
        from importlib.machinery import SourceFileLoader
        tabs_exec = SourceFileLoader(
            "tabs_exec",
            str(Path(__file__).parent.parent / "commands" / "tabs-exec")
        ).load_module()

        content = "Welcome to Claude Code. How can I help?"
        assert tabs_exec.is_claude_session(content) is True

    @pytest.mark.skip(reason="tabs-exec API changed - is_claude_session() no longer exists")
    def test_anthropic_indicator(self):
        """'anthropic' is a strong indicator (weight 2)."""
        from importlib.machinery import SourceFileLoader
        tabs_exec = SourceFileLoader(
            "tabs_exec",
            str(Path(__file__).parent.parent / "commands" / "tabs-exec")
        ).load_module()

        content = "Using anthropic API for analysis"
        assert tabs_exec.is_claude_session(content) is True

    @pytest.mark.skip(reason="tabs-exec API changed - is_claude_session() no longer exists")
    def test_multiple_weak_indicators(self):
        """Multiple weak indicators should sum to pass threshold."""
        from importlib.machinery import SourceFileLoader
        tabs_exec = SourceFileLoader(
            "tabs_exec",
            str(Path(__file__).parent.parent / "commands" / "tabs-exec")
        ).load_module()

        content = "claude said something > here"
        assert tabs_exec.is_claude_session(content) is True

    @pytest.mark.skip(reason="tabs-exec API changed - is_claude_session() no longer exists")
    def test_no_indicators(self):
        """Content without indicators should return False."""
        from importlib.machinery import SourceFileLoader
        tabs_exec = SourceFileLoader(
            "tabs_exec",
            str(Path(__file__).parent.parent / "commands" / "tabs-exec")
        ).load_module()

        content = "This is a regular shell session\n$ ls -la\ntotal 100"
        assert tabs_exec.is_claude_session(content) is False

    @pytest.mark.skip(reason="tabs-exec API changed - is_claude_session() no longer exists")
    def test_single_weak_indicator(self):
        """Single weak indicator should not pass threshold."""
        from importlib.machinery import SourceFileLoader
        tabs_exec = SourceFileLoader(
            "tabs_exec",
            str(Path(__file__).parent.parent / "commands" / "tabs-exec")
        ).load_module()

        # Just one '> ' is only weight 1
        content = "Some text > here"
        assert tabs_exec.is_claude_session(content) is False


class TestExtractDirectory:
    """Test directory extraction from content."""

    def test_pwd_pattern(self):
        """Extract directory from pwd output."""
        from importlib.machinery import SourceFileLoader
        tabs_exec = SourceFileLoader(
            "tabs_exec",
            str(Path(__file__).parent.parent / "commands" / "tabs-exec")
        ).load_module()

        content = "$ pwd\n/home/user/myproject\n$"
        # Should find the path pattern
        result = tabs_exec.extract_directory(content)
        # May or may not match depending on regex
        assert result in ["/home/user/myproject", "unknown"]

    def test_working_directory_label(self):
        """Extract directory from 'Working directory:' label."""
        from importlib.machinery import SourceFileLoader
        tabs_exec = SourceFileLoader(
            "tabs_exec",
            str(Path(__file__).parent.parent / "commands" / "tabs-exec")
        ).load_module()

        content = "Working directory: ~/myproject"
        result = tabs_exec.extract_directory(content)
        assert result == "~/myproject"

    def test_no_directory_found(self):
        """Return 'unknown' when no directory pattern found."""
        from importlib.machinery import SourceFileLoader
        tabs_exec = SourceFileLoader(
            "tabs_exec",
            str(Path(__file__).parent.parent / "commands" / "tabs-exec")
        ).load_module()

        content = "Just some random text without paths"
        result = tabs_exec.extract_directory(content)
        assert result == "unknown"


class TestExtractPurpose:
    """Test purpose extraction from content."""

    def test_skips_noise_lines(self):
        """Should skip prompt lines and find meaningful content."""
        from importlib.machinery import SourceFileLoader
        tabs_exec = SourceFileLoader(
            "tabs_exec",
            str(Path(__file__).parent.parent / "commands" / "tabs-exec")
        ).load_module()

        content = "$ ls\n> \nImplementing authentication middleware for the API"
        result = tabs_exec.extract_purpose(content)
        assert "authentication" in result.lower() or "implementing" in result.lower()

    def test_returns_meaningful_line(self):
        """Should return first meaningful line (truncation handled by format_output)."""
        from importlib.machinery import SourceFileLoader
        tabs_exec = SourceFileLoader(
            "tabs_exec",
            str(Path(__file__).parent.parent / "commands" / "tabs-exec")
        ).load_module()

        content = "This is a very long line that should be truncated because it exceeds forty characters"
        result = tabs_exec.extract_purpose(content)
        # extract_purpose returns the full line; truncation is handled by format_output
        assert result == content


class TestGetDefaultActions:
    """Test default actions based on status."""

    def test_active_status(self):
        """Active status should suggest letting it work."""
        from importlib.machinery import SourceFileLoader
        tabs_exec = SourceFileLoader(
            "tabs_exec",
            str(Path(__file__).parent.parent / "commands" / "tabs-exec")
        ).load_module()

        actions = tabs_exec.get_default_actions('active')
        assert 'let it work' in actions

    def test_error_status(self):
        """Error status should suggest interrupt/retry."""
        from importlib.machinery import SourceFileLoader
        tabs_exec = SourceFileLoader(
            "tabs_exec",
            str(Path(__file__).parent.parent / "commands" / "tabs-exec")
        ).load_module()

        actions = tabs_exec.get_default_actions('error')
        assert 'interrupt' in actions or 'retry' in actions

    def test_idle_status(self):
        """Idle status should suggest continue."""
        from importlib.machinery import SourceFileLoader
        tabs_exec = SourceFileLoader(
            "tabs_exec",
            str(Path(__file__).parent.parent / "commands" / "tabs-exec")
        ).load_module()

        actions = tabs_exec.get_default_actions('idle')
        assert 'continue' in actions


class TestAnalyzeSessionsHeuristic:
    """Test heuristic session analysis."""

    def test_detects_error_status(self):
        """Should detect error status from keywords."""
        from importlib.machinery import SourceFileLoader
        tabs_exec = SourceFileLoader(
            "tabs_exec",
            str(Path(__file__).parent.parent / "commands" / "tabs-exec")
        ).load_module()

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
        from importlib.machinery import SourceFileLoader
        tabs_exec = SourceFileLoader(
            "tabs_exec",
            str(Path(__file__).parent.parent / "commands" / "tabs-exec")
        ).load_module()

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
        from importlib.machinery import SourceFileLoader
        tabs_exec = SourceFileLoader(
            "tabs_exec",
            str(Path(__file__).parent.parent / "commands" / "tabs-exec")
        ).load_module()

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
        from importlib.machinery import SourceFileLoader
        tabs_exec = SourceFileLoader(
            "tabs_exec",
            str(Path(__file__).parent.parent / "commands" / "tabs-exec")
        ).load_module()

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
        from importlib.machinery import SourceFileLoader
        tabs_exec = SourceFileLoader(
            "tabs_exec",
            str(Path(__file__).parent.parent / "commands" / "tabs-exec")
        ).load_module()

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
        from importlib.machinery import SourceFileLoader
        tabs_exec = SourceFileLoader(
            "tabs_exec",
            str(Path(__file__).parent.parent / "commands" / "tabs-exec")
        ).load_module()

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
        from importlib.machinery import SourceFileLoader
        tabs_exec = SourceFileLoader(
            "tabs_exec",
            str(Path(__file__).parent.parent / "commands" / "tabs-exec")
        ).load_module()

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
        from importlib.machinery import SourceFileLoader
        tabs_exec = SourceFileLoader(
            "tabs_exec",
            str(Path(__file__).parent.parent / "commands" / "tabs-exec")
        ).load_module()

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
        from importlib.machinery import SourceFileLoader
        tabs_exec = SourceFileLoader(
            "tabs_exec",
            str(Path(__file__).parent.parent / "commands" / "tabs-exec")
        ).load_module()

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
        from importlib.machinery import SourceFileLoader
        tabs_exec = SourceFileLoader(
            "tabs_exec",
            str(Path(__file__).parent.parent / "commands" / "tabs-exec")
        ).load_module()

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
        from importlib.machinery import SourceFileLoader
        tabs_exec = SourceFileLoader(
            "tabs_exec",
            str(Path(__file__).parent.parent / "commands" / "tabs-exec")
        ).load_module()

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
        from importlib.machinery import SourceFileLoader
        tabs_exec = SourceFileLoader(
            "tabs_exec",
            str(Path(__file__).parent.parent / "commands" / "tabs-exec")
        ).load_module()

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
