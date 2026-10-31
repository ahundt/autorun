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
                Mock(returncode=0, stdout='/bin/bash\n'),  # ps -o comm=
                Mock(returncode=0, stdout='bash\n'),       # ps -o args=
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


def _fake_process_table(children, procs):
    """subprocess.run stand-in for pgrep -P and ps -p, keyed on the arguments.

    children maps a parent pid to its child pids; procs maps a pid to
    (comm, args). pgrep rejects a ppid list that is not comma-separated
    digits with exit 2, as the real macOS and procps pgrep do.
    """
    import re

    def run(cmd, **kwargs):
        if cmd[0] == 'pgrep':
            if not re.fullmatch(r'\d+(,\d+)*', cmd[2]):
                return Mock(returncode=2, stdout='')
            kids = [k for p in cmd[2].split(',') for k in children.get(p, [])]
            return Mock(returncode=0 if kids else 1, stdout=''.join(f'{k}\n' for k in kids))
        if cmd[0] == 'ps':
            comm, args = procs[cmd[2]]
            return Mock(returncode=0, stdout=(comm if cmd[4] == 'comm=' else args) + '\n')
        raise AssertionError(f'unexpected command {cmd}')
    return run


class TestIsClaudeSessionMatchesTheProcessNotItsArguments:
    """is_claude_session passed list-panes output ('111\\n222') to pgrep -P
    whole, so a split window was never detected, and it matched 'claude' as a
    substring of the full command line, so `vim ~/.claude/settings.json`
    counted as Claude and /ar:tabs all:continue typed into the editor."""

    def _detect(self, pane_pids, children, procs):
        tmux = TmuxUtilities(session_name="test-session")
        with patch.object(tmux, 'execute_tmux_command',
                          return_value={'returncode': 0, 'stdout': pane_pids}), \
                patch('autorun.tmux_utils.subprocess.run',
                      side_effect=_fake_process_table(children, procs)):
            return tmux.is_claude_session('test-session', '0')

    def test_claude_in_the_second_pane_of_a_split_window_is_detected(self):
        assert self._detect(
            '111\n222\n',
            {'222': ['333']},
            {'333': ('/usr/local/bin/claude', 'claude')},
        ) is True

    @pytest.mark.parametrize('comm,args', [
        ('/usr/bin/vim', 'vim /Users/u/.claude/settings.json'),
        ('/usr/bin/python3', 'python3 -m autorun --home /Users/u/.claude/autorun'),
        ('/usr/bin/less', 'less claude'),
    ])
    def test_a_command_line_that_only_mentions_claude_is_not_claude(self, comm, args):
        assert self._detect('111\n', {'111': ['333']}, {'333': (comm, args)}) is False

    def test_node_running_the_claude_script_is_detected(self):
        assert self._detect(
            '111\n',
            {'111': ['333']},
            {'333': ('/usr/local/bin/node', 'node /usr/local/bin/claude --resume')},
        ) is True


