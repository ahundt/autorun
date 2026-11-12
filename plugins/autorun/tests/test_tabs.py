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

import tmux_screens
from autorun.platforms import PLATFORMS
from autorun.tmux_utils import (
    TmuxUtilities,
    discover_claude_sessions,
    execute_session_selections,
    TypedNotSubmitted,
    pane_prompt_type,
    send_text_and_enter,
    send_to_session,
    tmux_detect_claude_active,
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
            mock_tmux.return_value = {'returncode': 0, 'stdout': '0 12345'}
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
            mock_tmux.return_value = {'returncode': 0, 'stdout': '0 12345'}
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
            mock_tmux.return_value = {'returncode': 0, 'stdout': '0 12345'}
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
            mock_tmux.return_value = {'returncode': 0, 'stdout': '0 12345'}
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
            '0 111\n1 222\n',
            {'222': ['333']},
            {'111': ('-zsh', '-zsh'), '333': ('/usr/local/bin/claude', 'claude')},
        ) is True

    @pytest.mark.parametrize('comm,args', [
        ('/usr/bin/vim', 'vim /Users/u/.claude/settings.json'),
        ('/usr/bin/python3', 'python3 -m autorun --home /Users/u/.claude/autorun'),
        ('/usr/bin/less', 'less claude'),
    ])
    def test_a_command_line_that_only_mentions_claude_is_not_claude(self, comm, args):
        assert self._detect('0 111\n', {'111': ['333']}, {'333': (comm, args)}) is False

    def test_each_pane_running_a_harness_is_reported_with_its_index(self):
        """A send must reach the pane that runs the harness, not the window's
        active pane, so discovery reports which pane matched."""
        tmux = TmuxUtilities(session_name="test-session")
        procs = {'111': ('-zsh', '-zsh'), '333': ('/usr/local/bin/claude', 'claude'),
                 '222': ('/usr/local/bin/codex', 'codex')}
        with patch.object(tmux, 'execute_tmux_command',
                          return_value={'returncode': 0, 'stdout': '0 111\n1 222\n2 444\n'}), \
                patch('autorun.tmux_utils.subprocess.run',
                      side_effect=_fake_process_table({'111': ['333']}, {**procs, '444': ('/bin/sh', 'sh')})):
            assert tmux.pane_harnesses('test-session', '0') == [('0', 'claude'), ('1', 'codex')]

    def test_node_running_the_claude_script_is_detected(self):
        assert self._detect(
            '0 111\n',
            {'111': ['333']},
            {'333': ('/usr/local/bin/node', 'node /usr/local/bin/claude --resume')},
        ) is True


