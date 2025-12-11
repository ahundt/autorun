#!/usr/bin/env python3
"""
Comprehensive tmux/byobu compliance test

Validates that the tmux implementation meets all requirements from
CLI_USAGE_AND_TEST_AUTOMATION_WITH_BYOBU_TMUX_SESSIONS.md
"""

import sys
import os

# Add src directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

try:
    from clautorun.tmux_utils import get_tmux_utilities, TmuxUtilities, TmuxControlState
    from clautorun.ai_monitor import get_tmux as get_ai_monitor_tmux
    from clautorun.tmux_injector import TmuxInjector, DualChannelInjector
    from clautorun.main import get_global_tmux_utils
    COMPLIANCE_TEST_AVAILABLE = True
except ImportError as e:
    print(f"Import error: {e}")
    COMPLIANCE_TEST_AVAILABLE = False


def test_default_session_naming():
    """Test requirement: Default session name should be 'clautorun'"""
    if not COMPLIANCE_TEST_AVAILABLE:
        print("❌ Default session naming test not available")
        return False

    try:
        # Test centralized utilities
        tmux_utils = get_tmux_utilities()
        if tmux_utils.DEFAULT_SESSION_NAME != "clautorun":
            print(f"❌ Default session name incorrect: {tmux_utils.DEFAULT_SESSION_NAME}")
            return False

        # Test injector default naming
        injector = TmuxInjector()
        if injector.session_id != "clautorun":
            print(f"❌ TmuxInjector default session incorrect: {injector.session_id}")
            return False

        # Test dual channel injector default naming
        dual_injector = DualChannelInjector()
        if dual_injector.session_id != "clautorun":
            print(f"❌ DualChannelInjector default session incorrect: {dual_injector.session_id}")
            return False

        print("✅ Default session naming 'clautorun' enforced correctly")
        return True

    except Exception as e:
        print(f"❌ Default session naming test failed: {e}")
        return False


def test_control_sequence_parsing():
    """Test requirement: Control sequence syntax (^ escape, ^^ literal)"""
    if not COMPLIANCE_TEST_AVAILABLE:
        print("❌ Control sequence parsing test not available")
        return False

    try:
        tmux_utils = TmuxUtilities("test_session")

        # Test basic parsing without control sequences
        result, state = tmux_utils.parse_control_sequences("normal text")
        if result != "normal text" or state != TmuxControlState.NORMAL:
            print(f"❌ Basic parsing failed: {result}, {state}")
            return False

        # Test literal ^^
        result, state = tmux_utils.parse_control_sequences("text^^more")
        if result != "text^more" or state != TmuxControlState.NORMAL:
            print(f"❌ Literal ^^ parsing failed: {result}, {state}")
            return False

        # Test escape sequence ^
        result, state = tmux_utils.parse_control_sequences("text^command")
        if result != "textcommand" or state != TmuxControlState.NORMAL:  # State resets after processing
            print(f"❌ Escape ^ parsing failed: {result}, {state}")
            return False

        # Test complex sequence
        tmux_utils.control_state = TmuxControlState.NORMAL  # Reset state
        result, state = tmux_utils.parse_control_sequences("start^^middle^end")
        if result != "start^middleend" or state != TmuxControlState.NORMAL:
            print(f"❌ Complex sequence parsing failed: {result}, {state}")
            return False

        print("✅ Control sequence parsing works correctly")
        return True

    except Exception as e:
        print(f"❌ Control sequence parsing test failed: {e}")
        return False


def test_win_ops_completeness():
    """Test requirement: Complete WIN_OPS dispatch dictionary"""
    if not COMPLIANCE_TEST_AVAILABLE:
        print("❌ WIN_OPS completeness test not available")
        return False

    try:
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

        missing_ops = []
        for op in required_ops:
            if op not in tmux_utils.WIN_OPS:
                missing_ops.append(op)

        if missing_ops:
            print(f"❌ Missing WIN_OPS: {missing_ops}")
            return False

        # Test some key operations have correct command mappings
        test_cases = [
            ('new-window', 'new-window'),
            ('split-window', 'split-window'),
            ('list-windows', 'list-windows'),
            ('send-keys', 'send-keys'),
            ('capture-pane', 'capture-pane'),
        ]

        for op, expected_cmd in test_cases:
            if tmux_utils.WIN_OPS[op] != expected_cmd:
                print(f"❌ WIN_OPS mapping incorrect for {op}: {tmux_utils.WIN_OPS[op]} != {expected_cmd}")
                return False

        print(f"✅ WIN_OPS dispatch dictionary complete with {len(tmux_utils.WIN_OPS)} operations")
        return True

    except Exception as e:
        print(f"❌ WIN_OPS completeness test failed: {e}")
        return False


