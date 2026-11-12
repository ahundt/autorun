#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unit tests for tmux_utils enhanced features:
- tmux_detect_claude_thinking_mode
- WindowList helper methods (actively_generating, in_mode, claude_sessions, thinking_enabled)
- tmux_dangerous_batch_execute
- _tmux_normalize_targets
- tmux_get_claude_window_status
- tmux_get_claude_window_mode
"""
import pytest
import sys
from pathlib import Path
from unittest.mock import Mock, patch

# Add src directory to Python path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from autorun import tmux_utils as tmux_module
from autorun.tmux_utils import (
    TmuxUtilities,
    resolve_tmux_binary,
    tmux_detect_claude_thinking_mode,
    tmux_detect_claude_mode,
    tmux_detect_claude_active,
    WindowList,
    _tmux_normalize_targets,
    tmux_dangerous_batch_execute,
    tmux_get_claude_window_status,
    tmux_get_claude_window_mode,
    ACTION_SEND,
    ACTION_CONTINUE,
    ACTION_ESCAPE,
    ACTION_STOP,
    ACTION_EXIT,
    ACTION_KILL,
    ACTION_TOGGLE_THINKING,
    ACTION_CYCLE_MODE,
    ACTION_SET_MODE,
    CLAUDE_MODE_DEFAULT,
    CLAUDE_MODE_PLAN,
    CLAUDE_MODE_BYPASS,
    CLAUDE_MODE_ACCEPT_EDITS,
)


class TestTmuxBinaryResolution:
    """Tests for robust tmux client selection."""

    def teardown_method(self):
        resolve_tmux_binary.cache_clear()

    def test_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("AUTORUN_TMUX_BIN", "/custom/bin/tmux")
        resolve_tmux_binary.cache_clear()
        assert resolve_tmux_binary() == "/custom/bin/tmux"

    def test_candidate_homebrew_prefers_intel_prefix_on_genuine_intel_mac(self, monkeypatch):
        monkeypatch.delenv("AUTORUN_TMUX_BIN", raising=False)
        resolve_tmux_binary.cache_clear()

        with patch.object(tmux_module.platform, "system", return_value="Darwin"), patch.object(
            tmux_module,
            "_is_apple_silicon",
            return_value=False,
        ), patch.object(
            tmux_module.shutil,
            "which",
            side_effect=lambda name: "/opt/homebrew/bin/brew" if name == "brew" else None,
        ), patch.object(tmux_module.Path, "exists", return_value=True):
            candidates = tmux_module._candidate_homebrew_binaries()

        assert candidates[0] == "/usr/local/bin/brew"
        assert candidates.index("/usr/local/bin/brew") < candidates.index("/opt/homebrew/bin/brew")

    def test_candidate_binaries_prefer_apple_silicon_homebrew_tmux(self, monkeypatch):
        monkeypatch.delenv("AUTORUN_TMUX_BIN", raising=False)
        resolve_tmux_binary.cache_clear()

        with patch.object(tmux_module.platform, "system", return_value="Darwin"), patch.object(
            tmux_module.platform,
            "machine",
            return_value="arm64",
        ), patch.object(
            tmux_module,
            "_tmux_paths_from_homebrew",
            return_value=["/opt/homebrew/opt/tmux/bin/tmux"],
        ), patch.object(
            tmux_module.shutil,
            "which",
            side_effect=lambda name: "/usr/local/bin/tmux" if name == "tmux" else None,
        ), patch.object(tmux_module.Path, "exists", return_value=True):
            candidates = tmux_module._candidate_tmux_binaries()

        assert candidates[0] == "/opt/homebrew/opt/tmux/bin/tmux"
        assert candidates.index("/opt/homebrew/bin/tmux") < candidates.index("/usr/local/bin/tmux")

    def test_candidate_binaries_prefer_apple_silicon_homebrew_tmux_under_rosetta(self, monkeypatch):
        monkeypatch.delenv("AUTORUN_TMUX_BIN", raising=False)
        resolve_tmux_binary.cache_clear()

        with patch.object(tmux_module.platform, "system", return_value="Darwin"), patch.object(
            tmux_module.platform,
            "machine",
            return_value="x86_64",
        ), patch.object(
            tmux_module,
            "_is_apple_silicon",
            return_value=True,
        ), patch.object(
            tmux_module,
            "_tmux_paths_from_homebrew",
            return_value=["/opt/homebrew/opt/tmux/bin/tmux"],
        ), patch.object(
            tmux_module.shutil,
            "which",
            side_effect=lambda name: "/usr/local/bin/tmux" if name == "tmux" else None,
        ), patch.object(tmux_module.Path, "exists", return_value=True):
            candidates = tmux_module._candidate_tmux_binaries()

        assert candidates[0] == "/opt/homebrew/opt/tmux/bin/tmux"
        assert candidates.index("/opt/homebrew/bin/tmux") < candidates.index("/usr/local/bin/tmux")

    def test_candidate_binaries_prefer_intel_homebrew_tmux_on_genuine_intel_mac(self, monkeypatch):
        monkeypatch.delenv("AUTORUN_TMUX_BIN", raising=False)
        resolve_tmux_binary.cache_clear()

        with patch.object(tmux_module.platform, "system", return_value="Darwin"), patch.object(
            tmux_module.platform,
            "machine",
            return_value="x86_64",
        ), patch.object(
            tmux_module,
            "_is_apple_silicon",
            return_value=False,
        ), patch.object(
            tmux_module,
            "_tmux_paths_from_homebrew",
            return_value=[
                "/usr/local/opt/tmux/bin/tmux",
                "/opt/homebrew/opt/tmux/bin/tmux",
            ],
        ), patch.object(
            tmux_module.shutil,
            "which",
            side_effect=lambda name: "/opt/homebrew/bin/tmux" if name == "tmux" else None,
        ), patch.object(tmux_module.Path, "exists", return_value=True):
            candidates = tmux_module._candidate_tmux_binaries()

        assert candidates[0] == "/usr/local/opt/tmux/bin/tmux"
        assert candidates.index("/usr/local/bin/tmux") < candidates.index("/opt/homebrew/bin/tmux")

    def test_homebrew_lookup_asks_for_the_prefix_without_a_formula(self):
        """`brew --prefix tmux` resolves the formula, which took 3.9 to 6.3 s
        under an empty HOME against a 2 s timeout; `brew --prefix` alone
        answers offline in about 0.02 s, and the tmux client sits at
        `<prefix>/opt/tmux/bin/tmux`."""
        calls = []

        def fake_run(argv, **_kwargs):
            calls.append(argv)
            return Mock(returncode=0, stdout="/opt/homebrew\n")

        with patch.object(
            tmux_module, "_candidate_homebrew_binaries", return_value=["/opt/homebrew/bin/brew"]
        ), patch.object(tmux_module.subprocess, "run", side_effect=fake_run):
            paths = tmux_module._tmux_paths_from_homebrew()

        assert calls == [["/opt/homebrew/bin/brew", "--prefix"]]
        assert paths == [str(Path("/opt/homebrew") / "opt" / "tmux" / "bin" / "tmux")]

    def test_prefers_client_that_can_read_inherited_socket(self, monkeypatch):
        monkeypatch.delenv("AUTORUN_TMUX_BIN", raising=False)
        monkeypatch.setenv("TERM_PROGRAM_VERSION", "3.6b")
        resolve_tmux_binary.cache_clear()

        with patch.object(
            tmux_module,
            "_candidate_tmux_binaries",
            return_value=["/usr/local/bin/tmux", "/opt/homebrew/bin/tmux"],
        ), patch.object(
            tmux_module,
            "_current_tmux_socket",
            return_value="/private/tmp/tmux-502/default",
        ), patch.object(
            tmux_module,
            "_tmux_socket_is_usable",
            side_effect=lambda binary, _socket: binary == "/opt/homebrew/bin/tmux",
        ), patch.object(
            tmux_module,
            "_tmux_version",
            side_effect=lambda binary: "tmux 3.6b" if binary == "/opt/homebrew/bin/tmux" else "tmux 3.5a",
        ):
            assert resolve_tmux_binary() == "/opt/homebrew/bin/tmux"


class TestDetectThinkingMode:
    """Tests for tmux_detect_claude_thinking_mode function"""

    @pytest.mark.unit
    def test_thinking_mode_active(self):
        """Test detection when thinking mode is active"""
        content = """Some output here
