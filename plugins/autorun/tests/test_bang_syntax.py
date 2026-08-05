#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test Claude's documented dynamic-context syntax in command files."""
import pytest
from pathlib import Path


class TestBangOperatorSyntax:
    """Test ! operator syntax in command markdown files"""

    @pytest.mark.unit
    def test_tabs_skill_still_gets_the_session_list_first(self):
        """The session list has to reach the agent before it picks a window.

        It used to arrive through Claude's `` !`cmd` `` block, which renders
        nowhere else, so the skill now instructs the agent to run the command.
        The requirement is unchanged: no acting on windows sight-unseen.
        """
        skill = Path(__file__).parent.parent / "skills" / "tabs" / "SKILL.md"
        content = skill.read_text(encoding="utf-8")

        assert "tmux list-sessions" in content
        assert "!`" not in content, "a Claude-only block came back into a skill body"

    @pytest.mark.unit
    def test_tabw_skill_still_gets_the_session_list_first(self):
        """Same rule for the writing command, which is the dangerous one."""
        skill = Path(__file__).parent.parent / "skills" / "tabw" / "SKILL.md"
        content = skill.read_text(encoding="utf-8")

        assert "tmux list-sessions" in content
        assert "!`" not in content

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
