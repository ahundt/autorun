#!/usr/bin/env python3
"""Test bashlex heredoc parsing behavior.

Verifies that bashlex correctly handles heredocs with quoted delimiters
and doesn't expose heredoc content as commands.
"""

import sys
from pathlib import Path

# Add src to path
plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root / 'src'))

try:
    import bashlex
    from bashlex import ast as bashlex_ast
    BASHLEX_AVAILABLE = True
except ImportError:
    BASHLEX_AVAILABLE = False
    bashlex = None
    bashlex_ast = None


class CommandExtractorVisitor(bashlex_ast.nodevisitor if bashlex_ast else object):
    """Visitor to extract command words from bashlex AST."""

    def __init__(self):
        self.commands = []
        self.all_words = []

    def visitcommand(self, node, parts):
        words = [p.word for p in parts if hasattr(p, 'kind') and p.kind == "word"]
        self.commands.append(words)
        self.all_words.extend(words)
        return True


def test_heredoc_parsing_basic():
    """Test bashlex can parse heredocs without errors."""
    if not BASHLEX_AVAILABLE:
        import pytest
        pytest.skip("bashlex not available")

    heredoc_cmd = """python3 << EOF
print("hello")
EOF"""

    # Should parse without errors
    parts = bashlex.parse(heredoc_cmd)
    assert len(parts) > 0, "Should parse at least one command"


def test_heredoc_quoted_delimiter():
    """Test bashlex handles quoted heredoc delimiters (e.g., << 'EOF')."""
    if not BASHLEX_AVAILABLE:
        import pytest
        pytest.skip("bashlex not available")

    # This used to fail in bashlex - verify our normalization works
    from clautorun.command_detection import _normalize_heredoc_delimiters

    heredoc_cmd = """python3 << 'EOF'
pattern = "grep"
EOF"""

    # After normalization, bashlex should parse it
    normalized = _normalize_heredoc_delimiters(heredoc_cmd)
    parts = bashlex.parse(normalized)

    assert len(parts) > 0, "Should parse heredoc with quoted delimiter after normalization"

    # Verify normalization removed quotes from delimiter
    assert "'EOF'" not in normalized, "Quotes should be removed from delimiter"
    assert "EOF" in normalized, "Delimiter should still be present"


def test_heredoc_content_not_exposed_as_commands():
    """Test that heredoc CONTENT is not extracted as separate commands.

    This is critical - if 'grep' appears inside heredoc content, it should
    NOT be extracted as a command.
    """
    if not BASHLEX_AVAILABLE:
        import pytest
        pytest.skip("bashlex not available")

    heredoc_cmd = """python3 << 'EOF'
import sys
pattern = "grep"
result = command_matches_pattern(test_cmd, pattern)
EOF"""

    # Normalize and parse
    from clautorun.command_detection import _normalize_heredoc_delimiters
    normalized = _normalize_heredoc_delimiters(heredoc_cmd)
    parts = bashlex.parse(normalized)

    # Extract all command words
    visitor = CommandExtractorVisitor()
    for part in parts:
        visitor.visit(part)

    # The PRIMARY command should be 'python3'
    assert 'python3' in visitor.all_words, "Should extract python3 as command"

    # CRITICAL: 'grep' should NOT be extracted as a command word
    # It's just content inside the heredoc
    assert 'grep' not in visitor.all_words, (
        f"'grep' should NOT be extracted as command - it's heredoc content\n"
        f"Extracted words: {visitor.all_words}"
    )

    # Neither should other Python keywords from heredoc content
    assert 'import' not in visitor.all_words, "Python keywords in heredoc should not be extracted"
    assert 'pattern' not in visitor.all_words, "Variable names in heredoc should not be extracted"


def test_heredoc_with_actual_user_command():
    """Test the exact heredoc that was causing false positives."""
    if not BASHLEX_AVAILABLE:
        import pytest
        pytest.skip("bashlex not available")

    # This is the actual command that revealed the bug
    heredoc_cmd = """python3 << 'EOF'
import sys
sys.path.insert(0, "plugins/clautorun/src")
from clautorun.command_detection import command_matches_pattern
test_cmd = "gemini extensions list | grep -A 2 -B 2 clautorun"
pattern = "grep"
result = command_matches_pattern(test_cmd, pattern)
print(f"Result: {result}")
EOF"""

    from clautorun.command_detection import _normalize_heredoc_delimiters
    normalized = _normalize_heredoc_delimiters(heredoc_cmd)
    parts = bashlex.parse(normalized)

    visitor = CommandExtractorVisitor()
    for part in parts:
        visitor.visit(part)

    # Verify primary command
    assert 'python3' in visitor.all_words, "Should extract python3"

    # Verify heredoc content NOT extracted
    heredoc_keywords = ['grep', 'import', 'sys', 'pattern', 'gemini', 'extensions']
    for keyword in heredoc_keywords:
        assert keyword not in visitor.all_words, (
            f"Heredoc content '{keyword}' should NOT be extracted as command\n"
            f"Extracted: {visitor.all_words}"
        )


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])