✳ Schlepping… (esc to interrupt · 7s · ↓ 44 tokens · thinking)
"""
        assert tmux_detect_claude_thinking_mode(content) is True

    @pytest.mark.unit
    def test_thinking_mode_with_tokens(self):
        """Test detection with tokens indicator"""
        content = """Working on your request...
✳ Processing (esc to interrupt · 12s · ↑ 1.2k tokens · thinking)
"""
        assert tmux_detect_claude_thinking_mode(content) is True

    @pytest.mark.unit
    def test_thinking_mode_inactive(self):
        """Test when thinking mode is not active"""
        content = """Some output here
✳ Working... (esc to interrupt · 7s · ↓ 44 tokens)
"""
        assert tmux_detect_claude_thinking_mode(content) is False

    @pytest.mark.unit
    def test_thinking_word_without_status_bar(self):
        """Test that 'thinking' in regular text doesn't trigger"""
        content = """I'm thinking about how to solve this problem.
This requires careful thinking and analysis.
>
"""
        assert tmux_detect_claude_thinking_mode(content) is False

    @pytest.mark.unit
    def test_empty_content(self):
        """Test with empty content"""
        assert tmux_detect_claude_thinking_mode("") is False
        assert tmux_detect_claude_thinking_mode(None) is False

    @pytest.mark.unit
    def test_thinking_in_last_5_lines(self):
        """Test that detection works within last 5 lines"""
        content = """Line 1
Line 2
Line 3
Line 4
✳ Working (esc to interrupt · 5s · ↓ 100 tokens · thinking)
"""
        assert tmux_detect_claude_thinking_mode(content) is True