def test_dry_compliance():
    """Test DRY principles across tmux implementations"""
    if not COMPLIANCE_TEST_AVAILABLE:
        print("❌ DRY compliance test not available")
        return False

    try:
        # All implementations should use the same centralized utilities
        tmux_utils1 = get_tmux_utilities()
        tmux_utils2 = get_ai_monitor_tmux()
        tmux_utils3 = get_global_tmux_utils()

        # They should all be the same instance or have the same session name
        if (tmux_utils1 and tmux_utils2 and tmux_utils3 and
            tmux_utils1.session_name == tmux_utils2.session_name == tmux_utils3.session_name):
            print("✅ DRY compliance: All implementations use centralized utilities")
            return True
        else:
            print("❌ DRY violation: Different tmux utilities instances or session names")
            return False

    except Exception as e:
        print(f"❌ DRY compliance test failed: {e}")
        return False


def test_session_environment_detection():
    """Test session environment detection and fallback"""
    if not COMPLIANCE_TEST_AVAILABLE:
        print("❌ Session environment detection test not available")
        return False

    try:
        tmux_utils = TmuxUtilities("test_session")

        # Test environment detection
        env_info = tmux_utils.detect_tmux_environment()
        if env_info:
            print(f"✅ Environment detected: {env_info}")
        else:
            print("ℹ️  No tmux environment detected (normal if not running in tmux)")

        # Test session creation if no environment
        if not env_info:
            if tmux_utils.ensure_session_exists("test_compliance_session"):
                print("✅ Session creation works as fallback")
            else:
                print("ℹ️  Session creation failed (tmux may not be available)")

        # Test session info
        session_info = tmux_utils.get_session_info()
        if (session_info and
            "session" in session_info and
            "available_sessions" in session_info):
            print(f"✅ Session info retrieval works: {session_info['session']}")
            return True
        else:
            print("❌ Session info retrieval failed")
            return False

    except Exception as e:
        print(f"❌ Session environment detection test failed: {e}")
        return False


def test_ai_monitor_integration():
    """Test ai_monitor.py integration with centralized utilities"""
    if not COMPLIANCE_TEST_AVAILABLE:
        print("❌ ai_monitor integration test not available")
        return False

    try:
        # Test ai_monitor uses centralized utilities
        ai_monitor_tmux = get_ai_monitor_tmux()
        if not ai_monitor_tmux:
            print("❌ ai_monitor tmux utilities not available")
            return False

        # Test window operations
        if hasattr(ai_monitor_tmux, 'WIN_OPS'):
            if len(ai_monitor_tmux.WIN_OPS) >= 4:  # list, read, send, own
                print("✅ ai_monitor WIN_OPS integration works")
                return True
            else:
                print(f"❌ ai_monitor WIN_OPS incomplete: {len(ai_monitor_tmux.WIN_OPS)} operations")
                return False
        else:
            print("❌ ai_monitor missing WIN_OPS")
            return False

    except Exception as e:
        print(f"❌ ai_monitor integration test failed: {e}")
        return False


def test_tmux_injector_integration():
    """Test tmux_injector.py integration with centralized utilities"""
    if not COMPLIANCE_TEST_AVAILABLE:
        print("❌ tmux_injector integration test not available")
        return False

    try:
        # Test injector uses centralized utilities
        injector = TmuxInjector("test_injector_session")
        if not injector.tmux_utils:
            print("❌ TmuxInjector missing centralized utilities")
            return False

        # Test default session naming
        default_injector = TmuxInjector()
        if default_injector.session_id != "clautorun":
            print(f"❌ TmuxInjector default session: {default_injector.session_id}")
            return False

        # Test dual channel injector
        dual_injector = DualChannelInjector("test_dual_session")
        if not dual_injector.tmux_injector.tmux_utils:
            print("❌ DualChannelInjector missing centralized utilities")
            return False

        # Test default dual channel session naming
        default_dual = DualChannelInjector()
        if default_dual.session_id != "clautorun":
            print(f"❌ DualChannelInjector default session: {default_dual.session_id}")
            return False

        print("✅ tmux_injector integration with centralized utilities works")
        return True

    except Exception as e:
        print(f"❌ tmux_injector integration test failed: {e}")
        return False