class TestDiscoveryFindsEveryRegisteredHarness:
    """Discovery matched only {'claude', 'happy', 'happy-dev'}, so on a live
    server with 10 Pi, 6 Codex and 2 Claude Code panes it found 2 of 18. The
    process names now come from the platform registry, and each pane records
    which harness it runs."""

    def _harness(self, procs):
        tmux = TmuxUtilities(session_name="test-session")
        with patch.object(tmux, 'execute_tmux_command',
                          return_value={'returncode': 0, 'stdout': '0 111\n'}), \
                patch('autorun.tmux_utils.subprocess.run',
                      side_effect=_fake_process_table({'111': ['333']}, {'333': procs})):
            return (tmux.pane_harness('test-session', '0'), tmux.is_ai_session('test-session', '0'),
                    tmux.is_claude_session('test-session', '0'))

    @pytest.mark.parametrize('name', sorted(PLATFORMS))
    def test_each_registered_binary_is_found_and_labelled(self, name):
        binary = PLATFORMS[name].binary
        # A Codex pane is an AI session, never a Claude Code session.
        assert self._harness((f'/usr/local/bin/{binary}', f'{binary} --resume x')) == (
            name, True, name == 'claude')

    @pytest.mark.parametrize('name', sorted(PLATFORMS))
    def test_every_process_name_comes_from_the_registry(self, name):
        """Extra executable names live on the Platform entry (process_names),
        not in a separate table: only Claude Code has any."""
        from autorun.client import harness_process_names

        platform = PLATFORMS[name]
        expected = ('claude-code', 'happy', 'happy-dev') if name == 'claude' else ()
        assert platform.process_names == expected
        names = harness_process_names()
        assert all(names[n] == name for n in (platform.name, platform.binary, *platform.process_names))
        assert set(names) == {n for p in PLATFORMS.values() for n in (p.name, p.binary, *p.process_names)}

    @pytest.mark.parametrize('shape', ['pane process', 'child of a shell'])
    @pytest.mark.parametrize('name', sorted(PLATFORMS))
    def test_found_as_the_pane_process_or_as_its_child(self, name, shape):
        """`tmux new-window codex` makes the harness the pane's own process,
        with no shell above it; discovery looked only at the pane's children."""
        binary = PLATFORMS[name].binary
        proc = (f'/usr/local/bin/{binary}', f'{binary} --resume x')
        if shape == 'pane process':
            children, procs = {}, {'111': proc}
        else:
            children, procs = {'111': ['333']}, {'111': ('-zsh', '-zsh'), '333': proc}
        tmux = TmuxUtilities(session_name="test-session")
        with patch.object(tmux, 'execute_tmux_command',
                          return_value={'returncode': 0, 'stdout': '0 111\n'}), \
                patch('autorun.tmux_utils.subprocess.run',
                      side_effect=_fake_process_table(children, procs)):
            assert tmux.pane_harness('test-session', '0') == name

    @pytest.mark.parametrize('harness', [None, *sorted(PLATFORMS)])
    def test_window_fields_keep_claude_apart_from_other_harnesses(self, harness):
        from autorun.tmux_utils import _harness_fields

        assert _harness_fields(harness) == {
            'harness': harness,
            'is_ai_session': harness is not None,
            'is_claude_session': harness == 'claude',
        }

    @pytest.mark.parametrize('comm,args,harness', [
        # Process shapes from the live tmux server on 2026-11-11 (paths are placeholders).
        ('/opt/homebrew/bin/node', 'node /Users/u/.npm-global/bin/pi', 'pi'),
        ('pi', 'pi', 'pi'),
        ('codex', 'codex --yolo resume 019ff3de-1728-76f3-ac72-b0b2691bdb4f', 'codex'),
        ('prime-agent', 'prime-agent', 'prime'),
        ('agy', 'agy', 'antigravity'),
        # Claude Code's native install runs a binary named for its version.
        ('/Users/u/.local/share/claude/versions/2.1.37', 'claude --dangerously-skip-permissions', 'claude'),
        ('/usr/local/bin/happy', 'happy', 'claude'),
        ('/usr/local/bin/happy-dev', 'happy-dev', 'claude'),
    ])
    def test_recorded_process_table(self, comm, args, harness):
        assert self._harness((comm, args)) == (harness, True, harness == 'claude')

    def test_a_shell_is_no_harness(self):
        assert self._harness(('/bin/zsh', '-zsh')) == (None, False, False)

    def test_discovery_carries_the_harness(self):
        from unittest.mock import create_autospec

        from autorun import tmux_utils

        listing = create_autospec(tmux_utils.tmux_list_windows, return_value=[
            {'session': 'main', 'w': 1, 'content': '', 'title': 't'},
        ])
        tmux = Mock()
        tmux.pane_harnesses.return_value = [('0', 'pi')]
        with patch.object(tmux_utils, 'tmux_list_windows', listing):
            assert discover_claude_sessions(tmux)[0]['harness'] == 'pi'


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
            'content': tmux_screens.READY,
        }]
        result = analyze_sessions_heuristic(sessions)
        assert result[0]['awaiting'] is True

    def test_a_plan_approval_dialog_is_not_awaiting_input(self):
        """Enter there accepts the highlighted option, so typed text would answer it."""
        sessions = [{
            'session_name': 'test', 'window_id': '0', 'tmux_target': 'test:0',
            'content': 'Some output here\nWould you like to proceed?\n❯ 1. Yes\n  2. No\n'
        }]
        result = analyze_sessions_heuristic(sessions)
        assert (result[0]['awaiting'], result[0]['status']) == (False, 'dialog')


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

    @pytest.fixture(autouse=True)
    def _pane_reads_back_submitted(self):
        """The pane read after Enter shows an empty input line: submitted."""
        with patch('autorun.tmux_utils._text_on_cursor_line', return_value=False):
            yield

    def _make_mock_tmux(self):
        mock_tmux = MagicMock()
        mock_tmux.execute_tmux_command.return_value = {'returncode': 0, 'stdout': tmux_screens.READY}
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
            {'tmux_target': 'dev:0', 'awaiting': False, 'prompt_type': 'question'},
            {'tmux_target': 'test:1', 'awaiting': True, 'prompt_type': 'input'},
            {'tmux_target': 'docs:2', 'awaiting': True, 'prompt_type': 'input'},
        ]
        with patch('autorun.tmux_utils.get_tmux_utilities', return_value=self._make_mock_tmux()):
            result = execute_selections('awaiting:continue', sessions)
        assert '2/2' in result