class TestDetectClaudeMode:
    """Tests for tmux_detect_claude_mode function"""

    @pytest.mark.unit
    def test_default_mode(self):
        """Test detection of default mode (no indicator)"""
        content = """Some regular output
>
"""
        assert tmux_detect_claude_mode(content) == CLAUDE_MODE_DEFAULT

    @pytest.mark.unit
    def test_plan_mode(self):
        """Test detection of plan mode"""
        content = """Output with status
plan mode on
>
"""
        assert tmux_detect_claude_mode(content) == CLAUDE_MODE_PLAN

    @pytest.mark.unit
    def test_bypass_mode(self):
        """Test detection of bypass mode"""
        content = """Output here
bypass permissions on
>
"""
        assert tmux_detect_claude_mode(content) == CLAUDE_MODE_BYPASS

    @pytest.mark.unit
    def test_accept_edits_mode(self):
        """Test detection of accept edits mode"""
        content = """Working on task
accept edits on
>
"""
        assert tmux_detect_claude_mode(content) == CLAUDE_MODE_ACCEPT_EDITS

    @pytest.mark.unit
    def test_empty_content(self):
        """Test with empty content returns default"""
        assert tmux_detect_claude_mode("") == CLAUDE_MODE_DEFAULT
        assert tmux_detect_claude_mode(None) == CLAUDE_MODE_DEFAULT


class TestWindowListHelperMethods:
    """Tests for WindowList enhanced helper methods"""

    def create_test_windows(self):
        """Create test window data"""
        return WindowList([
            {
                'session': 'main', 'w': 1, 'title': 'Task 1',
                'is_active': True, 'claude_mode': 'plan',
                'is_thinking': True, 'is_claude_session': True,
                'prompt_type': None
            },
            {
                'session': 'main', 'w': 2, 'title': 'Task 2',
                'is_active': False, 'claude_mode': 'default',
                'is_thinking': False, 'is_claude_session': True,
                'prompt_type': 'input'
            },
            {
                'session': 'main', 'w': 3, 'title': 'Shell',
                'is_active': False, 'claude_mode': 'default',
                'is_thinking': False, 'is_claude_session': False,
                'prompt_type': None
            },
            {
                'session': 'dev', 'w': 1, 'title': 'Debug',
                'is_active': True, 'claude_mode': 'accept_edits',
                'is_thinking': True, 'is_claude_session': True,
                'prompt_type': None
            },
        ])

    @pytest.mark.unit
    def test_actively_generating(self):
        """Test actively_generating filter"""
        windows = self.create_test_windows()
        active = windows.actively_generating()

        assert len(active) == 2
        assert all(w['is_active'] is True for w in active)
        assert active[0]['w'] == 1
        assert active[1]['session'] == 'dev'

    @pytest.mark.unit
    def test_in_mode_plan(self):
        """Test in_mode filter for plan mode"""
        windows = self.create_test_windows()
        plan = windows.in_mode('plan')

        assert len(plan) == 1
        assert plan[0]['w'] == 1
        assert plan[0]['claude_mode'] == 'plan'

    @pytest.mark.unit
    def test_in_mode_default(self):
        """Test in_mode filter for default mode"""
        windows = self.create_test_windows()
        default = windows.in_mode('default')

        assert len(default) == 2
        assert all(w['claude_mode'] == 'default' for w in default)

    @pytest.mark.unit
    def test_in_mode_accept_edits(self):
        """Test in_mode filter for accept_edits mode"""
        windows = self.create_test_windows()
        accept = windows.in_mode('accept_edits')

        assert len(accept) == 1
        assert accept[0]['session'] == 'dev'

    @pytest.mark.unit
    def test_claude_sessions(self):
        """Test claude_sessions filter"""
        windows = self.create_test_windows()
        claude_only = windows.claude_sessions()

        assert len(claude_only) == 3
        assert all(w['is_claude_session'] is True for w in claude_only)
        # Window 3 (Shell) should be excluded
        assert all(w['title'] != 'Shell' for w in claude_only)

    @pytest.mark.unit
    def test_thinking_enabled(self):
        """Test thinking_enabled filter"""
        windows = self.create_test_windows()
        thinking = windows.thinking_enabled()

        assert len(thinking) == 2
        assert all(w['is_thinking'] is True for w in thinking)

    @pytest.mark.unit
    def test_chained_filters(self):
        """Test chaining multiple filters"""
        windows = self.create_test_windows()
        result = windows.claude_sessions().actively_generating()

        assert len(result) == 2
        assert all(w['is_claude_session'] is True for w in result)
        assert all(w['is_active'] is True for w in result)

    @pytest.mark.unit
    def test_empty_result(self):
        """Test filters returning empty WindowList"""
        windows = self.create_test_windows()
        result = windows.in_mode('bypass')

        assert len(result) == 0
        assert isinstance(result, WindowList)


