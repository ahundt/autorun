#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test Claude's documented dynamic-context syntax in command files."""
import pytest
from pathlib import Path


class TestBangOperatorSyntax:
    """Test ! operator syntax in command markdown files"""

    @pytest.mark.unit
    def test_tabs_md_uses_dynamic_context_syntax(self):
        """The tabs command must use Claude's `!`command`` syntax."""
        tabs_md = Path(__file__).parent.parent / "commands" / "tabs.md"
        content = tabs_md.read_text(encoding="utf-8")

        assert "!`tmux list-sessions" in content

    @pytest.mark.unit
    def test_tabw_md_uses_dynamic_context_syntax(self):
        """The tab-window command must use Claude's dynamic context syntax."""
        tabw_md = Path(__file__).parent.parent / "commands" / "tabw.md"
        content = tabw_md.read_text(encoding="utf-8")

        assert "!`tmux list-sessions" in content

    @pytest.mark.unit
    def test_executable_command_lines_use_backticks(self):
        """Bare `! command` lines are inert and must not ship as executable docs."""
        commands_dir = Path(__file__).parent.parent / "commands"
        incorrect_files = []
        for md_file in commands_dir.glob("*.md"):
            content = md_file.read_text(encoding="utf-8")
            in_fence = False
            lines = content.split("\n")
            for i, line in enumerate(lines, 1):
                if line.strip().startswith("```"):
                    in_fence = not in_fence
                    continue
                if not in_fence and line.lstrip().startswith("! "):
                    incorrect_files.append(f"{md_file.name}:{i}")

        assert len(incorrect_files) == 0, \
            f"Found bare dynamic-context syntax in: {', '.join(incorrect_files)}"

    @pytest.mark.unit
    def test_bang_operator_documentation_exists(self):
        """Test bang operator is documented in plugin help"""
        help_md = Path(__file__).parent.parent / "commands" / "claude-code-plugin-help.md"
        content = help_md.read_text(encoding="utf-8")

        # Check for documentation sections
        assert "## Dynamic Context" in content
        assert "!`command`" in content
        assert "https://code.claude.com/docs/en/skills" in content


# Run with: python3 -m pytest tests/test_bang_syntax.py -v --override-ini='addopts='