class TestSelectionsSendOnlyWhatWasMeant:
    """A word without a colon was read as letters ('awaiting' -> A,W,A,I,...,
    'all' -> A,L,L), the default action typed was a human label such as
    'let it work', and send_to_session skipped send_text_and_enter's delay
    and ignored a failed Enter."""

    @pytest.fixture(autouse=True)
    def _pane_reads_back_submitted(self):
        """The pane read after Enter shows an empty input line: submitted."""
        with patch('autorun.tmux_utils._text_on_cursor_line', return_value=False):
            yield

    def _sessions(self, n, actions=('continue',)):
        return [{'tmux_target': f's:{i}', 'actions': list(actions)} for i in range(n)]

    def _tmux(self, *send_results):
        tmux = MagicMock()
        tmux.execute_tmux_command.return_value = {'returncode': 0, 'stdout': tmux_screens.READY}
        tmux.send_literal.return_value = True
        tmux.send_keys.side_effect = list(send_results) if send_results else None
        tmux.send_keys.return_value = True
        return tmux

    @staticmethod
    def _typed(tmux):
        """Each send in order: ('text', ...) typed literally, ('key', ...) a key press."""
        kinds = {'send_literal': 'text', 'send_keys': 'key'}
        return [(kinds[name], args[0]) for name, args, _ in tmux.method_calls if name in kinds]

    @pytest.mark.parametrize('selection,count', [('awaiting', 3), ('all', 12), ('AA', 3), ('AD', 3)])
    def test_a_letter_group_that_is_not_distinct_sessions_sends_nothing(self, selection, count):
        tmux = self._tmux()
        with patch('time.sleep'):
            results = execute_session_selections(selection, self._sessions(count), tmux)

        tmux.send_keys.assert_not_called()
        tmux.send_literal.assert_not_called()
        assert results and not any(r['success'] for r in results), results

    @pytest.mark.parametrize('selection,command', [
        ('all:', None), ('all:   ', None), ('awaiting:', None), ('A:', None),
        ('A: ', None), ('A:pwd, B:', None), ('all', '  '), ('A,B', '  '), ('A', ''),
    ])
    def test_an_empty_command_is_refused_and_nothing_is_sent(self, selection, command):
        """'all:' sent a bare Enter to every session, 'A:' to one."""
        tmux = self._tmux()
        sessions = [dict(s, awaiting=True) for s in self._sessions(2)]
        with patch('time.sleep'):
            results = execute_session_selections(selection, sessions, tmux, command=command)

        tmux.send_keys.assert_not_called()
        tmux.send_literal.assert_not_called()
        assert results and not any(r['success'] for r in results), results
        for syntax in ('A:<command>', 'all:<command>', 'awaiting:<command>'):
            assert all(syntax in r['error'] for r in results), results

    def test_an_advice_label_is_refused_not_typed(self):
        tmux = self._tmux()
        with patch('time.sleep'):
            results = execute_session_selections(
                'A', self._sessions(1, ('let it work', 'status')), tmux)

        tmux.send_keys.assert_not_called()
        tmux.send_literal.assert_not_called()
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

        assert self._typed(tmux) == [('text', 'continue'), ('key', 'C-m')]
        sleep.assert_called()

    def test_send_to_session_reports_a_failed_enter(self):
        tmux = self._tmux(False)
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

    def test_execute_mode_refuses_all_with_an_empty_command(self, capsys):
        import io
        import json
        from autorun import tmux_tab_ai_session_status as status

        tmux = self._tmux()
        stdin = io.StringIO(json.dumps({'selections': 'all: ', 'sessions': self._sessions(3)}))
        with patch('sys.stdin', stdin), patch('time.sleep'), \
                patch('autorun.tmux_utils.get_tmux_utilities', return_value=tmux):
            status.handle_execute_mode()

        out = capsys.readouterr().out
        tmux.send_keys.assert_not_called()
        tmux.send_literal.assert_not_called()
        assert 'empty command' in out and 'all:<command>' in out and '0/1' in out, out


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
    tmux.pane_harnesses.return_value = [("0", "claude")]
    with patch.object(tmux_utils, "tmux_list_windows", listing):
        sessions = discover_claude_sessions(tmux)

    assert [s["tmux_target"] for s in sessions] == ["main:1.0"]