class TestNormalizeTargets:
    """Tests for _tmux_normalize_targets helper function"""

    @pytest.mark.unit
    def test_string_target(self):
        """Test string target normalization"""
        result = _tmux_normalize_targets("main:5")
        assert result == [('main', '5')]

    @pytest.mark.unit
    def test_string_with_pane(self):
        """Test string target with pane number"""
        result = _tmux_normalize_targets("main:5.0")
        assert result == [('main', '5')]

    @pytest.mark.unit
    def test_single_dict(self):
        """Test single dict target"""
        result = _tmux_normalize_targets({'session': 'main', 'w': 5})
        assert result == [('main', '5')]

    @pytest.mark.unit
    def test_window_list(self):
        """Test WindowList target"""
        windows = WindowList([
            {'session': 'main', 'w': 1},
            {'session': 'main', 'w': 2},
            {'session': 'dev', 'w': 1},
        ])
        result = _tmux_normalize_targets(windows)
        assert result == [('main', '1'), ('main', '2'), ('dev', '1')]

    @pytest.mark.unit
    def test_list_of_dicts(self):
        """Test list of dict targets"""
        targets = [
            {'session': 'main', 'w': 1},
            {'session': 'dev', 'w': 3},
        ]
        result = _tmux_normalize_targets(targets)
        assert result == [('main', '1'), ('dev', '3')]

    @pytest.mark.unit
    def test_invalid_string(self):
        """Test invalid string returns empty list"""
        result = _tmux_normalize_targets("invalid")
        assert result == []

    @pytest.mark.unit
    def test_dict_missing_keys(self):
        """Test dict missing required keys"""
        result = _tmux_normalize_targets({'title': 'test'})
        assert result == []


