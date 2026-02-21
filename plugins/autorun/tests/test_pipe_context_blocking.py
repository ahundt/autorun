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

from autorun.integrations import _not_in_pipe


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

    # === Heredoc tests (cat << is string construction, not file read) ===

    def test_13_cat_heredoc_in_commit(self):
        """Allow: cat <<'EOF' in git commit -m (HEREDOC string constructor)."""
        cmd = """git commit -m "$(cat <<'EOF'\nfeat: add feature\nEOF\n)\" """
        ctx = MockCtx(cmd)
        result = _not_in_pipe(ctx)
        assert result is False, "cat <<'EOF' in commit should be allowed (heredoc)"

    def test_14_cat_heredoc_standalone(self):
        """Allow: standalone cat << EOF (heredoc, reading from inline text)."""
        cmd = "cat << EOF\nhello world\nEOF"
        ctx = MockCtx(cmd)
        result = _not_in_pipe(ctx)
        assert result is False, "cat << EOF should be allowed (heredoc)"

    def test_15_cat_heredoc_quoted_double(self):
        """Allow: cat <<"EOF" (double-quoted heredoc delimiter)."""
        cmd = 'cat <<"EOF"\nsome content\nEOF'
        ctx = MockCtx(cmd)
        result = _not_in_pipe(ctx)
        assert result is False, 'cat <<"EOF" should be allowed (heredoc)'

    def test_16_cat_heredoc_dash(self):
        """Allow: cat <<-EOF (heredoc with indentation stripping)."""
        cmd = "cat <<-EOF\n\tindented content\n\tEOF"
        ctx = MockCtx(cmd)
        result = _not_in_pipe(ctx)
        assert result is False, "cat <<-EOF should be allowed (heredoc)"

    def test_17_cat_heredoc_chained_commands(self):
        """Allow: cat heredoc chained with && (full commit workflow)."""
        cmd = (
            'git add file.py && git commit -m "$(cat <<\'EOF\'\n'
            'feat: add new feature\nEOF\n)" && git status'
        )
        ctx = MockCtx(cmd)
        result = _not_in_pipe(ctx)
        assert result is False, "cat heredoc in chained commit should be allowed"

    def test_18_cat_file_still_blocked(self):
        """Block: cat on file is still blocked (not a heredoc)."""
        cmd = "cat /etc/passwd"
        ctx = MockCtx(cmd)
        result = _not_in_pipe(ctx)
        assert result is True, "cat /etc/passwd should be blocked (direct file read)"

    def test_19_cat_multiple_files_still_blocked(self):
        """Block: cat on multiple files is still blocked."""
        cmd = "cat file1.txt file2.txt"
        ctx = MockCtx(cmd)
        result = _not_in_pipe(ctx)
        assert result is True, "cat file1.txt file2.txt should be blocked"

    def test_20_head_heredoc_allowed(self):
        """Allow: head with heredoc (unlikely but valid)."""
        cmd = "head -5 << EOF\nline1\nline2\nline3\nEOF"
        ctx = MockCtx(cmd)
        result = _not_in_pipe(ctx)
        assert result is False, "head << EOF should be allowed (heredoc)"

    def test_21_cat_no_args_still_allowed(self):
        """Allow: bare cat with no args (reads stdin)."""
        cmd = "cat"
        ctx = MockCtx(cmd)
        result = _not_in_pipe(ctx)
        assert result is False, "bare cat should be allowed (stdin)"

    def test_22_cat_heredoc_no_space(self):
        """Allow: cat <<EOF with no space before delimiter."""
        cmd = "cat <<EOF\ncontent\nEOF"
        ctx = MockCtx(cmd)
        result = _not_in_pipe(ctx)
        assert result is False, "cat <<EOF (no space) should be allowed (heredoc)"

    def test_23_cat_flags_only_no_file(self):
        """Allow: cat -n with no file (reads stdin)."""
        cmd = "cat -n"
        ctx = MockCtx(cmd)
        result = _not_in_pipe(ctx)
        assert result is False, "cat -n (stdin with flags) should be allowed"

    def test_24_heredoc_piped_to_grep(self):
        """Allow: cat <<EOF | grep pattern (heredoc piped)."""
        cmd = "cat <<EOF | grep hello\nhello world\ngoodbye\nEOF"
        ctx = MockCtx(cmd)
        result = _not_in_pipe(ctx)
        assert result is False, "cat <<EOF | grep should be allowed (pipe + heredoc)"

    def test_25_command_substitution_cat_file(self):
        """Block: echo $(cat file.txt) — cat reads a file, not heredoc."""
        cmd = "echo $(cat file.txt)"
        ctx = MockCtx(cmd)
        result = _not_in_pipe(ctx)
        assert result is True, "echo $(cat file.txt) should be blocked (file read)"

    def test_26_no_tool_input_attribute(self):
        """Edge case: context object without tool_input."""
        import types
        ctx = types.SimpleNamespace()  # No tool_input attribute
        result = _not_in_pipe(ctx)
        assert result is False, "Missing tool_input should be allowed (fail open)"

    def test_27_none_command(self):
        """Edge case: command is None."""
        ctx = MockCtx(None)
        # tool_input.get("command", "") returns None, which is falsy
        result = _not_in_pipe(ctx)
        assert result is False, "None command should be allowed (fail open)"

    def test_28_malformed_quotes_fallback(self):
        """Edge case: malformed quotes trigger shlex fallback to str.split."""
        cmd = "cat 'unclosed quote file.txt"
        ctx = MockCtx(cmd)
        result = _not_in_pipe(ctx)
        # shlex.split will fail, falls back to cmd.split()
        # Has non-flag args so should block
        assert result is True, "Malformed quotes with file arg should still block"

    def test_29_cat_heredoc_in_subshell(self):
        """Allow: cat heredoc inside $() subshell."""
        cmd = 'VAR="$(cat <<EOF\nvalue\nEOF\n)"'
        ctx = MockCtx(cmd)
        result = _not_in_pipe(ctx)
        assert result is False, "cat heredoc in $() subshell should be allowed"

    def test_30_git_diff_pipe_tail_still_works(self):
        """Regression: pipe detection still works after heredoc changes."""
        cmd = "cargo test 2>&1 | tail -100"
        ctx = MockCtx(cmd)
        result = _not_in_pipe(ctx)
        assert result is False, "pipe with tail should still be allowed"


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
        test_suite.test_13_cat_heredoc_in_commit,
        test_suite.test_14_cat_heredoc_standalone,
        test_suite.test_15_cat_heredoc_quoted_double,
        test_suite.test_16_cat_heredoc_dash,
        test_suite.test_17_cat_heredoc_chained_commands,
        test_suite.test_18_cat_file_still_blocked,
        test_suite.test_19_cat_multiple_files_still_blocked,
        test_suite.test_20_head_heredoc_allowed,
        test_suite.test_21_cat_no_args_still_allowed,
        test_suite.test_22_cat_heredoc_no_space,
        test_suite.test_23_cat_flags_only_no_file,
        test_suite.test_24_heredoc_piped_to_grep,
        test_suite.test_25_command_substitution_cat_file,
        test_suite.test_26_no_tool_input_attribute,
        test_suite.test_27_none_command,
        test_suite.test_28_malformed_quotes_fallback,
        test_suite.test_29_cat_heredoc_in_subshell,
        test_suite.test_30_git_diff_pipe_tail_still_works,
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
