"""A host that wraps Claude Code and supplies its own task list.

The Claude desktop app's Code tab runs Claude Code with
``CLAUDE_CODE_ENTRYPOINT=claude-desktop`` and attaches an MCP server whose
``mcp__ccd_turn__update_plan`` is the task list the app shows the user. The
app's system prompt tells the model to use that tool *in place of* TaskCreate,
TaskUpdate, TaskList and TaskGet and not to search for them. autorun's
SessionStart told the same session the opposite ("use ToolSearch once with
`select:TaskCreate,...`"), and on 2026-11-16 one desktop session followed
autorun and kept two task lists at once.

Evidence the hook can see, captured from a desktop Code-tab session's process
environment on 2026-11-16 (desktop app 5.11492.5, Claude Code 2.1.325):
``CLAUDE_CODE_ENTRYPOINT=claude-desktop`` and
``CLAUDE_CODE_TERMINAL_MCP_TOOLS=mcp__ccd_turn__wrap_up``. The same process
did not carry ``CLAUDE_CODE_ENABLE_TODO_TOOLS``, so the task-tool switch is no
evidence there.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from autorun.core import EventContext, ThreadSafeDB, normalize_hook_payload
from autorun.platforms import (
    PLATFORMS,
    forwarded_harness_env_names,
    host_task_list_env_names,
    host_task_list_tool,
)

HOOK_ENTRY = Path(__file__).resolve().parents[1] / "hooks" / "hook_entry.py"
DESKTOP_TOOL = "mcp__ccd_turn__update_plan"
DESKTOP_ENV = {
    "CLAUDE_CODE_ENTRYPOINT": "claude-desktop",
    "CLAUDE_CODE_TERMINAL_MCP_TOOLS": "mcp__ccd_turn__wrap_up",
}


def _hook_entry():
    spec = importlib.util.spec_from_file_location("hook_entry_host_task_list", HOOK_ENTRY)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# --- forwarding: hook process -> daemon, on both hook paths ------------------
#
# hooks/hook_entry.py is the path the installer writes into Claude's plugin
# cache. The desktop app loads the plugin from the marketplace source instead,
# whose hooks/hooks.json runs the console command `autorun --cli claude`; that
# path reaches the daemon through client.prepare_payload_for_daemon.


def _desktop_env(monkeypatch) -> None:
    for name in forwarded_harness_env_names():
        monkeypatch.delenv(name, raising=False)
    for name, value in DESKTOP_ENV.items():
        monkeypatch.setenv(name, value)


def test_host_variables_are_forwarded_by_the_hook_entry_path(monkeypatch):
    assert host_task_list_env_names() <= forwarded_harness_env_names()
    _desktop_env(monkeypatch)
    prepared = _hook_entry().prepare_hook_payload({"session_id": "s"}, "claude")
    assert prepared["autorun_harness_env"] == DESKTOP_ENV


def test_host_variables_are_forwarded_by_the_console_command_path(monkeypatch):
    from autorun.client import prepare_payload_for_daemon

    _desktop_env(monkeypatch)
    payload, _ = prepare_payload_for_daemon(
        {"hook_event_name": "SessionStart", "session_id": "s", "cli_type": "claude"}
    )
    assert payload["autorun_harness_env"] == DESKTOP_ENV
    assert normalize_hook_payload(payload)["host_task_list_tool"] == DESKTOP_TOOL


def test_the_console_path_keeps_env_hook_entry_already_attached(monkeypatch):
    """hook_entry.py may hand its prepared payload to the console command."""
    from autorun.client import prepare_payload_for_daemon

    _desktop_env(monkeypatch)
    attached = {"CLAUDE_CODE_ENTRYPOINT": "cli"}
    payload, _ = prepare_payload_for_daemon(
        {"hook_event_name": "SessionStart", "cli_type": "claude", "autorun_harness_env": attached}
    )
    assert payload["autorun_harness_env"] == attached


def test_the_console_path_adds_nothing_outside_a_harness(monkeypatch):
    from autorun.client import prepare_payload_for_daemon

    for name in forwarded_harness_env_names():
        monkeypatch.delenv(name, raising=False)
    payload, _ = prepare_payload_for_daemon({"hook_event_name": "SessionStart", "cli_type": "claude"})
    assert "autorun_harness_env" not in payload


# --- the registry decides which host supplies a task list ---------------------


@pytest.mark.parametrize(
    ("cli_type", "harness_env", "expected"),
    [
        ("claude", DESKTOP_ENV, DESKTOP_TOOL),
        # Several terminal tools, in any separator the app might use.
        ("claude", {**DESKTOP_ENV, "CLAUDE_CODE_TERMINAL_MCP_TOOLS": "x__y, mcp__ccd_turn__wrap_up"}, DESKTOP_TOOL),
        # The desktop entrypoint alone does not prove the plan tool is attached.
        ("claude", {"CLAUDE_CODE_ENTRYPOINT": "claude-desktop"}, None),
        ("claude", {**DESKTOP_ENV, "CLAUDE_CODE_TERMINAL_MCP_TOOLS": "mcp__other__wrap_up"}, None),
        # The terminal CLI and SDK entrypoints have no host task list.
        ("claude", {**DESKTOP_ENV, "CLAUDE_CODE_ENTRYPOINT": "cli"}, None),
        ("claude", {}, None),
        ("claude", None, None),
        # Another harness never inherits Claude's host.
        ("codex", DESKTOP_ENV, None),
        ("gemini", DESKTOP_ENV, None),
    ],
)
def test_host_task_list_tool(cli_type, harness_env, expected):
    assert host_task_list_tool(cli_type, harness_env) == expected


@pytest.mark.parametrize("cli_type", sorted(set(PLATFORMS) - {"claude"}))
def test_other_harnesses_ignore_inherited_desktop_variables(cli_type, monkeypatch):
    """A Codex, Gemini, Qwen, Pi... session started from a desktop session's
    Bash tool inherits both desktop variables, and its hook forwards them. The
    host belongs to Claude's registry entry, so for every other harness the
    variables must change neither the host answer nor the task evidence."""
    from autorun.client import prepare_payload_for_daemon

    def evidence(env: dict) -> tuple:
        for name in forwarded_harness_env_names():
            monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        payload, detected = prepare_payload_for_daemon(
            {"hook_event_name": "SessionStart", "session_id": f"other-{cli_type}", "cli_type": cli_type}
        )
        assert detected == cli_type
        normalized = normalize_hook_payload(payload)
        ctx = EventContext(
            session_id=payload["session_id"], event="SessionStart", store=ThreadSafeDB(),
            cli_type=cli_type, active_tools=normalized["active_tools"],
            host_task_list_tool=normalized["host_task_list_tool"],
        )
        return normalized["host_task_list_tool"], normalized["active_tools"], ctx.task_tool_evidence

    plain = evidence({})
    inherited = evidence(DESKTOP_ENV)
    assert inherited[0] is None
    assert inherited == plain


def test_normalized_payload_carries_the_host_task_list():
    normalized = normalize_hook_payload(
        {
            "hook_event_name": "SessionStart",
            "session_id": "desktop-normalize",
            "cli_type": "claude",
            "autorun_harness_env": DESKTOP_ENV,
        }
    )
    assert normalized["host_task_list_tool"] == DESKTOP_TOOL

    plain = normalize_hook_payload(
        {"hook_event_name": "SessionStart", "session_id": "cli-normalize", "cli_type": "claude"}
    )
    assert plain["host_task_list_tool"] is None


def test_event_context_exposes_the_host_task_list():
    ctx = EventContext(
        session_id="s", event="SessionStart", store=ThreadSafeDB(),
        cli_type="claude", host_task_list_tool=DESKTOP_TOOL,
    )
    assert ctx.host_task_list_tool == DESKTOP_TOOL
    assert EventContext(session_id="s", event="SessionStart").host_task_list_tool is None


# --- SessionStart names the host's list instead of Claude's Task tools --------


@pytest.fixture
def isolated_session(tmp_path):
    from autorun import session_manager as _sm_module
    from autorun.session_manager import SessionStateManager, _reset_for_testing

    temp_dir = tmp_path / "sessions"
    temp_dir.mkdir(parents=True, exist_ok=True)
    _reset_for_testing()
    mgr = SessionStateManager(state_dir=temp_dir)
    _sm_module._manager = mgr
    _sm_module._store = mgr._store
    yield mgr
    _reset_for_testing()


def _session_start(session_id: str, host_task_list_tool: str | None) -> str:
    from autorun import plugins

    ctx = EventContext(
        session_id=session_id,
        event="SessionStart",
        store=ThreadSafeDB(),
        cli_type="claude",
        source="startup",
        host_task_list_tool=host_task_list_tool,
    )
    ctx.autorun_active = False
    ctx.autorun_stage = EventContext.STAGE_INACTIVE
    return str(plugins.app.dispatch(ctx))


def test_desktop_session_start_points_at_the_host_list_not_task_tools(isolated_session):
    text = _session_start("desktop-start", DESKTOP_TOOL)

    assert DESKTOP_TOOL in text
    assert "select:Task" not in text
    assert "ToolSearch" not in text
    # The settings.json fix is for sessions that need Claude's Task tools; a
    # desktop session is told not to use them, so it must not be sent there.
    assert "CLAUDE_CODE_ENABLE_TODO_TOOLS" not in text
    assert "not enforcing task tracking yet" not in text


def test_task_gates_stand_down_under_a_host_task_list(isolated_session):
    """SessionStart says autorun's tracking is off there; the gates must agree.

    Every task gate (staleness, Stop, SessionStart resume) asks
    ``task_tool_evidence``. Under a host list it answers known-empty, even when
    the switch was forwarded or the model called TaskCreate anyway, so no gate
    demands the Task tools the host told the model not to use.
    """
    from autorun.platforms import (
        TASK_UPDATE_CAPABILITY_ROLES,
        registered_task_tools,
        task_enforcement_capability_available,
    )

    store = ThreadSafeDB()
    switch_proven = registered_task_tools("claude")
    desktop = EventContext(
        session_id="desktop-gates", event="Stop", store=store, cli_type="claude",
        active_tools=switch_proven, host_task_list_tool=DESKTOP_TOOL,
    )
    desktop.state_set(EventContext.TASK_TOOLS_OBSERVED, True)
    assert desktop.task_tool_evidence == frozenset()
    assert not task_enforcement_capability_available(
        "claude", desktop.task_tool_evidence, TASK_UPDATE_CAPABILITY_ROLES
    )

    terminal = EventContext(
        session_id="terminal-gates", event="Stop", store=store, cli_type="claude",
        active_tools=switch_proven,
    )
    assert terminal.task_tool_evidence == switch_proven


def test_desktop_stop_does_not_block_on_task_tool_tasks(isolated_session):
    from autorun import plugins
    from autorun.platforms import registered_task_tools
    from autorun.task_lifecycle import TaskLifecycle

    def stop(host_tool):
        ctx = EventContext(
            session_id="desktop-stop" if host_tool else "terminal-stop",
            event="Stop", store=ThreadSafeDB(), cli_type="claude",
            active_tools=registered_task_tools("claude"),
            host_task_list_tool=host_tool,
        )
        ctx.autorun_active = False
        ctx.autorun_stage = EventContext.STAGE_INACTIVE
        manager = TaskLifecycle(ctx=ctx)
        manager.create_task("1", {"subject": "Unfinished"}, "created")
        manager.update_task("1", {"status": "in_progress"}, "started")
        return plugins.app.dispatch(ctx)

    assert "Unfinished" in str(stop(None)), "control: a terminal session blocks"
    result = stop(DESKTOP_TOOL)
    assert result is None or "Unfinished" not in str(result)


def test_terminal_session_start_is_unchanged(isolated_session):
    text = _session_start("terminal-start", None)

    assert "ToolSearch" in text
    assert "select:Task" in text
    assert DESKTOP_TOOL not in text
