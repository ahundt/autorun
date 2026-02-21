#!/usr/bin/env python3
"""Critical bug tests: Naive string matching blocks commands with patterns in data.

BUG DESCRIPTION:
The hook is doing naive string matching on the ENTIRE Bash command string,
blocking commands that contain "grep", "head", "tail", "cat" ANYWHERE - even
in heredocs, string literals, comments, or Python code.

ROOT CAUSE:
The hook checks if pattern matches command using command_matches_pattern(),
which should use bashlex AST parsing. However, when the command contains
heredocs or complex structures, the pattern matching is being applied to
the ENTIRE command string including data content.

CRITICAL IMPACT:
- Blocks legitimate development commands like test scripts
- Blocks commands with "grep" in Python string literals
- Blocks heredocs containing command names as data
- Makes the plugin unusable for certain workflows

EXAMPLES OF BLOCKED COMMANDS:
1. `python3 << 'EOF' ... pattern = "grep" ... EOF` - BLOCKED (grep in Python string)
2. `echo "Use Grep tool instead of grep"` - BLOCKED (grep in echo argument)
3. `gemini extensions list | grep autorun || echo "..."` - BLOCKED (grep in logical OR)
"""

import sys
from pathlib import Path

# Add src to path
plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root / 'src'))

from autorun.command_detection import command_matches_pattern, BASHLEX_AVAILABLE


class TestNaiveStringMatchingBug:
    """Test that command_matches_pattern doesn't do naive substring matching."""

    def test_bashlex_available(self):
        """Verify bashlex is installed (required for accurate command detection)."""
        assert BASHLEX_AVAILABLE, (
            "bashlex must be installed for accurate command detection. "
            "Run: python3 -m pip install --break-system-packages bashlex"
        )

    def test_heredoc_grep_not_matched(self):
        """Heredoc with 'grep' in Python code should NOT match pattern 'grep'."""
        # This is the ACTUAL command that got blocked
        heredoc_cmd = """python3 << 'EOF'
import sys
sys.path.insert(0, "plugins/autorun/src")
from autorun.command_detection import command_matches_pattern
test_cmd = "gemini extensions list | grep -A 2 -B 2 autorun"
pattern = "grep"
result = command_matches_pattern(test_cmd, pattern)
print(f"Result: {result}")
EOF"""

        result = command_matches_pattern(heredoc_cmd, "grep")
        assert result == False, (
            f"Heredoc with 'grep' in Python string should NOT match pattern 'grep'\n"
            f"Command: {heredoc_cmd[:100]}...\n"
            f"Pattern: grep\n"
            f"Result: {result} (expected False)"
        )

    def test_heredoc_head_not_matched(self):
        """Heredoc with 'head' in Python code should NOT match pattern 'head'."""
        heredoc_cmd = """python3 << 'EOF'
if 'head' in line:
    print(line)
EOF"""

        result = command_matches_pattern(heredoc_cmd, "head")
        assert result == False, (
            f"Heredoc with 'head' in Python code should NOT match pattern 'head'\n"
            f"Result: {result}"
        )

    def test_heredoc_tail_not_matched(self):
        """Heredoc with 'tail' in content should NOT match pattern 'tail'."""
        heredoc_cmd = """cat << 'EOF' > file.txt
Use tail -n 100 to see last 100 lines
EOF"""

        result = command_matches_pattern(heredoc_cmd, "tail")
        assert result == False, (
            f"Heredoc content with 'tail' should NOT match pattern 'tail'\n"
            f"Result: {result}"
        )

    def test_echo_grep_argument_not_matched(self):
        """echo command with 'grep' as argument should NOT match pattern 'grep'."""
        echo_cmds = [
            "echo 'Use Grep tool instead of grep'",
            "echo \"grep pattern file.txt\"",
            "printf 'grep -r pattern .'",
        ]

        for cmd in echo_cmds:
            result = command_matches_pattern(cmd, "grep")
            assert result == False, (
                f"echo with 'grep' argument should NOT match pattern 'grep'\n"
                f"Command: {cmd}\n"
                f"Result: {result}"
            )

    def test_python_string_literals_not_matched(self):
        """Python commands with grep/head/tail in string literals should NOT match."""
        python_cmds = [
            ('python3 -c "print(\'grep pattern file.txt\')"', "grep"),
            ('python3 -c \'result = "head -20"\'', "head"),
            ('python3 -c "cmd = \'tail -n 100\'"', "tail"),
        ]

        for cmd, pattern in python_cmds:
            result = command_matches_pattern(cmd, pattern)
            assert result == False, (
                f"Python string literal should NOT match pattern '{pattern}'\n"
                f"Command: {cmd}\n"
                f"Result: {result}"
            )

    def test_comments_not_matched(self):
        """Comments with command names should NOT trigger matches."""
        comment_cmds = [
            ("ls -la  # Use grep to filter", "grep"),
            ("git status  # Then head to see first 20 lines", "head"),
            ("cat file.txt  # Could use tail instead", "tail"),
        ]

        for cmd, pattern in comment_cmds:
            result = command_matches_pattern(cmd, pattern)
            assert result == False, (
                f"Comment with '{pattern}' should NOT match pattern '{pattern}'\n"
                f"Command: {cmd}\n"
                f"Result: {result}"
            )

    def test_actual_commands_do_match(self):
        """Verify that ACTUAL grep/head/tail commands DO match (sanity check)."""
        actual_commands = [
            ("grep pattern file.txt", "grep"),
            ("head -50 file.txt", "head"),
            ("tail -n 100 log.txt", "tail"),
            ("cat README.md", "cat"),
            ("sudo grep -r 'test' .", "grep"),
        ]

        for cmd, pattern in actual_commands:
            result = command_matches_pattern(cmd, pattern)
            assert result == True, (
                f"Actual {pattern} command should match pattern '{pattern}'\n"
                f"Command: {cmd}\n"
                f"Result: {result}"
            )

    def test_piped_commands_do_match(self):
        """Verify that grep/head/tail in pipes DO match the pattern.

        Note: The _not_in_pipe() predicate will then ALLOW these commands.
        This test just verifies that command_matches_pattern() correctly
        identifies the command, regardless of pipe context.
        """
        piped_commands = [
            ("git log | grep fix", "grep"),
            ("ls -la | head -20", "head"),
            ("cat file.txt | tail -50", "tail"),
            ("gemini extensions list | grep -A 2 -B 2 autorun || echo 'Not found'", "grep"),
        ]

        for cmd, pattern in piped_commands:
            result = command_matches_pattern(cmd, pattern)
            assert result == True, (
                f"Command in pipe should still match pattern '{pattern}'\n"
                f"Command: {cmd}\n"
                f"Result: {result}\n"
                f"Note: _not_in_pipe() predicate will ALLOW this, but pattern should match"
            )


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])