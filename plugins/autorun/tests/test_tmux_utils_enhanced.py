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

from autorun.tmux_utils import (
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
