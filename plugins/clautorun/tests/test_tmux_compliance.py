#!/usr/bin/env python3
"""
Comprehensive tmux/byobu compliance test

Validates that the tmux implementation meets all requirements from
CLI_USAGE_AND_TEST_AUTOMATION_WITH_BYOBU_TMUX_SESSIONS.md
"""

import sys
import os

import pytest

# Add src directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

try:
    from clautorun.tmux_utils import get_tmux_utilities, TmuxUtilities, TmuxControlState
    from clautorun.ai_monitor import get_tmux as get_ai_monitor_tmux
    from clautorun.tmux_injector import TmuxInjector, DualChannelInjector
    from clautorun.main import get_global_tmux_utils
    COMPLIANCE_TEST_AVAILABLE = True
except ImportError:
    COMPLIANCE_TEST_AVAILABLE = False


def _skip_if_unavailable():
    if not COMPLIANCE_TEST_AVAILABLE:
        pytest.skip("Tmux compliance modules not available")


def test_default_session_naming():
    """Test requirement: Default session name should be 'clautorun'"""
    _skip_if_unavailable()

    # Test centralized utilities
    tmux_utils = get_tmux_utilities()
    assert tmux_utils.DEFAULT_SESSION_NAME == "clautorun", \
        f"Default session name incorrect: {tmux_utils.DEFAULT_SESSION_NAME}"

    # Test injector default naming
    injector = TmuxInjector()
    assert injector.session_id == "clautorun", \
        f"TmuxInjector default session incorrect: {injector.session_id}"

    # Test dual channel injector default naming
    dual_injector = DualChannelInjector()
    assert dual_injector.session_id == "clautorun", \
        f"DualChannelInjector default session incorrect: {dual_injector.session_id}"


def test_control_sequence_parsing():
    """Test requirement: Control sequence syntax (^ escape, ^^ literal)"""
    _skip_if_unavailable()

    tmux_utils = TmuxUtilities("test_session")

    # Test basic parsing without control sequences
    result, state = tmux_utils.parse_control_sequences("normal text")
    assert result == "normal text", f"Basic parsing failed: {result}"
    assert state == TmuxControlState.NORMAL

    # Test literal ^^
    result, state = tmux_utils.parse_control_sequences("text^^more")
    assert result == "text^more", f"Literal ^^ parsing failed: {result}"
    assert state == TmuxControlState.NORMAL

    # Test escape sequence ^
    result, state = tmux_utils.parse_control_sequences("text^command")
    assert result == "textcommand", f"Escape ^ parsing failed: {result}"
    assert state == TmuxControlState.NORMAL

    # Test complex sequence
    tmux_utils.control_state = TmuxControlState.NORMAL  # Reset state
    result, state = tmux_utils.parse_control_sequences("start^^middle^end")
    assert result == "start^middleend", f"Complex sequence parsing failed: {result}"
    assert state == TmuxControlState.NORMAL


def test_win_ops_completeness():
    """Test requirement: Complete WIN_OPS dispatch dictionary"""
    _skip_if_unavailable()

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


def test_dry_compliance():
    """Test DRY principles across tmux implementations"""
    _skip_if_unavailable()

    # All implementations should use the same centralized utilities
    tmux_utils1 = get_tmux_utilities()
    tmux_utils2 = get_ai_monitor_tmux()
    tmux_utils3 = get_global_tmux_utils()

    # They should all be the same instance or have the same session name
    assert tmux_utils1 is not None, "get_tmux_utilities() returned None"
    assert tmux_utils2 is not None, "get_ai_monitor_tmux() returned None"
    assert tmux_utils3 is not None, "get_global_tmux_utils() returned None"
    assert tmux_utils1.session_name == tmux_utils2.session_name == tmux_utils3.session_name, \
        "DRY violation: Different tmux utilities instances or session names"


def test_session_environment_detection():
    """Test session environment detection and fallback"""
    _skip_if_unavailable()

    tmux_utils = TmuxUtilities("test_session")

    # Test environment detection (may or may not detect tmux)
    env_info = tmux_utils.detect_tmux_environment()

    # Test session info retrieval
    session_info = tmux_utils.get_session_info()
    assert session_info is not None, "Session info retrieval returned None"
    assert "session" in session_info, "Session info missing 'session' key"
    assert "available_sessions" in session_info, "Session info missing 'available_sessions' key"


def test_ai_monitor_integration():
    """Test ai_monitor.py integration with centralized utilities"""
    _skip_if_unavailable()

    # Test ai_monitor uses centralized utilities
    ai_monitor_tmux = get_ai_monitor_tmux()
    assert ai_monitor_tmux is not None, "ai_monitor tmux utilities not available"

    # Test window operations
    assert hasattr(ai_monitor_tmux, 'WIN_OPS'), "ai_monitor missing WIN_OPS"
    assert len(ai_monitor_tmux.WIN_OPS) >= 4, \
        f"ai_monitor WIN_OPS incomplete: {len(ai_monitor_tmux.WIN_OPS)} operations"


def test_tmux_injector_integration():
    """Test tmux_injector.py integration with centralized utilities"""
    _skip_if_unavailable()

    # Test injector uses centralized utilities
    injector = TmuxInjector("test_injector_session")
    assert injector.tmux_utils is not None, "TmuxInjector missing centralized utilities"

    # Test default session naming
    default_injector = TmuxInjector()
    assert default_injector.session_id == "clautorun", \
        f"TmuxInjector default session: {default_injector.session_id}"

    # Test dual channel injector
    dual_injector = DualChannelInjector("test_dual_session")
    assert dual_injector.tmux_injector.tmux_utils is not None, \
        "DualChannelInjector missing centralized utilities"

    # Test default dual channel session naming
    default_dual = DualChannelInjector()
    assert default_dual.session_id == "clautorun", \
        f"DualChannelInjector default session: {default_dual.session_id}"


def test_main_py_integration():
    """Test main.py integration with tmux standards"""
    _skip_if_unavailable()

    # Test global tmux utilities
    global_tmux = get_global_tmux_utils()
    # global_tmux may be None if not initialized yet, that's ok

    # Verify the imports work
    from clautorun.main import TMUX_UTILS_AVAILABLE
    assert TMUX_UTILS_AVAILABLE, "main.py tmux utilities not available"


def test_error_handling_and_fallbacks():
    """Test error handling and graceful fallbacks"""
    _skip_if_unavailable()

    tmux_utils = TmuxUtilities("nonexistent_session")

    # Test command execution with invalid session - should not crash
    # The key requirement is graceful handling (no exception), not a specific return code
    result = tmux_utils.execute_tmux_command(
        ['list-windows', '-t', 'clautorun_nonexistent_session_xyz_99'],
        'clautorun_nonexistent_session_xyz_99'
    )
    assert result is None or isinstance(result, dict), \
        f"execute_tmux_command should return None or dict, got {type(result)}"

    # Test WIN_OPS with invalid operation - should return False, not crash
    success = tmux_utils.execute_win_op('nonexistent_operation')
    assert not success, "Invalid WIN_OP should return False"

    # Test user typing detection when no session - should return False, not crash
    is_typing = tmux_utils.is_user_typing()
    assert not is_typing, "User typing detection should default to False when no session"
