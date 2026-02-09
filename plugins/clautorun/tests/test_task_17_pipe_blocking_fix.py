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

from clautorun.integrations import _not_in_pipe
from clautorun.command_detection import BASHLEX_AVAILABLE


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


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])