class TestSendKeysArgv:
    """`send_keys` builds one `send-keys <arg>` for every kind of send."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "keys",
        [
            "C-m", "C-c", "C-u",          # named in the old branch's list
            "Enter", "Escape", "Tab", "BTab",  # key names it did not name
            "C-a C-k", "Escape 0 d",      # multi-key sequences
            "export TEST_VAR=1",          # literal text
            "",                           # empty send
        ],
    )
    def test_every_kind_of_send_is_one_unmodified_argument(self, keys):
        """tmux parses each argument as a key name and falls back to sending
        its characters, which is why one form serves both.

        Pinned because the branch this replaces claimed to separate the two and
        built the same list in both arms. Separating them for real means
        `send-keys -l` on the text arm, and the arm's five-name list did not
        recognise `Enter`, `Escape`, `Tab`, `BTab`, `C-a C-k` or `Escape 0 d` —
        so restoring the split without reclassifying those call sites would send
        each of them as literal characters instead of as the key it names.
        """
        tmux = TmuxUtilities("some-session")
        with patch.object(
            tmux, "execute_tmux_command", return_value={"returncode": 0, "stdout": ""}
        ) as executed:
            assert tmux.send_keys(keys, "some-session") is True

        executed.assert_called_once_with(
            ["send-keys", keys], "some-session", None, None
        )

    @pytest.mark.unit
    def test_a_failed_send_is_reported_as_false(self):
        """A non-zero tmux exit and an unreachable server are both failures;
        `execute_tmux_command` returns None for the second."""
        tmux = TmuxUtilities("some-session")
        for outcome in ({"returncode": 1, "stdout": ""}, None):
            with patch.object(tmux, "execute_tmux_command", return_value=outcome):
                assert not tmux.send_keys("C-m", "some-session")


class TestExecuteWindowAction:
    """Tests for tmux_dangerous_batch_execute function"""

    @pytest.fixture
    def mock_tmux(self):
        """Create mock TmuxUtilities instance"""
        mock = Mock()
        mock.send_keys.return_value = True
        mock.execute_tmux_command.return_value = {
            'returncode': 0,
            'stdout': '>\n'
        }
        return mock

    @pytest.mark.unit
    def test_action_constants_defined(self):
        """Test all action constants are defined"""
        assert ACTION_SEND == 'send'
        assert ACTION_CONTINUE == 'continue'
        assert ACTION_ESCAPE == 'escape'
        assert ACTION_STOP == 'stop'
        assert ACTION_EXIT == 'exit'
        assert ACTION_KILL == 'kill'
        assert ACTION_TOGGLE_THINKING == 'toggle_thinking'
        assert ACTION_CYCLE_MODE == 'cycle_mode'
        assert ACTION_SET_MODE == 'set_mode'

    @pytest.mark.unit
    def test_escape_action(self, mock_tmux):
        """Test escape action execution"""
        result = tmux_dangerous_batch_execute(mock_tmux, 'escape', 'main:1')

        assert result['success_count'] == 1
        assert result['failure_count'] == 0
        assert len(result['results']) == 1
        assert result['results'][0]['target'] == 'main:1'
        assert result['results'][0]['success'] is True

    @pytest.mark.unit
    def test_stop_action_alias(self, mock_tmux):
        """Test stop action (alias for escape)"""
        result = tmux_dangerous_batch_execute(mock_tmux, 'stop', 'main:1')

        assert result['success_count'] == 1
        mock_tmux.send_keys.assert_called()

    @pytest.mark.unit
    def test_multiple_targets(self, mock_tmux):
        """Test action on multiple targets"""
        targets = WindowList([
            {'session': 'main', 'w': 1},
            {'session': 'main', 'w': 2},
        ])
        result = tmux_dangerous_batch_execute(mock_tmux, 'escape', targets)

        assert result['success_count'] == 2
        assert result['failure_count'] == 0
        assert len(result['results']) == 2

    @pytest.mark.unit
    def test_send_without_message(self, mock_tmux):
        """Test send action without message fails"""
        result = tmux_dangerous_batch_execute(mock_tmux, 'send', 'main:1')

        assert result['success_count'] == 0
        assert result['failure_count'] == 1
        assert result['results'][0]['reason'] == 'no_message'

    @pytest.mark.unit
    def test_set_mode_without_mode(self, mock_tmux):
        """Test set_mode action without mode specified fails"""
        result = tmux_dangerous_batch_execute(mock_tmux, 'set_mode', 'main:1')

        assert result['success_count'] == 0
        assert result['failure_count'] == 1
        assert result['results'][0]['reason'] == 'no_mode_specified'

    @pytest.mark.unit
    def test_unknown_action(self, mock_tmux):
        """Test unknown action returns error"""
        result = tmux_dangerous_batch_execute(mock_tmux, 'invalid_action', 'main:1')

        assert result['success_count'] == 0
        assert result['failure_count'] == 1
        assert 'unknown_action' in result['results'][0]['reason']

    @pytest.mark.unit
    def test_result_structure(self, mock_tmux):
        """Test result dictionary structure"""
        result = tmux_dangerous_batch_execute(mock_tmux, 'escape', 'main:1')

        assert 'success_count' in result
        assert 'failure_count' in result
        assert 'results' in result
        assert isinstance(result['results'], list)
        assert 'target' in result['results'][0]
        assert 'success' in result['results'][0]
        assert 'reason' in result['results'][0]


class TestIntegrationScenarios:
    """Integration tests for common usage patterns"""

    @pytest.mark.unit
    def test_filter_and_action_workflow(self):
        """Test typical workflow: filter windows then plan action"""
        windows = WindowList([
            {'session': 'main', 'w': 1, 'is_active': True,
             'claude_mode': 'default', 'is_claude_session': True},
            {'session': 'main', 'w': 2, 'is_active': False,
             'claude_mode': 'plan', 'is_claude_session': True,
             'prompt_type': 'input'},
            {'session': 'main', 'w': 3, 'is_active': False,
             'claude_mode': 'default', 'is_claude_session': False},
        ])

        # Filter to Claude sessions that are active
        active_claude = windows.claude_sessions().actively_generating()
        assert len(active_claude) == 1
        assert active_claude[0]['w'] == 1

        # Get targets for potential action
        targets = _tmux_normalize_targets(active_claude)
        assert targets == [('main', '1')]

    @pytest.mark.unit
    def test_mode_filtering_workflow(self):
        """Test workflow for managing windows by mode"""
        windows = WindowList([
            {'session': 'main', 'w': 1, 'claude_mode': 'plan'},
            {'session': 'main', 'w': 2, 'claude_mode': 'default'},
            {'session': 'dev', 'w': 1, 'claude_mode': 'plan'},
            {'session': 'dev', 'w': 2, 'claude_mode': 'accept_edits'},
        ])

        # Find all plan mode windows
        plan_windows = windows.in_mode('plan')
        assert len(plan_windows) == 2

        # Can convert to targets for batch action
        targets = plan_windows.to_targets()
        assert 'main:1' in targets
        assert 'dev:1' in targets


class TestGetClaudeWindowStatus:
    """Tests for tmux_get_claude_window_status and tmux_get_claude_window_mode functions"""

    @pytest.fixture
    def mock_tmux_with_content(self):
        """Create mock TmuxUtilities that returns realistic content"""
        mock = Mock()

        def mock_execute(cmd, session=None, window=None, pane=None):
            if 'capture-pane' in cmd:
                return {
                    'returncode': 0,
                    'stdout': """Working on task...
