#!/usr/bin/env python3
"""TDD tests for Task #17: Pipe blocking fix with bashlex.

Verifies that:
1. Commands in pipes are ALLOWED (head/tail/grep/cat after |)
2. Direct file operations are BLOCKED (head/tail/grep/cat without pipe)
3. bashlex is used when available for accurate parsing
4. Fallback works when bashlex unavailable
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

# Add src to path
plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root / 'src'))

from autorun.integrations import _not_in_pipe
from autorun.command_detection import BASHLEX_AVAILABLE


def create_mock_context(command: str):
    """Create mock EventContext with command."""
    ctx = MagicMock()
    ctx.tool_input = {'command': command}
    return ctx


class TestPipeBlockingFix:
    """Test pipe detection with bashlex."""

    def test_bashlex_available(self):
        """Verify bashlex is installed and available."""
        assert BASHLEX_AVAILABLE, (
            "bashlex must be installed for pipe detection. "
            "Run: python3 -m pip install --break-system-packages bashlex"
        )

    def test_commands_in_pipe_allowed(self):
        """Test that head/tail/grep/cat in pipes are ALLOWED."""
        pipe_commands = [
            "git log | head -50",
            "git diff | tail -30",
            "ps aux | grep python",
            "ls -la | head -20",
            "cargo build 2>&1 | head -50",
            "uv run pytest 2>&1 | tail -100",
            "find . -name '*.py' | head -10",
            "git status | grep modified",
        ]

        for cmd in pipe_commands:
            ctx = create_mock_context(cmd)
            result = _not_in_pipe(ctx)
            assert result == False, (
                f"Command in pipe should be ALLOWED: {cmd}\n"
                f"_not_in_pipe() returned {result} (should be False for pipe commands)"
            )

    def test_direct_file_operations_blocked(self):
        """Test that direct head/tail/grep/cat are BLOCKED."""
        direct_commands = [
            "head file.txt",
            "tail /path/to/file",
            "grep 'pattern' file.py",
            "cat README.md",
            "head -50 somefile",
            "tail -n 100 logfile.log",
        ]

        for cmd in direct_commands:
            ctx = create_mock_context(cmd)
            result = _not_in_pipe(ctx)
            assert result == True, (
                f"Direct file operation should be BLOCKED: {cmd}\n"
                f"_not_in_pipe() returned {result} (should be True to block)"
            )

    def test_complex_pipe_chains(self):
        """Test complex pipe chains are properly detected."""
        complex_pipes = [
            "git log --oneline | head -20 | tail -5",
            "find . -name '*.md' | grep -v '.git' | head -10",
            "ps aux | grep python | grep -v grep",
            "uv run pytest --co -q 2>&1 | head -50",
        ]

        for cmd in complex_pipes:
            ctx = create_mock_context(cmd)
            result = _not_in_pipe(ctx)
            assert result == False, (
                f"Complex pipe chain should be ALLOWED: {cmd}\n"
                f"_not_in_pipe() returned {result}"
            )

    def test_redirection_not_pipe(self):
        """Test that redirections (>, >>) are not treated as pipes."""
        redirect_commands = [
            "head file.txt > output.txt",
            "tail -n 50 log.txt >> combined.log",
            "grep 'error' file.py > errors.txt",
        ]

        for cmd in redirect_commands:
            ctx = create_mock_context(cmd)
            result = _not_in_pipe(ctx)
            assert result == True, (
                f"Redirection should still BLOCK direct file ops: {cmd}\n"
                f"_not_in_pipe() returned {result}"
            )

    def test_edge_cases(self):
        """Test edge cases in pipe detection."""
        edge_cases = [
            # Command with | in string argument should NOT be treated as pipe
            # But echo has arguments, so it will be blocked (echo is not a file read command though)
            ("echo 'foo | bar'", True),  # Direct echo with args
            # Empty command
            ("", False),  # No command, allow
            # Command with pipe symbol in path (rare but possible)
            ("cat 'file|name.txt'", True),  # Direct cat, blocked
        ]

        for cmd, expected_blocked in edge_cases:
            ctx = create_mock_context(cmd)
            result = _not_in_pipe(ctx)
            assert result == expected_blocked, (
                f"Edge case failed: {cmd}\n"
                f"Expected blocked={expected_blocked}, got {result}"
            )

    def test_pipes_with_logical_operators(self):
        """Test pipe detection with || and && operators (REGRESSION TEST)."""
        commands_with_logical_ops = [
            # Pipe with || (logical OR) - SHOULD BE ALLOWED
            "gemini extensions list | grep autorun || echo 'Not found'",
            "git log | grep fix || exit 1",
            "ps aux | grep python || echo 'No python processes'",

            # Pipe with && (logical AND) - SHOULD BE ALLOWED
            "git diff | grep TODO && echo 'Found TODOs'",
            "ls -la | grep .txt && cat list.txt",

            # Complex: pipe with both || and && - SHOULD BE ALLOWED
            "cat file.txt | grep error && echo 'Errors found' || echo 'No errors'",

            # Pipe with grep -A/-B flags (context lines) - SHOULD BE ALLOWED
            "gemini extensions list | grep -A 2 -B 2 autorun || echo 'No autorun found'",
        ]

        for cmd in commands_with_logical_ops:
            ctx = create_mock_context(cmd)
            result = _not_in_pipe(ctx)
            assert result == False, (
                f"Command with pipe + logical operator should be ALLOWED: {cmd}\n"
                f"_not_in_pipe() returned {result} (should be False for piped commands)"
            )

    def test_comprehensive_grep_pipe_scenarios(self):
        """Comprehensive test of all grep pipe scenarios reported by users."""
        grep_pipe_commands = [
            # Basic pipes with grep
            "git log | grep fix",
            "ps aux | grep python",
            "ls -la | grep .md",

            # grep with multiple flags
            "docker ps | grep -i container",
            "find . | grep -v node_modules",
            "cat file.txt | grep -E 'pattern.*match'",

            # grep with context flags (-A, -B, -C)
            "git log | grep -A 5 commit",
            "cat error.log | grep -B 10 ERROR",
            "ps aux | grep -C 3 python",
            "gemini extensions list | grep -A 2 -B 2 autorun",

            # Multiple pipes with grep
            "cat file.txt | grep error | grep -v warning",
            "git log --oneline | grep fix | grep -i security",

            # Pipes with grep + redirection
            "cat file.txt | grep pattern > output.txt",
            "git log | grep fix >> results.log",

            # Pipes with grep + logical operators
            "cat file.txt | grep pattern && echo found",
            "git log | grep fix || echo 'no fixes'",
            "ps aux | grep python && kill -9 $(pidof python) || echo 'not running'",
        ]

        for cmd in grep_pipe_commands:
            ctx = create_mock_context(cmd)
            result = _not_in_pipe(ctx)
            assert result == False, (
                f"Grep in pipe should be ALLOWED: {cmd}\n"
                f"_not_in_pipe() returned {result}"
            )

    def test_comprehensive_head_tail_pipe_scenarios(self):
        """Comprehensive test of all head/tail pipe scenarios."""
        head_tail_pipe_commands = [
            # head in pipes
            "git log | head -10",
            "ls -la | head -n 20",
            "cat large_file.txt | head -100",
            "uv run pytest --co -q | head -50",

            # tail in pipes
            "git log | tail -10",
            "cat log.txt | tail -n 100",
            "dmesg | tail -50",

            # head/tail with other commands in pipe
            "git diff | head -100 | grep TODO",
            "cat file.txt | tail -50 | grep error",

            # head/tail with logical operators
            "git log | head -20 || echo 'empty'",
            "cat file.txt | tail -10 && echo 'success'",

            # Complex pipes with head/tail
            "find . -name '*.py' | head -100 | grep test",
            "git log --oneline | tail -50 | grep fix",
        ]

        for cmd in head_tail_pipe_commands:
            ctx = create_mock_context(cmd)
            result = _not_in_pipe(ctx)
            assert result == False, (
                f"head/tail in pipe should be ALLOWED: {cmd}\n"
                f"_not_in_pipe() returned {result}"
            )

    def test_comprehensive_cat_pipe_scenarios(self):
        """Comprehensive test of all cat pipe scenarios."""
        cat_pipe_commands = [
            # cat in pipes
            "cat file.txt | grep pattern",
            "cat *.log | grep error",
            "cat file1.txt file2.txt | head -50",

            # cat with flags in pipes
            "cat -n file.txt | grep '10:'",
            "cat -A file.txt | head -20",

            # cat in complex pipes
            "cat file.txt | grep error | wc -l",
            "cat *.md | grep TODO | head -100",

            # cat with logical operators
            "cat file.txt | grep pattern || echo 'not found'",
            "cat file.txt | head -10 && echo 'success'",
        ]

        for cmd in cat_pipe_commands:
            ctx = create_mock_context(cmd)
            result = _not_in_pipe(ctx)
            assert result == False, (
                f"cat in pipe should be ALLOWED: {cmd}\n"
                f"_not_in_pipe() returned {result}"
            )

    def test_command_detection_accuracy(self):
        """Test that command_matches_pattern correctly identifies actual commands vs data.

        This tests the command_detection.py module directly to ensure it uses
        bashlex AST parsing and doesn't do naive substring matching.
        """
        from autorun.command_detection import command_matches_pattern

        # Should MATCH (actual grep commands)
        actual_grep_commands = [
            ("grep pattern file.txt", "grep", True),
            ("sudo grep -r 'test' .", "grep", True),
            ("grep 'error' log.txt", "grep", True),
        ]

        for cmd, pattern, expected in actual_grep_commands:
            result = command_matches_pattern(cmd, pattern)
            assert result == expected, (
                f"Actual command should match: {cmd}\n"
                f"Pattern: {pattern}\n"
                f"Expected: {expected}, Got: {result}"
            )

        # Should NOT MATCH (grep in arguments/data/heredocs)
        non_grep_commands = [
            ("echo grep", "grep", False),  # grep is an argument to echo
            ("python3 -c \"pattern = 'grep'\"", "grep", False),  # grep in Python string
            ("cat << EOF\ngrep pattern\nEOF", "grep", False),  # grep in heredoc content
        ]

        for cmd, pattern, expected in non_grep_commands:
            result = command_matches_pattern(cmd, pattern)
            assert result == expected, (
                f"Non-command occurrence should NOT match: {cmd}\n"
                f"Pattern: {pattern}\n"
                f"Expected: {expected}, Got: {result}"
            )


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])
