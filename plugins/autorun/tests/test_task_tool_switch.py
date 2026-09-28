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
    assert {"claude", "codex", "gemini", "qwen"} <= requires
    for name in ("claude", "codex", "gemini", "qwen"):
        assert task_enforcement_capability_available(name, None) is False, name
    # Pi and Prime report their tools; OpenCode's primary agent always has
    # todowrite.
    for name in ("pi", "prime", "opencode"):
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


def test_a_subagent_gets_no_evidence_from_the_parents_switch():
    """A subagent inherits the parent's environment, not necessarily its tools.

    A Claude custom agent whose `tools:` list leaves out TaskCreate still sees
    CLAUDE_CODE_ENABLE_TODO_TOOLS=1. Treating that as evidence would demand a
    tool it cannot call, the failure rc3 fixes for Pi children.
    """
    normalized = normalize_hook_payload(
        {
            "hook_event_name": "PostToolUse",
            "session_id": "s",
            "cli_type": "claude",
            "agent_id": "agent-1",
            **CLAUDE_SWITCH_ON,
        }
    )
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


# --- a switch read from the harness's settings file (Qwen Code) ------------


def _qwen_settings(directory: Path, text: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "settings.json").write_text(text, encoding="utf-8")


@pytest.mark.parametrize(
    ("user_settings", "expected"),
    [
        (None, None),
        ('{"tools": {"todoWrite": {"enabled": true}}}', "tools"),
        ('{"tools": {"todoWrite": {"enabled": false}}}', frozenset()),
        ('{"theme": "dark"}', None),
        # Qwen Code reads settings through strip-json-comments, so a commented
        # file says what it says; comment markers inside strings are text.
        ('{\n  // mine\n  "tools": {"todoWrite": {"enabled": true}}\n}', "tools"),
        ('{"tools": {/* off */ "todoWrite": {"enabled": false}}}', frozenset()),
        ('{"url": "http://x/*y*/", "q": "a\\"//", "tools": {"todoWrite": {"enabled": true}}}', "tools"),
        # A file the harness cannot parse either says nothing.
        ('{"tools": {"todoWrite": {"enabled": true}}', None),
    ],
)
def test_qwen_evidence_is_its_own_setting(tmp_path, monkeypatch, user_settings, expected):
    from autorun.core import task_tools_proven_by_settings

    home = tmp_path / "qwen-home"
    monkeypatch.setenv("QWEN_HOME", str(home))
    if user_settings is not None:
        _qwen_settings(home, user_settings)
    proven = task_tools_proven_by_settings("qwen", str(tmp_path / "project"))
    if expected == "tools":
        assert proven == registered_task_tools("qwen")
    else:
        assert proven == expected


def test_qwen_workspace_settings_override_the_user_file(tmp_path, monkeypatch):
    from autorun.core import task_tools_proven_by_settings

    home = tmp_path / "qwen-home"
    monkeypatch.setenv("QWEN_HOME", str(home))
    _qwen_settings(home, '{"tools": {"todoWrite": {"enabled": true}}}')
    project = tmp_path / "project"
    _qwen_settings(project / ".qwen", '{"tools": {"todoWrite": {"enabled": false}}}')

    assert task_tools_proven_by_settings("qwen", str(project)) == frozenset()


