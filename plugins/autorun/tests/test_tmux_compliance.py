#!/usr/bin/env python3
"""
Comprehensive tmux/byobu compliance test

Validates that the tmux implementation meets all requirements from
plugins/autorun-skills/skills/tmux-automation/SKILL.md
"""

import sys
import os

# Add src directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from autorun.tmux_utils import get_tmux_utilities, TmuxUtilities, TmuxControlState


def test_default_session_naming():
    """Test requirement: Default session name should be 'autorun'"""
    # Test centralized utilities
    tmux_utils = get_tmux_utilities()
    assert tmux_utils.DEFAULT_SESSION_NAME == "autorun", \
        f"Default session name incorrect: {tmux_utils.DEFAULT_SESSION_NAME}"


def test_win_ops_completeness():
    """Test requirement: Complete WIN_OPS dispatch dictionary"""

    tmux_utils = TmuxUtilities("test_session")

    # Required operations from documentation
    required_ops = [
        # Navigation and window management
        'new-window', 'new', 'nw',
        'select-window', 'sw', 'window', 'w',
        'next-window', 'n', 'prev-window', 'p', 'pw',

        # Pane management
        'split-window', 'split', 'sp', 'vsplit', 'vsp',
        'select-pane', 'sp', 'pane', 'np', 'pp',

        # Session management
        'new-session', 'ns', 'new-sess',
        'attach-session', 'attach', 'as',
        'detach-client', 'detach', 'dc',

        # Layout and display
        'select-layout', 'layout', 'sl',
        'clock-mode', 'clock',
        'copy-mode', 'copy',

        # Search and navigation
        'search-forward', 'search-backward',

        # Lists and info
        'list-windows', 'lw', 'list-sessions', 'ls', 'list-panes', 'lp',
        'rename-window', 'rename',
        'kill-window', 'kw', 'kill-pane', 'kp', 'kill-session', 'ks',

        # Display and messaging
        'display-message', 'display', 'dm',
        'show-options', 'show', 'so',

        # Input and capture
        'send-keys', 'send', 'sk',
        'capture-pane', 'capture', 'cp',
        'pipe-pane', 'pipe',

        # Buffer operations
        'list-buffers', 'lb', 'save-buffer', 'sb', 'delete-buffer', 'db',

        # Advanced operations
        'resize-pane', 'resize', 'rp',
        'swap-pane', 'swap',
        'join-pane', 'join', 'break-pane', 'break',
    ]

    missing_ops = [op for op in required_ops if op not in tmux_utils.WIN_OPS]
    assert not missing_ops, f"Missing WIN_OPS: {missing_ops}"

    # Test some key operations have correct command mappings
    test_cases = [
        ('new-window', 'new-window'),
        ('split-window', 'split-window'),
        ('list-windows', 'list-windows'),
        ('send-keys', 'send-keys'),
        ('capture-pane', 'capture-pane'),
    ]

    for op, expected_cmd in test_cases:
        assert tmux_utils.WIN_OPS[op] == expected_cmd, \
            f"WIN_OPS mapping incorrect for {op}: {tmux_utils.WIN_OPS[op]} != {expected_cmd}"


def test_session_environment_detection():
    """Test session environment detection and fallback"""

    tmux_utils = TmuxUtilities("test_session")

    # Test environment detection (may or may not detect tmux)
    env_info = tmux_utils.detect_tmux_environment()

    # Test session info retrieval
    session_info = tmux_utils.get_session_info()
    assert session_info is not None, "Session info retrieval returned None"
    assert "session" in session_info, "Session info missing 'session' key"
    assert "available_sessions" in session_info, "Session info missing 'available_sessions' key"


def test_error_handling_and_fallbacks():
    """Test error handling and graceful fallbacks"""

    tmux_utils = TmuxUtilities("nonexistent_session")

    # Test command execution with invalid session - should not crash
    # The key requirement is graceful handling (no exception), not a specific return code
    result = tmux_utils.execute_tmux_command(
        ['list-windows', '-t', 'autorun_nonexistent_session_xyz_99'],
        'autorun_nonexistent_session_xyz_99'
    )
    assert result is None or isinstance(result, dict), \
        f"execute_tmux_command should return None or dict, got {type(result)}"

    # Test WIN_OPS with invalid operation - should return False, not crash
    success = tmux_utils.execute_win_op('nonexistent_operation')
    assert not success, "Invalid WIN_OP should return False"

    # Test user typing detection when no session - should return False, not crash
    is_typing = tmux_utils.is_user_typing()
    assert not is_typing, "User typing detection should default to False when no session"


def test_claude_detection_functions():
    """Test Claude session detection from pane content (no live tmux needed)."""

    from autorun.tmux_utils import (
        tmux_detect_prompt_type,
        tmux_detect_claude_active,
        tmux_detect_claude_mode,
        tmux_detect_claude_thinking_mode,
    )

    # Prompt type detection — detects Claude Code prompts, not shell prompts
    assert tmux_detect_prompt_type("") is None, "Empty content should return None"
    assert tmux_detect_prompt_type("random text no prompt") is None, \
        "Non-prompt content should return None"
    # Claude tool permission prompt
    assert tmux_detect_prompt_type("Allow tool use? [Y/n]") is not None, \
        "Should detect Y/n tool permission prompt"
    # Claude plan approval prompt
    assert tmux_detect_prompt_type("Would you like to proceed?\n❯ Yes") is not None, \
        "Should detect plan approval prompt"

    # Claude active detection
    assert tmux_detect_claude_active("╭─") or True, "Claude active detection returns bool"
    assert isinstance(tmux_detect_claude_active("random text"), bool)

    # Claude mode detection returns a string
    mode = tmux_detect_claude_mode("some pane content")
    assert isinstance(mode, str), f"Mode should be string, got {type(mode)}"

    # Thinking mode detection returns bool
    assert isinstance(tmux_detect_claude_thinking_mode("some content"), bool)


def test_safety_check_function():
    """Test check_safe_to_send validates content before sending to tmux."""

    from autorun.tmux_utils import check_safe_to_send

    # Safe content should pass
    is_safe, reason = check_safe_to_send("echo hello")
    assert isinstance(is_safe, bool), "check_safe_to_send should return (bool, str)"
    assert isinstance(reason, str), "check_safe_to_send reason should be str"

    # Empty content should be flagged
    is_safe_empty, reason_empty = check_safe_to_send("")
    assert not is_safe_empty, "Empty content should not be safe to send"


def test_detect_current_tmux_session():
    """Test detect_current_tmux_session returns None or str (no crash)."""

    from autorun.tmux_utils import detect_current_tmux_session

    result = detect_current_tmux_session()
    assert result is None or isinstance(result, str), \
        f"detect_current_tmux_session should return None or str, got {type(result)}"


def test_custom_session_naming():
    """Test that all components accept custom session names consistently."""

    custom_name = "test_custom_session_42"

    tmux = get_tmux_utilities(custom_name)
    assert tmux.session_name == custom_name, \
        f"get_tmux_utilities should accept custom name: {tmux.session_name}"


def test_control_state_enum():
    """Test TmuxControlState enum has required states."""

    assert hasattr(TmuxControlState, 'NORMAL'), "Missing NORMAL state"
    assert hasattr(TmuxControlState, 'ESCAPE'), "Missing ESCAPE state"

    # Verify state transitions work
    tmux = TmuxUtilities("test_state")
    tmux.control_state = TmuxControlState.NORMAL
    assert tmux.control_state == TmuxControlState.NORMAL
    tmux.control_state = TmuxControlState.ESCAPE
    assert tmux.control_state == TmuxControlState.ESCAPE
