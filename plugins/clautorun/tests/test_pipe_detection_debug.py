#!/usr/bin/env python3
"""Test pipe detection for real user commands.

Verifies that _not_in_pipe() correctly identifies piped commands,
including complex cases with logical operators (|| and &&).
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

# Add src to path
plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root / 'src'))

from clautorun.integrations import _not_in_pipe
from clautorun.command_detection import command_matches_pattern, BASHLEX_AVAILABLE


def create_mock_context(command: str):
    """Create mock EventContext with command."""
    ctx = MagicMock()
    ctx.tool_input = {'command': command}
    return ctx


class TestPipeDetectionRealWorld:
    """Test pipe detection with actual user commands that were problematic."""

    def test_user_reported_command(self):
        """Test the exact command user reported as blocked."""
        # This is the ACTUAL command that revealed the bug
        user_cmd = "gemini extensions list | grep -A 2 -B 2 clautorun || echo 'No clautorun found'"

        # Step 1: Pattern should match
        matches = command_matches_pattern(user_cmd, "grep")
        assert matches == True, f"'grep' should match in command: {user_cmd}"

        # Step 2: Should be detected as piped (return False to ALLOW)
        ctx = create_mock_context(user_cmd)
        not_in_pipe = _not_in_pipe(ctx)
        assert not_in_pipe == False, (
            f"grep IS in pipe, _not_in_pipe() should return False (allow)\n"
            f"Command: {user_cmd}\n"
            f"bashlex available: {BASHLEX_AVAILABLE}"
        )

    def test_pipe_with_logical_or(self):
        """Test pipes with || (logical OR) are correctly detected."""
        commands = [
            "git log | grep fix || echo 'not found'",
            "ps aux | grep python || exit 1",
            "cat file.txt | grep pattern || echo 'no match'",
        ]

        for cmd in commands:
            ctx = create_mock_context(cmd)
            result = _not_in_pipe(ctx)
            assert result == False, (
                f"Command with pipe + || should be ALLOWED\n"
                f"Command: {cmd}\n"
                f"_not_in_pipe() returned: {result} (should be False)"
            )

    def test_pipe_with_logical_and(self):
        """Test pipes with && (logical AND) are correctly detected."""
        commands = [
            "git diff | grep TODO && echo 'Found TODOs'",
            "cat file.txt | head -10 && echo 'success'",
            "ls -la | grep .txt && wc -l",
        ]

        for cmd in commands:
            ctx = create_mock_context(cmd)
            result = _not_in_pipe(ctx)
            assert result == False, (
                f"Command with pipe + && should be ALLOWED\n"
                f"Command: {cmd}\n"
                f"_not_in_pipe() returned: {result} (should be False)"
            )

    def test_grep_with_context_flags(self):
        """Test grep with -A/-B/-C flags in pipes (user's exact use case)."""
        commands = [
            "gemini extensions list | grep -A 2 -B 2 clautorun",
            "git log | grep -A 5 commit",
            "cat error.log | grep -B 10 ERROR",
            "ps aux | grep -C 3 python",
        ]

        for cmd in commands:
            ctx = create_mock_context(cmd)
            result = _not_in_pipe(ctx)
            assert result == False, (
                f"grep with context flags in pipe should be ALLOWED\n"
                f"Command: {cmd}\n"
                f"_not_in_pipe() returned: {result}"
            )

    def test_direct_commands_blocked(self):
        """Test that direct file operations ARE blocked (sanity check)."""
        commands = [
            "grep pattern file.txt",
            "head -50 somefile",
            "tail -100 logfile",
            "cat README.md",
        ]

        for cmd in commands:
            ctx = create_mock_context(cmd)
            result = _not_in_pipe(ctx)
            assert result == True, (
                f"Direct file operation should be BLOCKED\n"
                f"Command: {cmd}\n"
                f"_not_in_pipe() returned: {result} (should be True)"
            )

    def test_complex_pipeline_chains(self):
        """Test complex multi-stage pipelines with logical operators."""
        commands = [
            # Multiple pipes with logical OR
            "git log --oneline | head -20 | tail -5 || echo 'empty'",

            # Multiple pipes with logical AND
            "find . -name '*.py' | grep test | wc -l && echo 'counted'",

            # Mixed logical operators
            "cat file.txt | grep error && echo 'has errors' || echo 'no errors'",
        ]

        for cmd in commands:
            ctx = create_mock_context(cmd)
            result = _not_in_pipe(ctx)
            assert result == False, (
                f"Complex pipeline should be ALLOWED\n"
                f"Command: {cmd}\n"
                f"_not_in_pipe() returned: {result}"
            )

    def test_stderr_redirection_with_pipe(self):
        """Test 2>&1 stderr redirection combined with pipes."""
        commands = [
            "cargo build 2>&1 | head -50",
            "uv run pytest 2>&1 | tail -100",
            "npm install 2>&1 | grep error",
        ]

        for cmd in commands:
            ctx = create_mock_context(cmd)
            result = _not_in_pipe(ctx)
            assert result == False, (
                f"Command with stderr redirect + pipe should be ALLOWED\n"
                f"Command: {cmd}\n"
                f"_not_in_pipe() returned: {result}"
            )


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])
