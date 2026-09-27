"""Task-tool switches: harnesses whose task tools are an opt-in.

Claude Code 2.1.268+ offers TaskCreate/TaskUpdate/TaskList/TaskGet only to
older models unless CLAUDE_CODE_ENABLE_TODO_TOOLS=1; Qwen Code 0.24.0+ registers
todo_write only with tools.todoWrite.enabled; Gemini CLI 0.36.0+ offers
write_todos only on an explicit Gemini 2.x model. No harness hook reports the
session's tools. A task gate that demands a tool the session was never given
denies every later call, so these harnesses enforce only with evidence, and the
switch the hook process can see is that evidence (platforms.TaskToolSwitch).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from autorun.core import EventContext, ThreadSafeDB, normalize_hook_payload
from autorun.platforms import (
    PLATFORMS,
    TASK_CREATE_CAPABILITY_ROLES,
    registered_task_tools,
    task_enforcement_capability_available,
    task_tool_role,
    task_tool_switch_env_names,
    task_tools_proven_by_switch,
)
from task_tool_evidence import CLAUDE_SWITCH_ON

HOOK_ENTRY = Path(__file__).resolve().parents[1] / "hooks" / "hook_entry.py"


def _hook_entry():
    spec = importlib.util.spec_from_file_location("hook_entry_under_test", HOOK_ENTRY)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_hook_forwards_exactly_the_variables_the_registry_declares():
    """hook_entry.py imports no autorun module, so its list is a second copy.

    This pins the copy: a switch added to the registry and not to the hook
    would never reach the daemon, and the gates would stand down forever on
    that harness with nothing reporting why.
    """
    assert set(_hook_entry().TASK_TOOL_SWITCH_ENV) == task_tool_switch_env_names()


def test_the_hook_forwards_only_switches_that_are_set(monkeypatch):
    hook_entry = _hook_entry()
    for name in task_tool_switch_env_names():
        monkeypatch.delenv(name, raising=False)
    assert "autorun_harness_env" not in hook_entry.prepare_hook_payload({}, "claude")

    monkeypatch.setenv("CLAUDE_CODE_ENABLE_TODO_TOOLS", "1")
    prepared = hook_entry.prepare_hook_payload({"session_id": "s"}, "claude")
    assert prepared["autorun_harness_env"] == {"CLAUDE_CODE_ENABLE_TODO_TOOLS": "1"}


@pytest.mark.parametrize(
    ("harness_env", "proven"),
    [
        (None, False),
        ({}, False),
        ({"CLAUDE_CODE_ENABLE_TODO_TOOLS": "1"}, True),
        ({"CLAUDE_CODE_ENABLE_TODO_TOOLS": " TRUE "}, True),
        ({"CLAUDE_CODE_ENABLE_TODO_TOOLS": "0"}, False),
        ({"CLAUDE_CODE_ENABLE_TODO_TOOLS": "maybe"}, False),
        # =0 swaps the Task tools for TodoWrite, which the registry does not
        # list, so the switch no longer proves the tools the gates name.
        ({"CLAUDE_CODE_ENABLE_TODO_TOOLS": "1", "CLAUDE_CODE_ENABLE_TASKS": "0"}, False),
        ({"CLAUDE_CODE_ENABLE_TODO_TOOLS": "1", "CLAUDE_CODE_ENABLE_TASKS": "1"}, True),
    ],
)
def test_claude_switch_values(harness_env, proven):
    tools = task_tools_proven_by_switch("claude", harness_env)
    assert (tools == registered_task_tools("claude")) if proven else tools is None


def test_a_harness_without_an_env_switch_proves_nothing():
    everything_on = {name: "1" for name in task_tool_switch_env_names()}
    for name in ("codex", "gemini", "qwen", "opencode", "pi", "antigravity"):
        assert task_tools_proven_by_switch(name, everything_on) is None, name


def test_harnesses_whose_tools_are_optional_need_evidence():
    requires = {
        name for name, platform in PLATFORMS.items()
        if platform.task_enforcement_requires_tool_evidence
    }
    assert {"claude", "codex", "gemini"} <= requires
    for name in ("claude", "codex", "gemini"):
        assert task_enforcement_capability_available(name, None) is False, name
    # Pi and Prime report their tools; OpenCode's primary agent always has
    # todowrite; Qwen's switch is set by the installer.
    for name in ("pi", "prime", "opencode", "qwen"):
        assert task_enforcement_capability_available(name, None) is True, name


def test_qwen_counts_its_own_checklist_tool():
    assert "todo_write" in registered_task_tools("qwen")
    assert task_tool_role("qwen", "todo_write") in TASK_CREATE_CAPABILITY_ROLES


@pytest.mark.parametrize(
    ("payload_extra", "known"),
    [({}, False), (CLAUDE_SWITCH_ON, True)],
)
def test_normalize_turns_a_forwarded_switch_into_evidence(payload_extra, known):
    normalized = normalize_hook_payload(
        {"hook_event_name": "Stop", "session_id": "s", "cli_type": "claude", **payload_extra}
    )
    if known:
        assert normalized["active_tools"] == registered_task_tools("claude")
    else:
        assert normalized["active_tools"] is None


def test_a_reported_tool_list_outranks_a_switch():
    """Pi reports what the session really has; a stray variable cannot widen it."""
    normalized = normalize_hook_payload(
        {
            "hook_event_name": "Stop",
            "session_id": "s",
            "cli_type": "claude",
            "active_tools": ["Read", "Bash"],
            **CLAUDE_SWITCH_ON,
        }
    )
    assert normalized["active_tools"] == frozenset({"Read", "Bash"})


@pytest.mark.parametrize("known", [False, True])
def test_a_claude_plan_command_arms_the_task_nag_only_with_evidence(known):
    from autorun.plugins import app

    normalized = normalize_hook_payload(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": f"claude-plan-evidence-{known}",
            "cli_type": "claude",
            **(CLAUDE_SWITCH_ON if known else {}),
        }
    )
    ctx = EventContext(
        session_id=normalized["session_id"],
        event="UserPromptSubmit",
        prompt="/ar:pn notes/plan.md",
        store=ThreadSafeDB(),
        cli_type="claude",
        active_tools=normalized["active_tools"],
    )
    app.dispatch(ctx)
    assert ctx.plan_awaiting_planning_tasks is known