def test_discovery_lists_each_harness_pane_as_its_own_target():
    """Two harness panes in one window are two entries, each targeted at its
    own pane; the window's other panes are not entries."""
    from unittest.mock import create_autospec

    from autorun import tmux_utils

    listing = create_autospec(tmux_utils.tmux_list_windows, return_value=[
        {"session": "main", "w": 1, "content": "", "title": "t", "cmd": "zsh"},
    ])
    tmux = Mock()
    tmux.pane_harnesses.return_value = [("1", "codex"), ("2", "pi")]
    with patch.object(tmux_utils, "tmux_list_windows", listing):
        sessions = discover_claude_sessions(tmux)

    assert [(s["tmux_target"], s["pane"], s["harness"]) for s in sessions] == [
        ("main:1.1", "1", "codex"), ("main:1.2", "2", "pi")]


class TestTabsUsesThePromptDetector:
    """analyze_sessions_heuristic had its own idea of waiting: content ending
    in '>' or containing 'waiting for'/'your selection'. A generating window
    that printed 'waiting for build' was awaiting; Claude Code idle at its
    '❯' prompt, with status lines below it, was not. discover_claude_sessions
    also dropped the prompt_type and is_active that tmux_list_windows computed;
    discovery now reads both from the capture of the pane that runs the harness."""

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
            {'session': 'main', 'w': 1, 'content': 'plain text', 'title': 't'},
        ])
        tmux = Mock()
        tmux.pane_harnesses.return_value = [('1', 'claude')]
        tmux.execute_tmux_command.side_effect = lambda cmd, *a, **k: {
            'returncode': 0, 'stdout': self.IDLE if cmd[0] == 'capture-pane' else '/w\n'}
        with patch.object(tmux_utils, 'tmux_list_windows', listing):
            session = discover_claude_sessions(tmux)[0]

        assert (session['prompt_type'], session['is_active']) == ('input', False)
        assert analyze_sessions_heuristic([session])[0]['awaiting'] is True