@pytest.mark.tmux
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
            'content': 'Task completed\n> '
        }]
        result = analyze_sessions_heuristic(sessions)
        assert result[0]['status'] == 'idle'

    def test_detects_awaiting_input(self):
        sessions = [{
            'session_name': 'test', 'window_id': '0', 'tmux_target': 'test:0',
            'content': 'Some output here\nWould you like to proceed?\n❯ 1. Yes\n  2. No\n'
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
            {'tmux_target': 'docs:2', 'actions': ['continue']},
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


class TestSelectionsSendOnlyWhatWasMeant:
    """A word without a colon was read as letters ('awaiting' -> A,W,A,I,...,
    'all' -> A,L,L), the default action typed was a human label such as
    'let it work', and send_to_session skipped send_text_and_enter's delay
    and ignored a failed Enter."""

    def _sessions(self, n, actions=('continue',)):
        return [{'tmux_target': f's:{i}', 'actions': list(actions)} for i in range(n)]

    def _tmux(self, *send_results):
        tmux = MagicMock()
        tmux.send_keys.side_effect = list(send_results) if send_results else None
        tmux.send_keys.return_value = True
        return tmux

    @pytest.mark.parametrize('selection,count', [('awaiting', 3), ('all', 12), ('AA', 3), ('AD', 3)])
    def test_a_letter_group_that_is_not_distinct_sessions_sends_nothing(self, selection, count):
        tmux = self._tmux()
        with patch('time.sleep'):
            results = execute_session_selections(selection, self._sessions(count), tmux)

        tmux.send_keys.assert_not_called()
        assert results and not any(r['success'] for r in results), results

    def test_an_advice_label_is_refused_not_typed(self):
        tmux = self._tmux()
        with patch('time.sleep'):
            results = execute_session_selections(
                'A', self._sessions(1, ('let it work', 'status')), tmux)

        tmux.send_keys.assert_not_called()
        assert results[0]['success'] is False and 'let it work' in results[0]['error']

    def test_interrupt_sends_escape_not_the_word(self):
        tmux = self._tmux()
        with patch('time.sleep'):
            results = execute_session_selections(
                'A', self._sessions(1, ('interrupt', 'retry')), tmux)

        assert [c.args[0] for c in tmux.send_keys.call_args_list] == ['Escape']
        assert results[0]['success'] is True

    def test_continue_is_typed_then_enter_after_a_delay(self):
        tmux = self._tmux()
        with patch('time.sleep') as sleep:
            execute_session_selections('A', self._sessions(1), tmux)

        assert [c.args[0] for c in tmux.send_keys.call_args_list] == ['continue', 'C-m']
        sleep.assert_called()

    def test_send_to_session_reports_a_failed_enter(self):
        tmux = self._tmux(True, False)
        with patch('time.sleep'):
            result = send_to_session(tmux, {'tmux_target': 's:0'}, 'continue')

        assert result['success'] is False

    def test_execute_mode_reads_bare_all_with_a_command_as_every_session(self, capsys):
        import io
        import json
        from autorun import tmux_tab_ai_session_status as status

        tmux = self._tmux()
        stdin = io.StringIO(json.dumps({
            'selections': 'all', 'command': 'continue', 'sessions': self._sessions(3)}))
        with patch('sys.stdin', stdin), patch('time.sleep'), \
                patch('autorun.tmux_utils.get_tmux_utilities', return_value=tmux):
            status.handle_execute_mode()

        assert '3/3' in capsys.readouterr().out


if __name__ == '__main__':
    pytest.main([__file__, '-v'])


def test_discovery_calls_tmux_list_windows_with_its_real_signature():
    """discover_claude_sessions passed include_git=True, which tmux_list_windows
    does not accept, so every /ar:tabs discovery raised TypeError. The
    autospec enforces the real signature, which a bare Mock would not."""
    from unittest.mock import create_autospec

    from autorun import tmux_utils

    listing = create_autospec(tmux_utils.tmux_list_windows, return_value=[
        {"session": "main", "w": 1, "content": "> ", "title": "t", "cmd": "claude"},
    ])
    tmux = Mock()
    tmux.is_claude_session.return_value = True
    with patch.object(tmux_utils, "tmux_list_windows", listing):
        sessions = discover_claude_sessions(tmux)

    assert [s["tmux_target"] for s in sessions] == ["main:1"]


class TestTabsUsesThePromptDetector:
    """analyze_sessions_heuristic had its own idea of waiting: content ending
    in '>' or containing 'waiting for'/'your selection'. A generating window
    that printed 'waiting for build' was awaiting; Claude Code idle at its
    '❯' prompt, with status lines below it, was not. discover_claude_sessions
    also dropped the prompt_type and is_active that tmux_list_windows computed."""

    IDLE = (
        "⏺ All tests pass.\n\n"
        "────────────────────────────────────────\n"
        "❯ \n"
        "────────────────────────────────────────\n"
        "  ? for shortcuts\n"
    )
    GENERATING = (
        "Started the build, waiting for build to finish\n"
        "✳ Building… (esc to interrupt · 7s · ↓ 44 tokens)\n"
    )

    def _analyze(self, **session):
        return analyze_sessions_heuristic([{'tmux_target': 's:0', **session}])[0]

    def test_a_generating_window_that_mentions_waiting_is_not_awaiting(self):
        result = self._analyze(content=self.GENERATING)
        assert (result['awaiting'], result['status']) == (False, 'active')

    def test_claude_idle_at_the_chevron_prompt_is_awaiting(self):
        result = self._analyze(content=self.IDLE)
        assert (result['awaiting'], result['status']) == (True, 'idle')

    def test_discovery_carries_prompt_type_and_is_active_into_the_analysis(self):
        from unittest.mock import create_autospec

        from autorun import tmux_utils

        listing = create_autospec(tmux_utils.tmux_list_windows, return_value=[
            {'session': 'main', 'w': 1, 'content': 'plain text', 'title': 't',
             'prompt_type': 'input', 'is_active': False},
        ])
        tmux = Mock()
        tmux.is_claude_session.return_value = True
        with patch.object(tmux_utils, 'tmux_list_windows', listing):
            session = discover_claude_sessions(tmux)[0]

        assert (session['prompt_type'], session['is_active']) == ('input', False)
        assert analyze_sessions_heuristic([session])[0]['awaiting'] is True
