#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test Claude's documented dynamic-context syntax in command files."""
import pytest
from pathlib import Path


class TestBangOperatorSyntax:
    """Test ! operator syntax in command markdown files"""

    @pytest.mark.unit
    def test_tmux_skill_lists_the_sessions_before_it_sends(self):
        """The session list has to reach the agent before it picks a window.

        It used to arrive through Claude's `` !`cmd` `` block, which renders
        nowhere else, so the skill instructs the agent to run the command.
        The requirement is unchanged: no typing into windows sight-unseen.
        The skill folds the former listing (`tabs`) and writing (`tabw`)
        skills, so the list must come before the first send.
        """
        skill = Path(__file__).parent.parent / "skills" / "tmux" / "SKILL.md"
        content = skill.read_text(encoding="utf-8")

        assert "autorun tmux list" in content
        assert content.index("autorun tmux list") < content.index("autorun tmux send --to")
        assert "!`" not in content, "a Claude-only block came back into a skill body"
        # One stated fallback for Claude Code marketplace installs; any other
        # use of the variable is a command that fails on every other harness.
        assert content.count("${CLAUDE_PLUGIN_ROOT}") == 1

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


# Run with: python3 -m pytest tests/test_bang_syntax.py -v --override-ini='addopts='