class TestPromptStateOfHarnessesAutorunCannotRead:
    """tmux_detect_prompt_type reads Claude Code's prompts only, so for any
    other harness "awaiting input" could not be told from "working". Such a
    pane's prompt state is 'unknown', and an awaiting selection skips it, so
    a batch send never types into a harness in mid-turn."""

    IDLE = TestTabsUsesThePromptDetector.IDLE

    @pytest.mark.parametrize('name', sorted(PLATFORMS))
    def test_prompt_state_is_read_only_where_the_registry_says_it_can_be(self, name):
        from autorun.tmux_utils import pane_prompt_type

        readable = PLATFORMS[name].tmux_prompt_state_readable
        assert pane_prompt_type(self.IDLE, name) == ('input' if readable else 'unknown')

    def test_only_claude_code_prompts_are_readable(self):
        assert {n for n, p in PLATFORMS.items() if p.tmux_prompt_state_readable} == {'claude'}

    def test_a_pane_running_no_harness_keeps_the_detector(self):
        from autorun.tmux_utils import pane_prompt_type

        assert pane_prompt_type(self.IDLE, None) == 'input'

    def test_an_unknown_prompt_state_is_not_awaiting(self):
        session = {'tmux_target': 's:0', 'content': self.IDLE, 'prompt_type': 'unknown', 'is_active': False}
        result = analyze_sessions_heuristic([session])[0]
        assert (result['awaiting'], result['status']) == (False, 'unknown')
        assert 'continue' not in result['actions']

    @pytest.mark.parametrize('word', ['awaiting', 'awaiting-input', 'AWAITING-INPUT'])
    def test_an_awaiting_selection_skips_a_pane_whose_state_is_unknown(self, word):
        tmux = MagicMock()
        tmux.send_keys.return_value = True
        sessions = [
            {'tmux_target': 'claude:0', 'awaiting': True, 'prompt_type': 'input'},
            {'tmux_target': 'pi:1', 'awaiting': True, 'prompt_type': 'unknown'},
        ]
        with patch('time.sleep'):
            results = execute_session_selections(f'{word}:continue', sessions, tmux)

        assert [r['target'] for r in results] == ['claude:0']

    def test_bare_awaiting_input_takes_the_command(self):
        tmux = MagicMock()
        tmux.send_keys.return_value = True
        sessions = [{'tmux_target': 'claude:0', 'awaiting': True, 'prompt_type': 'input'}]
        with patch('time.sleep'):
            results = execute_session_selections('awaiting-input', sessions, tmux, command='go on')

        assert [(r['target'], r['command']) for r in results] == [('claude:0', 'go on')]


