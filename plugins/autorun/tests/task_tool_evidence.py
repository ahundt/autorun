"""Task-tool evidence for tests that exercise gates on a session that has the tools.

Claude Code, Codex, and Gemini CLI can each start a session without their task
tools, and their hooks cannot tell. autorun's task-only gates therefore enforce
there only with evidence (platforms.Platform.task_enforcement_requires_tool_evidence):
a reported tool list, or the harness's own switch forwarded by the hook process.
A test about gate behavior, rather than capability, states that evidence here
instead of relying on the old assumption that every Claude session has
TaskCreate. Tests about capability itself pass ``active_tools`` explicitly.
"""

from __future__ import annotations

from autorun.platforms import platform_for, registered_task_tools


def task_tool_evidence(cli_type: str | None = None) -> frozenset[str] | None:
    """The registered task tools for a harness that needs evidence, else None.

    None keeps the legacy "unknown" path for harnesses that do not need
    evidence, which is what those tests have always exercised.
    """
    if not platform_for(cli_type).task_enforcement_requires_tool_evidence:
        return None
    return registered_task_tools(cli_type)


#: A Claude hook payload carrying the switch the installer sets, as
#: hooks/hook_entry.py forwards it.
CLAUDE_SWITCH_ON = {"autorun_harness_env": {"CLAUDE_CODE_ENABLE_TODO_TOOLS": "1"}}