✳ Processing (esc to interrupt · 5s · ↓ 100 tokens · thinking)
plan mode on
>
"""
                }
            return {'returncode': 0, 'stdout': ''}

        mock.execute_tmux_command = mock_execute
        return mock

    @pytest.fixture
    def mock_tmux_failed_capture(self):
        """Create mock TmuxUtilities that fails capture"""
        mock = Mock()
        mock.execute_tmux_command.return_value = {'returncode': 1, 'stdout': ''}
        return mock

    @pytest.mark.unit
    def test_tmux_get_claude_window_status_success(self, mock_tmux_with_content):
        """Test successful window status retrieval"""
        status = tmux_get_claude_window_status(mock_tmux_with_content, 'main', '5')

        assert status['success'] is True
        assert status['claude_mode'] == CLAUDE_MODE_PLAN
        assert status['is_thinking'] is True
        assert status['is_active'] is True  # Has spinner
        assert status['content'] != ''

    @pytest.mark.unit
    def test_tmux_get_claude_window_status_failed_capture(self, mock_tmux_failed_capture):
        """Test window status when capture fails"""
        status = tmux_get_claude_window_status(mock_tmux_failed_capture, 'main', '5')

        assert status['success'] is False
        assert status['claude_mode'] == CLAUDE_MODE_DEFAULT
        assert status['is_thinking'] is False
        assert status['is_active'] is False
        assert status['content'] == ''

    @pytest.mark.unit
    def test_tmux_get_claude_window_status_structure(self, mock_tmux_with_content):
        """Test that status dict has all expected keys"""
        status = tmux_get_claude_window_status(mock_tmux_with_content, 'main', '5')

        expected_keys = ['claude_mode', 'is_thinking', 'is_active',
                         'prompt_type', 'content', 'success']
        for key in expected_keys:
            assert key in status, f"Missing key: {key}"

    @pytest.mark.unit
    def test_tmux_get_claude_window_mode_convenience(self, mock_tmux_with_content):
        """Test tmux_get_claude_window_mode convenience function"""
        mode = tmux_get_claude_window_mode(mock_tmux_with_content, 'main', '5')

        assert mode == CLAUDE_MODE_PLAN

    @pytest.mark.unit
    def test_tmux_get_claude_window_mode_failed_capture(self, mock_tmux_failed_capture):
        """Test tmux_get_claude_window_mode returns default on failure"""
        mode = tmux_get_claude_window_mode(mock_tmux_failed_capture, 'main', '5')

        assert mode == CLAUDE_MODE_DEFAULT


class TestGetClaudeWindowStatusModes:
    """Test tmux_get_claude_window_status with different modes"""

    def create_mock_for_content(self, content):
        """Helper to create mock with specific content"""
        mock = Mock()
        mock.execute_tmux_command.return_value = {
            'returncode': 0,
            'stdout': content
        }
        return mock

    @pytest.mark.unit
    def test_detect_default_mode(self):
        """Test detection of default mode"""
        content = """Some output
>
"""
        mock = self.create_mock_for_content(content)
        status = tmux_get_claude_window_status(mock, 'main', '1')

        assert status['claude_mode'] == CLAUDE_MODE_DEFAULT

    @pytest.mark.unit
    def test_detect_bypass_mode(self):
        """Test detection of bypass mode"""
        content = """Output here
bypass permissions on
>
"""
        mock = self.create_mock_for_content(content)
        status = tmux_get_claude_window_status(mock, 'main', '1')

        assert status['claude_mode'] == CLAUDE_MODE_BYPASS

    @pytest.mark.unit
    def test_detect_accept_edits_mode(self):
        """Test detection of accept edits mode"""
        content = """Working...
accept edits on
>
"""
        mock = self.create_mock_for_content(content)
        status = tmux_get_claude_window_status(mock, 'main', '1')

        assert status['claude_mode'] == CLAUDE_MODE_ACCEPT_EDITS

    @pytest.mark.unit
    def test_detect_not_thinking(self):
        """Test detection when thinking is off"""
        content = """Working...
✳ Processing (esc to interrupt · 5s · ↓ 100 tokens)
>
"""
        mock = self.create_mock_for_content(content)
        status = tmux_get_claude_window_status(mock, 'main', '1')

        assert status['is_thinking'] is False

    @pytest.mark.unit
    def test_detect_not_active(self):
        """Test detection when not actively generating"""
        content = """Previous output
