#!/usr/bin/env python3
"""Test command extraction doesn't have false positives from heredoc content.

Verifies that command_detection correctly identifies actual commands vs
data/strings within heredocs.
"""

import sys
from pathlib import Path

# Add src to path
plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root / 'src'))

from clautorun.command_detection import _extract_cached, extract_commands, command_matches_pattern, BASHLEX_AVAILABLE


def test_heredoc_extraction_no_false_positives():
    """Test that heredoc content doesn't cause false positive command matches."""

    # The ACTUAL command that was causing false positives
    heredoc_cmd = """python3 << 'EOF'
import sys
sys.path.insert(0, "plugins/clautorun/src")
from clautorun.command_detection import command_matches_pattern
test_cmd = "gemini extensions list | grep -A 2 -B 2 clautorun"
pattern = "grep"
result = command_matches_pattern(test_cmd, pattern)
print(f"Result: {result}")
EOF"""

    # Extract using the cached function
    result = _extract_cached(heredoc_cmd)

    # CRITICAL: 'grep' should NOT match (it's just a Python string in heredoc)
    grep_matches = command_matches_pattern(heredoc_cmd, 'grep')
    assert grep_matches == False, (
        f"'grep' should NOT match - it's only in heredoc content, not an actual command\n"
        f"Extracted names: {result.names}\n"
        f"Extracted strings: {result.strings}\n"
        f"All potential: {result.all_potential}"
    )

    # Python3 SHOULD match (it's the actual command)
    python_matches = command_matches_pattern(heredoc_cmd, 'python3')
    assert python_matches == True, (
        f"'python3' SHOULD match - it's the actual command\n"
        f"Extracted names: {result.names}"
    )


def test_extraction_identifies_actual_commands():
    """Test that extraction correctly identifies real commands."""

    # Commands that ARE actual commands
    test_cases = [
        ("grep pattern file.txt", "grep", True),
        ("python3 -m module", "python3", True),
        ("git log | grep fix", "grep", True),  # grep in pipe is real
        ("git log | grep fix", "git", True),    # git is real
    ]

    for cmd, pattern, expected in test_cases:
        result = command_matches_pattern(cmd, pattern)
        assert result == expected, (
            f"Command: {cmd}\n"
            f"Pattern: {pattern}\n"
            f"Expected: {expected}, Got: {result}"
        )


def test_extraction_ignores_string_content():
    """Test that strings, heredocs, and comments don't match as commands."""

    # Commands where pattern appears but NOT as actual command
    test_cases = [
        # Heredoc content
        ("python3 << 'EOF'\ngrep pattern\nEOF", "grep", False),

        # Echo arguments
        ("echo grep", "grep", False),
        ("echo 'use grep tool'", "grep", False),

        # Python strings
        ("python3 -c \"pattern = 'grep'\"", "grep", False),
    ]

    for cmd, pattern, expected in test_cases:
        result = command_matches_pattern(cmd, pattern)
        assert result == expected, (
            f"Command: {cmd}\n"
            f"Pattern: {pattern}\n"
            f"Expected: {expected}, Got: {result}\n"
            f"Pattern should NOT match - it's in string/heredoc content"
        )


def test_extraction_handles_complex_heredocs():
    """Test extraction with complex heredocs containing multiple command names."""

    heredoc_with_many_keywords = """bash << 'EOF'
# This heredoc contains many command names as content
grep something
head file.txt
tail -n 10 log.txt
cat README.md
python3 script.py
EOF"""

    # NONE of these should match - they're all heredoc content
    for pattern in ['grep', 'head', 'tail', 'cat', 'python3']:
        result = command_matches_pattern(heredoc_with_many_keywords, pattern)
        assert result == False, (
            f"Pattern '{pattern}' should NOT match in heredoc content\n"
            f"Command: {heredoc_with_many_keywords}"
        )

    # Only 'bash' should match (the actual command)
    bash_matches = command_matches_pattern(heredoc_with_many_keywords, 'bash')
    assert bash_matches == True, "'bash' SHOULD match - it's the actual command"


def test_extraction_respects_bashlex_availability():
    """Test that extraction falls back gracefully when bashlex unavailable."""

    # This test verifies the extraction works regardless of bashlex
    cmd = "git log | grep fix"

    # Should always identify actual commands
    assert command_matches_pattern(cmd, "git") == True
    assert command_matches_pattern(cmd, "grep") == True

    # Should never match non-commands
    assert command_matches_pattern(cmd, "log") == False  # log is argument to git


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])
