#!/usr/bin/env python3
"""
Test context-aware command blocking - distinguish pipe usage from direct file operations.

**Problem**: Commands like `head`, `tail`, `grep`, `cat` were being blocked even when used
in pipes (e.g., `git diff | head -50`), which is a valid use case.

**Solution**: Use `_not_in_pipe` when predicate to only block these commands when used for
direct file reading, not when used as filters in pipes.

**Implementation**: The `_not_in_pipe` predicate in integrations.py checks:
1. Is command in a pipe? (has `|`) → return False (allow)
2. No file arguments (reading stdin)? → return False (allow)
3. Has file arguments? → return True (block)

Test cases verify the predicate logic for:
1. Valid pipe usage (should allow): `git diff | head -50`, `ps aux | grep pattern`
2. Direct file reading (should block): `head file.txt`, `grep pattern file.txt`
3. Edge cases: `head -50` (stdin), complex multi-pipe commands
"""

import pytest
import sys
from pathlib import Path

# Add src to path
plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root / 'src'))

from clautorun.integrations import _not_in_pipe


# Mock context class for testing
class MockCtx:
    """Mock EventContext for predicate testing."""
    def __init__(self, command):
        self.tool_input = {'command': command}


class TestNotInPipePredicate:
    """Test _not_in_pipe predicate logic."""

    def test_01_user_reported_bug_git_diff_pipe_head(self):
        """CRITICAL: User-reported bug - git diff | head should be allowed."""
        print("\n=== Test 1: User-reported bug (git diff | head -50) ===")

        cmd = "git diff crates/ui/src/gui.rs | head -50"
        ctx = MockCtx(cmd)
        result = _not_in_pipe(ctx)

        assert result is False, \
            "CRITICAL: git diff | head -50 must be allowed (predicate should return False)"

        print("✅ Test 1 passed: User-reported bug fixed")

    def test_02_head_in_pipe_allowed(self):
        """Allow: head in pipe."""
        print("\n=== Test 2: head in pipe ===")

        cmd = "git diff | head -50"
        ctx = MockCtx(cmd)
        result = _not_in_pipe(ctx)

        assert result is False, "head in pipe should be allowed (return False)"
        print("✅ Test 2 passed: head in pipe allowed")

    def test_03_head_on_file_blocked(self):
        """Block: head on file."""
        print("\n=== Test 3: head on file ===")

        cmd = "head -20 myfile.txt"
        ctx = MockCtx(cmd)
        result = _not_in_pipe(ctx)

        assert result is True, "head on file should be blocked (return True)"
        print("✅ Test 3 passed: head on file blocked")

    def test_04_head_stdin_allowed(self):
        """Allow: head with no file (reading stdin)."""
        print("\n=== Test 4: head reading stdin ===")

        cmd = "head -50"
        ctx = MockCtx(cmd)
        result = _not_in_pipe(ctx)

        assert result is False, "head with no file should be allowed (return False)"
        print("✅ Test 4 passed: head reading stdin allowed")

    def test_05_grep_in_pipe_allowed(self):
        """Allow: grep in pipe."""
        print("\n=== Test 5: grep in pipe ===")

        cmd = "ps aux | grep python"
        ctx = MockCtx(cmd)
        result = _not_in_pipe(ctx)

        assert result is False, "grep in pipe should be allowed (return False)"
        print("✅ Test 5 passed: grep in pipe allowed")

    def test_06_grep_on_file_blocked(self):
        """Block: grep on file."""
        print("\n=== Test 6: grep on file ===")

        cmd = "grep pattern file.txt"
        ctx = MockCtx(cmd)
        result = _not_in_pipe(ctx)

        assert result is True, "grep on file should be blocked (return True)"
        print("✅ Test 6 passed: grep on file blocked")

    def test_07_cat_in_pipe_allowed(self):
        """Allow: cat in pipe."""
        print("\n=== Test 7: cat in pipe ===")

        cmd = "cat file.txt | grep pattern"
        ctx = MockCtx(cmd)
        result = _not_in_pipe(ctx)

        assert result is False, "cat in pipe should be allowed (return False)"
        print("✅ Test 7 passed: cat in pipe allowed")

    def test_08_cat_on_file_blocked(self):
        """Block: cat on file."""
        print("\n=== Test 8: cat on file ===")

        cmd = "cat README.md"
        ctx = MockCtx(cmd)
        result = _not_in_pipe(ctx)

        assert result is True, "cat on file should be blocked (return True)"
        print("✅ Test 8 passed: cat on file blocked")

    def test_09_tail_in_pipe_allowed(self):
        """Allow: tail in pipe."""
        print("\n=== Test 9: tail in pipe ===")

        cmd = "git log | tail -20"
        ctx = MockCtx(cmd)
        result = _not_in_pipe(ctx)

        assert result is False, "tail in pipe should be allowed (return False)"
        print("✅ Test 9 passed: tail in pipe allowed")

    def test_10_tail_on_file_blocked(self):
        """Block: tail on file."""
        print("\n=== Test 10: tail on file ===")

        cmd = "tail -20 /var/log/syslog"
        ctx = MockCtx(cmd)
        result = _not_in_pipe(ctx)

        assert result is True, "tail on file should be blocked (return True)"
        print("✅ Test 10 passed: tail on file blocked")

    def test_11_complex_multi_pipe(self):
        """Allow: Complex multi-pipe command."""
        print("\n=== Test 11: Complex multi-pipe ===")

        cmd = "git log | grep 'bug' | head -10"
        ctx = MockCtx(cmd)
        result = _not_in_pipe(ctx)

        assert result is False, "Complex pipe should be allowed (return False)"
        print("✅ Test 11 passed: Complex multi-pipe allowed")

    def test_12_empty_command(self):
        """Edge case: Empty command."""
        print("\n=== Test 12: Empty command ===")

        cmd = ""
        ctx = MockCtx(cmd)
        result = _not_in_pipe(ctx)

        assert result is False, "Empty command should be allowed (return False)"
        print("✅ Test 12 passed: Empty command handled")


def run_all_tests():
    """Run all tests."""
    print("Running _not_in_pipe predicate tests...\n")

    test_suite = TestNotInPipePredicate()

    tests = [
        test_suite.test_01_user_reported_bug_git_diff_pipe_head,
        test_suite.test_02_head_in_pipe_allowed,
        test_suite.test_03_head_on_file_blocked,
        test_suite.test_04_head_stdin_allowed,
        test_suite.test_05_grep_in_pipe_allowed,
        test_suite.test_06_grep_on_file_blocked,
        test_suite.test_07_cat_in_pipe_allowed,
        test_suite.test_08_cat_on_file_blocked,
        test_suite.test_09_tail_in_pipe_allowed,
        test_suite.test_10_tail_on_file_blocked,
        test_suite.test_11_complex_multi_pipe,
        test_suite.test_12_empty_command,
    ]

    passed = 0
    failed = 0

    for test_func in tests:
        try:
            test_func()
            passed += 1
        except AssertionError as e:
            print(f"❌ {test_func.__name__} failed: {e}")
            failed += 1
        except Exception as e:
            print(f"❌ {test_func.__name__} error: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*60}")
    print(f"Predicate Tests: {passed} passed, {failed} failed")
    print(f"{'='*60}\n")

    return failed == 0


if __name__ == '__main__':
    success = run_all_tests()
    sys.exit(0 if success else 1)