>
"""
        mock = self.create_mock_for_content(content)
        status = tmux_get_claude_window_status(mock, 'main', '1')

        assert status['is_active'] is False


class TestSetModeReachesTheRequestedWindow:
    """The set_mode action called cycle_to_mode(tmux, mode, session, window,
    None), so the session name was read as screen content and the window was
    used as the session; cycle_to_mode also pressed Shift+Tab max_cycles times
    without reading the screen again and reported success regardless."""

    def _run(self, screens, mode="plan"):
        sent = []
        captures = iter(screens)

        def shift_tab(tmux, session, window, pane):
            sent.append((session, window))
            return True

        with patch.object(tmux_module, "send_shift_tab", side_effect=shift_tab), \
                patch.object(tmux_module, "_tmux_capture_pane",
                             side_effect=lambda *a, **k: next(captures)), \
                patch("time.sleep", lambda s: None):
            result = tmux_module.tmux_dangerous_batch_execute(
                Mock(), "set_mode", "main:5", message=mode, force=True, delay_ms=0
            )
        return result, sent

    def test_it_presses_until_the_target_mode_shows_then_stops(self):
        result, sent = self._run([
            "> \n",                       # before: default mode
            "> \n  accept edits on\n",    # after press 1
            "> \n  plan mode on\n",       # after press 2: reached
        ])

        assert sent == [("main", "5"), ("main", "5")]
        assert result["success_count"] == 1, result

    def test_it_reports_failure_when_the_mode_never_shows(self):
        result, sent = self._run(["> \n"] * 10)

        assert sent and all(target == ("main", "5") for target in sent)
        assert result["success_count"] == 0, result


CLAUDE_IDLE_SCREEN = (
    "⏺ All tests pass.\n"
    "\n"
    "────────────────────────────────────────\n"
    "❯ \n"
    "────────────────────────────────────────\n"
    "  ? for shortcuts\n"
)


class TestSafeToSendAgreesWithThePromptDetector:
    """tmux_detect_prompt_type calls a '❯' line or an indented '  > ' line the
    input prompt, but check_safe_to_send re-parsed only lines starting with
    '>' and returned unknown_state, so send_message_to_claude and /ar:tabw
    continue refused at Claude Code's own prompt."""

    def test_an_empty_chevron_prompt_is_ready(self):
        assert tmux_module.check_safe_to_send(CLAUDE_IDLE_SCREEN) == (True, "ready")

    def test_an_indented_empty_prompt_is_ready(self):
        assert tmux_module.check_safe_to_send("some output\n  > \n") == (True, "ready")

    def test_text_after_the_chevron_is_user_typing(self):
        screen = CLAUDE_IDLE_SCREEN.replace("❯ \n", "❯ fix the lint\n")
        assert tmux_module.check_safe_to_send(screen) == (False, "user_typing")

    def test_tabw_continue_reaches_a_window_at_the_chevron_prompt(self):
        tmux = Mock()
        tmux.execute_tmux_command.return_value = {"returncode": 0, "stdout": CLAUDE_IDLE_SCREEN}
        tmux.send_keys.return_value = True
        with patch("time.sleep"):
            result = tmux_dangerous_batch_execute(tmux, ACTION_CONTINUE, "main:5")

        assert result["success_count"] == 1, result
        assert [c.args[0] for c in tmux.send_keys.call_args_list] == ["continue", "C-m"]


class TestOnlyACursorOnAnOptionIsAChoicePrompt:
    """tmux_detect_prompt_type called a screen a 'question' when a '❯' and a
    line like '1. Run' were anywhere in its last 15 lines, and a
    'plan_approval' when a '❯' and 'Would you like to proceed?' were. Claude
    Code's idle input prompt is itself a '❯', so an idle window whose reply
    held a numbered list, or ended by asking "Would you like to proceed?", was
    a choice prompt: check_safe_to_send refused it and /ar:tabw continue
    skipped it. A real choice prompt puts the cursor on an option: '❯ 1. Yes'."""

    @pytest.mark.parametrize("reply", [
        "⏺ Next steps:\n  1. Run the tests\n  2. Fix the lint\n\n",
        "⏺ The plan is written. Would you like to proceed?\n\n",
    ])
    def test_claude_idle_after_a_reply_is_at_the_input_prompt(self, reply):
        screen = reply + CLAUDE_IDLE_SCREEN

        assert tmux_module.tmux_detect_prompt_type(screen) == "input"
        assert tmux_module.check_safe_to_send(screen) == (True, "ready")

    @pytest.mark.parametrize("screen,expected", [
        (" Bash command\n\n   rm -rf build\n\n Do you want to proceed?\n"
         " ❯ 1. Yes\n   2. Yes, and don't ask again for rm commands\n"
         "   3. No, and tell Claude what to do differently (esc)\n", "question"),
        ("│ Do you want to proceed?          │\n│ ❯ 1. Yes                         │\n"
         "│   2. No                          │\n╰──────────────────────────────────╯\n",
         "question"),
        ("Which approach?\n\n  1. Patch the caller\n     Smallest diff\n"
         "❯ 2. Fix the shared helper\n     Covers every caller\n  3. Type something.\n",
         "question"),
        (" Would you like to proceed?\n\n ❯ 1. Yes, and auto-accept edits\n"
         "   2. Yes, and manually approve edits\n   3. No, keep planning\n", "plan_approval"),
    ])
    def test_a_cursor_on_a_numbered_option_is_a_choice_prompt(self, screen, expected):
        assert tmux_module.tmux_detect_prompt_type(screen) == expected


