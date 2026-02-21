#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test ! (bang) operator syntax in markdown command files

The ! operator allows Pre-Prompt Execution where shell commands are executed
before LLM reasoning and the output is injected into the prompt context.

Correct syntax:  ! command
Incorrect syntax: !`command` (backticks are wrong)
"""
import pytest
from pathlib import Path


class TestBangOperatorSyntax:
    """Test ! operator syntax in command markdown files"""

    @pytest.mark.unit
    def test_tabs_md_uses_correct_bang_syntax(self):
        """Test tabs.md uses correct ! syntax (not backticks)"""
        tabs_md = Path(__file__).parent.parent / "commands" / "tabs.md"
        content = tabs_md.read_text()

        # Should have ! operator
        assert "! tmux list-sessions" in content, \
            "tabs.md should have ! operator for tmux command"

        # Should NOT have incorrect !` syntax
        assert "!`" not in content, \
            "tabs.md should not use !` syntax (backticks are incorrect)"

    @pytest.mark.unit
    def test_tabw_md_uses_correct_bang_syntax(self):
        """Test tabw.md uses correct ! syntax (not backticks)"""
        tabw_md = Path(__file__).parent.parent / "commands" / "tabw.md"
        content = tabw_md.read_text()

        # Should have ! operator
        assert "! tmux list-sessions" in content, \
            "tabw.md should have ! operator for tmux command"

        # Should NOT have incorrect !` syntax
        assert "!`" not in content, \
            "tabw.md should not use !` syntax (backticks are incorrect)"

    @pytest.mark.unit
    def test_no_command_files_use_incorrect_bang_syntax(self):
        """Test no command markdown files use incorrect !` syntax.

        Note: !`command` (backtick after bang) is valid for multi-line inline
        execution blocks (e.g., !`python3 -c "...multiline code..."`).
        Files that use this pattern for intentional multi-line execution
        are excluded.
        """
        commands_dir = Path(__file__).parent.parent / "commands"

        # Files that intentionally use !` for multi-line inline execution blocks
        # These use !`python3 -c "..." syntax for embedded Python scripts
        multiline_execution_files = {
            "task-status.md", "task-ignore.md", "restart-daemon.md",
        }

        incorrect_files = []
        for md_file in commands_dir.glob("*.md"):
            # Skip documentation files and known multi-line execution files
            if "help" in md_file.name.lower() or "guide" in md_file.name.lower():
                continue
            if md_file.name in multiline_execution_files:
                continue

            content = md_file.read_text()
            # Check for incorrect syntax (excluding markdown code blocks)
            lines = content.split('\n')
            for i, line in enumerate(lines, 1):
                # Skip code blocks
                if '```' in line:
                    continue
                # Check for incorrect syntax
                if line.strip().startswith('!`'):
                    incorrect_files.append(f"{md_file.name}:{i}")

        assert len(incorrect_files) == 0, \
            f"Found incorrect !` syntax in: {', '.join(incorrect_files)}"

    @pytest.mark.unit
    def test_bang_operator_documentation_exists(self):
        """Test bang operator is documented in plugin help"""
        help_md = Path(__file__).parent.parent / "commands" / "claude-code-plugin-help.md"
        content = help_md.read_text()

        # Check for documentation sections
        assert "Embedded Execution" in content or "! Operator" in content, \
            "Documentation should explain ! operator"
        assert "Pre-Prompt Execution" in content, \
            "Documentation should mention Pre-Prompt Execution"


# Run with: python3 -m pytest tests/test_bang_syntax.py -v --override-ini='addopts='