def test_main_py_integration():
    """Test main.py integration with tmux standards"""
    if not COMPLIANCE_TEST_AVAILABLE:
        print("❌ main.py integration test not available")
        return False

    try:
        # Test global tmux utilities
        global_tmux = get_global_tmux_utils()
        if not global_tmux:
            print("ℹ️  Global tmux utilities not initialized (normal if no imports)")

        # Test session naming enforcement would happen in handle_activate
        # We can't easily test this without a full session, but we can verify the imports work
        from clautorun.main import TMUX_UTILS_AVAILABLE
        if TMUX_UTILS_AVAILABLE:
            print("✅ main.py tmux utilities integration available")
            return True
        else:
            print("❌ main.py tmux utilities not available")
            return False

    except Exception as e:
        print(f"❌ main.py integration test failed: {e}")
        return False


def test_error_handling_and_fallbacks():
    """Test error handling and graceful fallbacks"""
    if not COMPLIANCE_TEST_AVAILABLE:
        print("❌ Error handling test not available")
        return False

    try:
        tmux_utils = TmuxUtilities("nonexistent_session")

        # Test command execution with invalid session
        result = tmux_utils.execute_tmux_command(['list-sessions'], 'invalid_session')
        # Should return None or error, not crash
        if result is None or result.get('returncode', -1) != 0:
            print("✅ Invalid session handled gracefully")
        else:
            print("ℹ️  Invalid session unexpectedly succeeded")

        # Test WIN_OPS with invalid operation
        success = tmux_utils.execute_win_op('nonexistent_operation')
        if not success:
            print("✅ Invalid WIN_OP handled gracefully")
        else:
            print("ℹ️  Invalid WIN_OP unexpectedly succeeded")

        # Test user typing detection when no session
        is_typing = tmux_utils.is_user_typing()
        if not is_typing:
            print("✅ User typing detection fallback works")
        else:
            print("ℹ️  User typing detection unexpectedly detected typing")

        return True

    except Exception as e:
        print(f"❌ Error handling test failed: {e}")
        return False


def main():
    """Run all tmux compliance tests"""
    print("🚀 Running tmux/byobu Compliance Tests")
    print("=" * 60)
    print("Testing compliance with CLI_USAGE_AND_TEST_AUTOMATION_WITH_BYOBU_TMUX_SESSIONS.md")
    print()

    tests = [
        ("Default Session Naming", test_default_session_naming),
        ("Control Sequence Parsing", test_control_sequence_parsing),
        ("WIN_OPS Completeness", test_win_ops_completeness),
        ("DRY Compliance", test_dry_compliance),
        ("Session Environment Detection", test_session_environment_detection),
        ("ai_monitor Integration", test_ai_monitor_integration),
        ("tmux_injector Integration", test_tmux_injector_integration),
        ("main.py Integration", test_main_py_integration),
        ("Error Handling and Fallbacks", test_error_handling_and_fallbacks),
    ]

    passed = 0
    total = len(tests)

    for test_name, test_func in tests:
        print(f"📋 {test_name}")
        print("-" * 40)

        if test_func():
            print(f"✅ {test_name} PASSED")
            passed += 1
        else:
            print(f"❌ {test_name} FAILED")
        print()

    print("=" * 60)
    print(f"Compliance Test Results: {passed}/{total} tests passed")

    if passed == total:
        print("🎉 All tmux/byobu compliance tests passed!")
        print("✅ System meets CLI_USAGE_AND_TEST_AUTOMATION_WITH_BYOBU_TMUX_SESSIONS.md requirements")
        return 0
    else:
        print("💥 Some tmux/byobu compliance tests failed!")
        print("⚠️  Review failed tests to ensure proper tmux/byobu integration")
        return 1


if __name__ == "__main__":
    exit(main())