class TestEnvironmentDetectionAsksTmuxForNames:
    """detect_tmux_environment parsed TMUX=socket,server_pid,session_id and
    returned the server PID as the session name and the session id as the
    pane, so a caller targeted (and auto-created) a session named after
    the PID."""

    def test_session_window_pane_come_from_display_message(self, monkeypatch):
        monkeypatch.setenv("TMUX", "/tmp/tmux-501/default,4219,0")
        with patch.object(tmux_module.subprocess, "run",
                          return_value=Mock(returncode=0, stdout="main:3:1\n")) as run:
            env = TmuxUtilities("autorun").detect_tmux_environment()

        assert (env["session"], env["window"], env["pane"]) == ("main", "3", "1")
        assert env["socket_path"] == "/tmp/tmux-501/default"
        assert "-t" not in run.call_args.args[0], "must ask about the caller's pane, not a target"

    def test_outside_tmux_there_is_no_current_window(self, monkeypatch):
        """Asked from outside tmux, display-message without -t describes the
        server's most recently used session, so tmux_list_windows dropped that
        session's current window as "the caller's own" and `autorun tmux list`
        run from a plain terminal hid an AI session (found by the end-to-end
        test in test_tmux_command_line.py)."""
        monkeypatch.delenv("TMUX", raising=False)
        with patch.object(tmux_module.subprocess, "run",
                          return_value=Mock(returncode=0, stdout="main:3:1\n")) as run:
            location = tmux_module._tmux_current_location()

        assert location is None
        run.assert_not_called()

    def test_outside_tmux_there_is_no_environment(self, monkeypatch):
        monkeypatch.delenv("TMUX", raising=False)
        assert TmuxUtilities("autorun").detect_tmux_environment() is None


class TestWindowListingSurvivesAPipeInAField:
    """tmux_list_windows split list-windows output on '|', so a pane title or
    path containing '|' gave the wrong field count and the window vanished."""

    @pytest.mark.parametrize("vis_encoded", [False, True], ids=["tmux-3.5+", "tmux-3.4"])
    def test_a_pipe_in_the_title_and_path_keeps_the_window(self, vis_encoded):
        """tmux 3.4 prints list output through utf8_stravisx(VIS_OCTAL), so the
        \\x1f separator arrives as the four characters \\037 (server-client.c
        server_client_print, cmdq_print passes parse=0); later versions print
        the byte. Both must parse."""
        import re

        values = {
            "session_name": "main", "window_index": "3", "pane_title": "build | test",
            "pane_current_command": "node", "pane_current_path": "/srv/a|b",
            "pane_pid": "42", "window_active": "0", "window_activity": "1700000000",
            "pane_index": "0",
            "window_flags": "",  # a background window: the line ends in the separator
        }

        def fake_tmux(cmd, session=None, window=None, pane=None):
            fmt = cmd[cmd.index("-F") + 1]  # expand the format as tmux would
            line = re.sub(r"#\{(\w+)\}", lambda m: values[m.group(1)], fmt)
            if vis_encoded:
                line = line.replace("\x1f", "\\037")
            return {"returncode": 0, "stdout": line + "\n"}

        tmux = Mock()
        tmux.execute_tmux_command.side_effect = fake_tmux
        with patch.object(tmux_module, "get_tmux_utilities", return_value=tmux):
            windows = tmux_module.tmux_list_windows(session="main", exclude_current=False)

        assert [(w["w"], w["title"], w["path"]) for w in windows] == [(3, "build | test", "/srv/a|b")]

    @pytest.mark.tmux
    @pytest.mark.parametrize("lang", ["C.UTF-8", "C"])
    def test_a_pipe_in_a_real_pane_title_keeps_the_window(self, lang, monkeypatch):
        """Under a non-UTF-8 locale a tmux client prints the \x1f separator as
        "_", so every line was malformed and no window was listed (the
        tmux-integration CI job). autorun's tmux calls pass -u."""
        import shutil
        import subprocess

        from conftest import tmux_argv

        if not shutil.which("tmux"):
            pytest.skip("tmux not installed")
        for name in ("LC_ALL", "LC_CTYPE"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("LANG", lang)
        session = "autorun-test-pipe-title"
        subprocess.run(tmux_argv("new-session", "-d", "-s", session, "-x", "80", "-y", "24"),
                       capture_output=True)
        try:
            subprocess.run(tmux_argv("select-pane", "-t", f"{session}:0", "-T", "build | test"),
                           capture_output=True, check=True)
            windows = tmux_module.tmux_list_windows(session=session, exclude_current=False)
            assert [w["title"] for w in windows] == ["build | test"]
        finally:
            subprocess.run(tmux_argv("kill-session", "-t", session), capture_output=True)
