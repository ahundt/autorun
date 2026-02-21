#!/usr/bin/env python3
"""Verify Gemini CLI tool name coverage in config.py sets."""
import sys
from pathlib import Path

# Add src to path
plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root / 'src'))

from clautorun.config import (
    BASH_TOOLS, WRITE_TOOLS, EDIT_TOOLS, PLAN_TOOLS,
    TASK_CREATE_TOOLS, TASK_UPDATE_TOOLS, TASK_LIST_TOOLS, TASK_GET_TOOLS
)


class TestGeminiToolNameCoverage:
    """Verify all Gemini CLI tool names are recognized."""

    def test_gemini_bash_tools(self):
        """Gemini bash tool names must be in BASH_TOOLS."""
        assert "bash_command" in BASH_TOOLS, "Gemini's bash_command missing"
        assert "run_shell_command" in BASH_TOOLS, "Gemini's run_shell_command missing"
        # Claude Code compatibility
        assert "Bash" in BASH_TOOLS, "Claude Code's Bash missing"

    def test_gemini_write_tools(self):
        """Gemini write tool names must be in WRITE_TOOLS."""
        assert "write_file" in WRITE_TOOLS, "Gemini's write_file missing"
        assert "Write" in WRITE_TOOLS, "Claude Code's Write missing"

    def test_gemini_edit_tools(self):
        """Gemini edit tool names must be in EDIT_TOOLS."""
        assert "edit_file" in EDIT_TOOLS, "Gemini's edit_file missing"
        assert "replace" in EDIT_TOOLS, "Gemini's replace missing"
        assert "Edit" in EDIT_TOOLS, "Claude Code's Edit missing"

    def test_gemini_plan_tools(self):
        """Gemini plan tool names must be in PLAN_TOOLS."""
        assert "exit_plan_mode" in PLAN_TOOLS, "Gemini's exit_plan_mode missing"
        assert "ExitPlanMode" in PLAN_TOOLS, "Claude Code's ExitPlanMode missing"

    def test_gemini_task_tools(self):
        """Gemini task tool names must be in task tool sets."""
        # Task creation
        assert "task_create" in TASK_CREATE_TOOLS, "Gemini's task_create missing"
        assert "TaskCreate" in TASK_CREATE_TOOLS, "Claude Code's TaskCreate missing"

        # Task update
        assert "task_update" in TASK_UPDATE_TOOLS
        assert "TaskUpdate" in TASK_UPDATE_TOOLS

        # Task list
        assert "task_list" in TASK_LIST_TOOLS
        assert "TaskList" in TASK_LIST_TOOLS

        # Task get
        assert "task_get" in TASK_GET_TOOLS
        assert "TaskGet" in TASK_GET_TOOLS

    def test_no_cross_contamination(self):
        """Tool sets should not overlap (prevent incorrect matching)."""
        # BASH_TOOLS should not contain file tools
        assert not (BASH_TOOLS & WRITE_TOOLS), "BASH_TOOLS overlaps WRITE_TOOLS"
        assert not (BASH_TOOLS & EDIT_TOOLS), "BASH_TOOLS overlaps EDIT_TOOLS"

        # File tools shouldn't overlap with plan tools
        assert not (WRITE_TOOLS & PLAN_TOOLS), "WRITE_TOOLS overlaps PLAN_TOOLS"


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])