def test_reading_the_qwen_switch_keeps_the_installer_off_the_hook_path(tmp_path):
    """Every Qwen hook reads the switch, inside a 4 s budget; the installer's
    walk, parsers and URL handling must not load with it."""
    import json
    import os
    import subprocess

    home = tmp_path / "qwen-home"
    _qwen_settings(home, '{"tools": {"todoWrite": {"enabled": true}}}')
    src = Path(__file__).resolve().parents[1] / "src"
    code = (
        f"import sys, json; sys.path.insert(0, {str(src)!r}); "
        "from autorun.core import task_tools_proven_by_settings; "
        "before = set(sys.modules); "
        "assert task_tools_proven_by_settings('qwen', None); "
        "print(json.dumps(sorted(set(sys.modules) - before)))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={**os.environ, "QWEN_HOME": str(home)},
        cwd=tmp_path,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    loaded = set(json.loads(completed.stdout))
    heavy = {
        "argparse",
        "urllib.request",
        "autorun.installer.fs",
        "autorun.installer.settings",
        "autorun.installer.traversal",
    }
    assert not loaded & heavy, sorted(loaded & heavy)


def test_a_settings_change_is_seen_without_a_restart(tmp_path, monkeypatch):
    """The read is cached by modification time and size, not forever."""
    from autorun.core import task_tools_proven_by_settings

    home = tmp_path / "qwen-home"
    monkeypatch.setenv("QWEN_HOME", str(home))
    _qwen_settings(home, '{"tools": {"todoWrite": {"enabled": false}}}')
    assert task_tools_proven_by_settings("qwen", None) == frozenset()
    _qwen_settings(home, '{"tools": {"todoWrite": {"enabled": true}}}  ')
    assert task_tools_proven_by_settings("qwen", None) == registered_task_tools("qwen")


def test_an_env_switch_harness_never_reads_settings_files(tmp_path, monkeypatch):
    from autorun.core import task_tools_proven_by_settings

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    (tmp_path / "settings.json").write_text(
        '{"env": {"CLAUDE_CODE_ENABLE_TODO_TOOLS": "1"}}', encoding="utf-8"
    )
    # Claude's evidence is the variable the session process has, which the
    # hook forwards; the file may not be what that process loaded.
    assert task_tools_proven_by_settings("claude", None) is None


def test_the_codex_switch_is_installed_but_never_evidence(tmp_path, monkeypatch):
    """With update_plan enabled, Codex Plan mode still rejects it; hooks cannot tell."""
    from autorun.core import task_tools_proven_by_settings

    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text("[tools.update_plan]\nenabled = true\n", encoding="utf-8")
    assert PLATFORMS["codex"].task_tool_switch.proves_tools is False
    assert task_tools_proven_by_settings("codex", None) is None
    assert task_enforcement_capability_available("codex", None) is False


# --- evidence from a task call the session made -----------------------------


def _post_tool(session_id: str, cli_type: str, tool_name: str, store, agent_id=None,
               transcript_path=None):
    return EventContext(
        session_id=session_id,
        event="PostToolUse",
        prompt="",
        tool_name=tool_name,
        tool_input={"subject": "Observed", "description": ""},
        tool_result="Task #1 created successfully",
        store=store,
        cli_type=cli_type,
        agent_id=agent_id,
        transcript_path=transcript_path,
    )


def test_a_task_call_proves_the_tools_for_the_rest_of_the_session():
    """A Claude session on an older model has TaskCreate without any switch.

    Its first successful task call is the evidence: from then on task gates
    may enforce in that session.
    """
    from autorun.plugins import app

    store = ThreadSafeDB()
    before = _post_tool("observed-claude", "claude", "Read", store)
    assert before.task_tool_evidence is None, "unknown until a task call"

    app.dispatch(
        _post_tool("observed-claude", "claude", "TaskCreate", store)
    )
    after = _post_tool("observed-claude", "claude", "Bash", store)
    assert after.task_tool_evidence == registered_task_tools("claude")
    assert task_enforcement_capability_available("claude", after.task_tool_evidence)


def test_a_subagents_task_call_proves_nothing_for_the_parent_or_itself():
    from autorun.plugins import app

    store = ThreadSafeDB()
    app.dispatch(
        _post_tool("observed-child", "claude", "TaskCreate", store, agent_id="agent-1")
    )
    assert _post_tool("observed-child", "claude", "Bash", store).task_tool_evidence is None
    child = _post_tool("observed-child", "claude", "Bash", store, agent_id="agent-1")
    assert child.task_tool_evidence is None


def test_codex_never_learns_from_a_call_plan_mode_could_later_refuse():
    from autorun.plugins import app

    store = ThreadSafeDB()
    assert PLATFORMS["codex"].task_evidence_from_task_calls is False
    app.dispatch(
        _post_tool("observed-codex", "codex", "update_plan", store)
    )
    assert _post_tool("observed-codex", "codex", "Bash", store).task_tool_evidence is None


def test_a_model_switch_retires_the_proof_until_the_new_model_calls_a_task_tool(tmp_path):
    """Evidence from a task call belongs to the model that made it.

    A session proved its tools on one model, then `/model` moved it to one
    Claude Code offers no task tools; the old proof kept Stop demanding task
    updates the agent could not make.
    """
    import json

    from autorun.plugins import app

    transcript = tmp_path / "session.jsonl"

    def answered_by(model: str) -> None:
        with transcript.open("a", encoding="utf-8") as out:
            out.write(json.dumps({"type": "assistant", "message": {"model": model}}) + "\n")
            out.write(json.dumps({"type": "user", "message": {"content": "ok"}}) + "\n")

    def event(tool: str):
        return _post_tool("switched", "claude", tool, store, transcript_path=str(transcript))

    store = ThreadSafeDB()
    answered_by("claude-opus-5-5")
    app.dispatch(event("TaskCreate"))
    assert event("Bash").task_tool_evidence == registered_task_tools("claude")

    answered_by("<synthetic>")  # the harness's own lines name no model
    assert event("Bash").task_tool_evidence == registered_task_tools("claude")

    with transcript.open("a", encoding="utf-8") as out:  # a subagent's turn
        out.write(json.dumps({"type": "assistant", "isSidechain": True,
                              "message": {"model": "claude-haiku-4-5"}}) + "\n")
    assert event("Bash").task_tool_evidence == registered_task_tools("claude")

    answered_by("claude-older-model")
    assert event("Bash").task_tool_evidence is None, "unknown again after the switch"

    app.dispatch(event("TaskUpdate"))
    assert event("Bash").task_tool_evidence == registered_task_tools("claude")