@pytest.mark.tmux
class TestSendIsVerifiedOnThePrivateServer:
    """send_text_and_enter returned the exit status of its two send-keys
    calls, so text left sitting on the input line was reported as sent. It
    now reads the pane's cursor line after Enter: text still there is
    reported as 'typed, not submitted', a failure, and is never resent."""

    @pytest.fixture
    def pane(self):
        import os
        import shutil
        import subprocess
        import uuid

        # Never the user's own server: conftest.py sets this with its private socket.
        assert os.environ.get("AUTORUN_TEST_TMUX_SOCKET") and shutil.which("tmux"), \
            "tmux-marked tests need the suite's private tmux server"
        name = f"autorun-verify-{uuid.uuid4().hex[:8]}"
        subprocess.run(["tmux", "new-session", "-d", "-s", name, "-x", "80", "-y", "24", "/bin/sh"],
                       check=True, capture_output=True)
        try:
            yield name
        finally:
            subprocess.run(["tmux", "kill-session", "-t", name], check=False, capture_output=True)

    @staticmethod
    def _send(tmux, pane, text):
        """(sent, error) from send_text_and_enter, the layer that verifies.
        These panes run a shell, not a harness, so the prompt check that
        send_to_session adds in front would refuse them; it is tested in
        TestNoSendTypesIntoADialogOrAMidTurnPane."""
        try:
            return send_text_and_enter(tmux, text, pane, '0'), None
        except TypedNotSubmitted as e:
            return False, str(e)

    def _wait_for_prompt(self, tmux, name):
        import time

        for _ in range(50):
            out = tmux.execute_tmux_command(['capture-pane', '-p'], name, '0')
            if out and out['stdout'].strip():
                return
            time.sleep(0.1)

    def test_text_left_on_the_input_line_is_reported_and_not_resent(self, pane):
        tmux = TmuxUtilities(session_name=pane)
        self._wait_for_prompt(tmux, pane)
        real_type = tmux.send_literal
        sent = []

        def lose_enter(keys, *args, **kwargs):
            sent.append(keys)
            return True

        def type_text(text, *args, **kwargs):
            sent.append(text)
            return real_type(text, *args, **kwargs)

        tmux.send_keys, tmux.send_literal = lose_enter, type_text
        result = self._send(tmux, pane, ': autorun-marker-1')

        assert result == (False, 'typed, not submitted')
        assert sent == [': autorun-marker-1', 'C-m']

    def test_text_echoed_above_an_empty_input_line_is_reported_as_sent(self, pane):
        """The sent text stays on screen as the shell's echo and output, but the
        cursor line is a fresh empty prompt, so the send is a success."""
        tmux = TmuxUtilities(session_name=pane)
        self._wait_for_prompt(tmux, pane)

        result = self._send(tmux, pane, 'echo autorun-marker-3')
        screen = tmux.execute_tmux_command(['capture-pane', '-p'], pane, '0')['stdout']

        assert result == (True, None)
        assert screen.count('autorun-marker-3') >= 2, screen

    def test_submitted_text_is_reported_as_sent(self, pane):
        tmux = TmuxUtilities(session_name=pane)
        self._wait_for_prompt(tmux, pane)

        result = self._send(tmux, pane, ': autorun-marker-2')

        assert result == (True, None)

    @pytest.mark.parametrize('text', ['C-c', '-R', 'Up', 'Escape', 'a;b', 'ends;', 'back\\;'])
    def test_text_that_reads_as_a_key_name_or_flag_arrives_as_those_characters(self, pane, text):
        """`tmux send-keys <text>` read `C-c` as Ctrl-C (killing the program),
        `Up` as the arrow key, `-R` as its reset-terminal flag, and a trailing
        `;` as a tmux command separator. A pane running `cat` echoes each line
        back, so the text must appear verbatim, typed and then printed."""
        import subprocess

        subprocess.run(["tmux", "respawn-pane", "-k", "-t", f"{pane}:0", "cat"],
                       check=True, capture_output=True)
        tmux = TmuxUtilities(session_name=pane)

        result = self._send(tmux, pane, text)
        screen = tmux.execute_tmux_command(['capture-pane', '-p'], pane, '0')['stdout']

        assert result == (True, None), (result, screen)
        assert screen.split('\n')[:2] == [text, text], screen


class TestNoSendTypesIntoADialogOrAMidTurnPane:
    """`awaiting-input` counted any recognised prompt as waiting, so `send`
    typed text and Enter into Claude Code's permission dialog, where Enter
    accepts the highlighted "1. Yes", and into plan approvals and question
    menus; `all` and letters typed into them too, and into a pane mid-turn.
    Every text send now reads the pane just before typing and goes through
    check_safe_to_send; a pane not at its plain input prompt is refused by
    letter with the reason, and nothing is typed into it."""

    READABLE = sorted(n for n, p in PLATFORMS.items() if p.tmux_prompt_state_readable)

    @staticmethod
    def _tmux(screen):
        tmux = MagicMock()
        tmux.execute_tmux_command.return_value = {'returncode': 0, 'stdout': screen}
        tmux.send_literal.return_value = True
        tmux.send_keys.return_value = True
        return tmux

    @staticmethod
    def _session(harness, **fields):
        return {'tmux_target': 's:0.0', 'harness': harness, 'prompt_type': 'input',
                'is_active': False, 'actions': ['continue'], **fields}

    @pytest.fixture(autouse=True)
    def _pane_reads_back_submitted(self):
        with patch('autorun.tmux_utils._text_on_cursor_line', return_value=False), \
                patch('time.sleep'):
            yield

    @pytest.mark.parametrize('harness', READABLE)
    @pytest.mark.parametrize('reason', sorted(tmux_screens.NOT_READY))
    @pytest.mark.parametrize('selection,command', [
        ('A', 'continue'), ('A', None), ('A:continue', None),
        ('all', 'continue'), ('awaiting-input', 'continue')])
    def test_a_pane_not_at_its_input_prompt_when_typed_into_is_refused(
            self, harness, reason, selection, command):
        """Discovery saw the plain input prompt; by send time the pane shows
        the dialog (or is mid-turn, or has text typed), as a race would."""
        tmux = self._tmux(tmux_screens.NOT_READY[reason])

        results = execute_session_selections(selection, [self._session(harness)], tmux,
                                             command=command)

        tmux.send_literal.assert_not_called()
        tmux.send_keys.assert_not_called()
        assert [(r['letter'], r['success'], r['refused']) for r in results] == [('A', False, True)]
        assert reason in results[0]['error'], results

    @pytest.mark.parametrize('harness', READABLE)
    def test_the_same_selections_type_into_a_pane_at_its_input_prompt(self, harness):
        for selection in ('A', 'all', 'awaiting-input'):
            tmux = self._tmux(tmux_screens.READY)
            results = execute_session_selections(selection, [self._session(harness)], tmux,
                                                 command='continue')

            assert [(r['letter'], r['success']) for r in results] == [('A', True)], results
            assert tmux.send_literal.call_args.args[0] == 'continue'

    @pytest.mark.parametrize('harness', READABLE)
    @pytest.mark.parametrize('reason', tmux_screens.DIALOGS + ['active'])
    def test_awaiting_input_does_not_select_a_pane_listed_in_a_dialog_or_mid_turn(
            self, harness, reason):
        screen = tmux_screens.NOT_READY[reason]
        session = analyze_sessions_heuristic([{
            'tmux_target': 's:0.0', 'harness': harness, 'content': screen,
            'prompt_type': pane_prompt_type(screen, harness),
            'is_active': tmux_detect_claude_active(screen)}])[0]
        tmux = self._tmux(tmux_screens.READY)

        results = execute_session_selections('awaiting-input', [session], tmux, command='go')

        assert session['awaiting'] is False and results == []
        tmux.send_literal.assert_not_called()

    @pytest.mark.parametrize('harness', READABLE)
    @pytest.mark.parametrize('reason', tmux_screens.DIALOGS)
    def test_list_shows_a_dialog_pane_as_needing_the_person(self, harness, reason):
        screen = tmux_screens.NOT_READY[reason]
        session = analyze_sessions_heuristic([{
            'tmux_target': 's:0.0', 'harness': harness, 'content': screen,
            'prompt_type': pane_prompt_type(screen, harness), 'is_active': False}])[0]

        assert (session['awaiting'], session['status']) == (False, 'dialog')
        assert 'continue' not in session['actions']
        row = [line for line in format_output([session]).splitlines() if line.startswith('A ')]
        assert 'dialog' in row[0], row

    @pytest.mark.parametrize('harness', sorted(n for n, p in PLATFORMS.items()
                                              if not p.tmux_prompt_state_readable))
    def test_a_harness_autorun_cannot_read_is_refused_by_a_batch_and_sent_to_by_letter(
            self, harness):
        """Its dialogs cannot be told from its input prompt, so only a send
        that names the pane types into it."""
        for selection, sent in (('all', False), ('awaiting-input', None), ('A', True)):
            tmux = self._tmux(tmux_screens.READY)
            session = self._session(harness, prompt_type='unknown')
            results = execute_session_selections(selection, [session], tmux, command='go')

            if sent is None:
                assert results == []
            else:
                assert [(r['letter'], r['success']) for r in results] == [('A', sent)], results
                assert bool(tmux.send_literal.called) is sent
            if sent is False:
                assert results[0]['refused'] and 'cannot read' in results[0]['error'], results
