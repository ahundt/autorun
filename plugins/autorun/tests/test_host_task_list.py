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


def _hook_ctx(payload: dict, store) -> EventContext:
    """Build the context the daemon builds from a hook payload (core.py
    AutorunDaemon.handle_client): normalize, then pass the normalized fields on."""
    normalized = normalize_hook_payload(payload)
    return EventContext(
        session_id=normalized["session_id"],
        event=normalized["hook_event_name"],
        prompt=normalized["prompt"],
        tool_name=normalized["tool_name"],
        tool_input=normalized["tool_input"],
        tool_input_envelopes=normalized["tool_input_envelopes"],
        tool_result=normalized["tool_result"],
        store=store,
        cli_type=normalized["cli_type"],
        source=normalized["source"],
        agent_id=normalized["agent_id"],
        agent_type=normalized["agent_type"],
        active_tools=normalized["active_tools"],
        host_task_list_tool=normalized["host_task_list_tool"],
    )


def _dispatch(event: str, session_id: str, store, env=None, **fields):
    """Dispatch one hook event through the real chain; return (response, ctx)."""
    from autorun import plugins

    payload = {"hook_event_name": event, "session_id": session_id, "cli_type": "claude", **fields}
    if env is not None:
        payload["autorun_harness_env"] = env
    ctx = _hook_ctx(payload, store)
    if event != "SessionStart":
        ctx.autorun_active = False
        ctx.autorun_stage = EventContext.STAGE_INACTIVE
    return plugins.app.dispatch(ctx), ctx


def _start_text(session_id: str, store, env) -> str:
    response, _ = _dispatch("SessionStart", session_id, store, env, source="startup")
    return str(response)


SWITCH = {"CLAUDE_CODE_ENABLE_TODO_TOOLS": "1"}


def test_desktop_session_start_without_proof_is_conditional(isolated_session):
    """(a) The environment alone does not prove the plan tool exists."""
    text = _start_text("desktop-start", ThreadSafeDB(), DESKTOP_ENV)

    assert f"If this session has `{DESKTOP_TOOL}`" in text
    assert "do not load or call TaskCreate, TaskGet, TaskList or TaskUpdate" in text
    assert "Otherwise, before tracked work, use ToolSearch once with `select:TaskCreate" in text
    # It must not assert the tool exists, and must not say autorun's tracking is off.
    assert "keeps its task list in" not in text
    assert "stays off" not in text


def test_desktop_session_start_after_proof_names_the_host_list(isolated_session):
    """(b) Once the session has called the plan tool, the instruction is unconditional."""
    store = ThreadSafeDB()
    _dispatch("PostToolUse", "desktop-start-proof", store, DESKTOP_ENV, tool_name=DESKTOP_TOOL,
              tool_input={"steps": []}, tool_response="Plan shown (3 steps).")
    text = _start_text("desktop-start-proof", store, DESKTOP_ENV)

    assert DESKTOP_TOOL in text
    assert "If this session has" not in text
    assert "ToolSearch" not in text
    assert "select:Task" not in text
    assert "CLAUDE_CODE_ENABLE_TODO_TOOLS" not in text
    assert "not enforcing task tracking yet" not in text


@pytest.fixture
def desktop_after_task_stop(isolated_session, request):
    store = ThreadSafeDB()
    sid = request.node.name
    env = {**DESKTOP_ENV, **SWITCH}
    assert f"If this session has `{DESKTOP_TOOL}`" in _start_text(sid, store, env)
    _dispatch("PostToolUse", sid, store, env, tool_name="TaskCreate",
              tool_input={"subject": "Original task"},
              tool_response="Task #1 created successfully: Original task")
    block, _ = _dispatch("Stop", sid, store, env)
    assert all(text in str(block) for text in ("CANNOT STOP", "Original task", "TaskUpdate"))
    return sid, store, env


def _call_desktop_plan(sid, store, env):
    return _dispatch("PostToolUse", sid, store, env, tool_name=DESKTOP_TOOL,
                     tool_input={"steps": []}, tool_response="Plan shown")


def test_desktop_plan_proof_drops_pending_task_stop_replay(desktop_after_task_stop):
    sid, store, env = desktop_after_task_stop
    proof, ctx = _call_desktop_plan(sid, store, env)
    assert ctx.host_task_list_in_use
    assert "CANNOT STOP" not in str(proof)
    assert ctx.pending_stop_injection is None
    stop, _ = _dispatch("Stop", sid, store, env)
    assert "CANNOT STOP" not in str(stop)


def test_desktop_plan_proof_delivers_definitive_guidance_once(desktop_after_task_stop):
    sid, store, env = desktop_after_task_stop
    guidance = f"This session's host keeps its task list in `{DESKTOP_TOOL}`"
    proof, _ = _call_desktop_plan(sid, store, env)
    assert guidance in (proof or {}).get("hookSpecificOutput", {}).get("additionalContext", "")
    assert "If this session has" not in str(proof)
    repeat, _ = _call_desktop_plan(sid, store, env)
    assert guidance not in str(repeat)
    # A later fresh context has not seen it: one more delivery, then none.
    assert guidance in _start_text(sid, store, env)
    assert guidance not in _start_text(sid, store, env)


def test_task_evidence_follows_the_proof_not_the_environment(isolated_session):
    """(a)+(b) Same switch variables: desktop without proof equals a terminal;
    after the plan tool was called the evidence is the empty set."""
    from autorun.platforms import registered_task_tools

    store = ThreadSafeDB()
    env = {**DESKTOP_ENV, **SWITCH}
    terminal_env = {"CLAUDE_CODE_ENTRYPOINT": "cli", **SWITCH}

    def evidence(session_id, harness_env):
        _, ctx = _dispatch("Stop", session_id, store, harness_env)
        return ctx.task_tool_evidence

    assert evidence("evi-desktop", env) == evidence("evi-terminal", terminal_env)
    assert evidence("evi-desktop", env) == registered_task_tools("claude")

    _dispatch("PostToolUse", "evi-desktop", store, env, tool_name=DESKTOP_TOOL, tool_input={})
    assert evidence("evi-desktop", env) == frozenset()
    # Proof belongs to the session that made the call.
    assert evidence("evi-desktop-other", env) == registered_task_tools("claude")


def test_proof_survives_a_new_store_over_the_same_session_state(isolated_session):
    """(b) The record lives in session state like the task-call proof, so a
    later hook process or a restarted daemon (a fresh ThreadSafeDB) sees it."""
    _dispatch("PostToolUse", "persist", ThreadSafeDB(), DESKTOP_ENV, tool_name=DESKTOP_TOOL, tool_input={})
    _, ctx = _dispatch("Stop", "persist", ThreadSafeDB(), DESKTOP_ENV)
    assert ctx.task_tool_evidence == frozenset()


def test_a_subagent_call_does_not_prove_the_tool_for_the_session(isolated_session):
    """(b) Same scoping as the task-call proof: only the main agent's call counts."""
    from autorun.platforms import registered_task_tools

    store = ThreadSafeDB()
    env = {**DESKTOP_ENV, **SWITCH}
    _dispatch("PostToolUse", "sub", store, env, tool_name=DESKTOP_TOOL, tool_input={},
              agent_id="agent-1", agent_type="general-purpose")
    _, ctx = _dispatch("Stop", "sub", store, env)
    assert ctx.task_tool_evidence == registered_task_tools("claude")


def test_the_proof_is_recorded_before_any_handler_that_can_answer(isolated_session):
    """The PostToolUse chain stops at the first handler that answers, so the
    recorder must precede the handlers that can (stop-block delivery,
    staleness and creation reminders)."""
    from autorun import plugins

    names = [h.__name__ for h in plugins.app.chains["PostToolUse"]]
    recorder = names.index("record_host_task_list_use")
    for later in ("deliver_pending_stop_injection", "check_task_staleness",
                  "remind_until_tasks_created", "track_task_operations"):
        assert recorder < names.index(later)


@pytest.mark.parametrize(
    "env",
    [
        {"CLAUDE_CODE_ENTRYPOINT": "cli", "CLAUDE_CODE_TERMINAL_MCP_TOOLS": "mcp__ccd_turn__wrap_up"},
        {"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"},
        {},
    ],
)
def test_the_tool_name_proves_nothing_outside_the_desktop_app(isolated_session, env):
    """(c) An MCP tool that happens to share the name changes nothing off desktop."""
    from autorun.platforms import registered_task_tools

    store = ThreadSafeDB()
    env = {**env, **SWITCH}
    # Own session per case: SessionStart's once-per-session claim outlives a store.
    sid = f"nodesk-{env.get('CLAUDE_CODE_ENTRYPOINT', 'unset')}-{len(env)}"
    _dispatch("PostToolUse", sid, store, env, tool_name=DESKTOP_TOOL, tool_input={})
    _, ctx = _dispatch("Stop", sid, store, env)
    assert ctx.task_tool_evidence == registered_task_tools("claude")
    assert ctx.state_get(EventContext.HOST_TASK_LIST_OBSERVED, False) is False
    assert "ToolSearch" in _start_text(sid, store, env)


def test_other_tools_in_a_desktop_session_record_nothing(isolated_session):
    store = ThreadSafeDB()
    _dispatch("PostToolUse", "other-tool", store, DESKTOP_ENV, tool_name="mcp__ccd_turn__wrap_up", tool_input={})
    _, ctx = _dispatch("Stop", "other-tool", store, DESKTOP_ENV)
    assert ctx.state_get(EventContext.HOST_TASK_LIST_OBSERVED, False) is False


def test_desktop_stop_blocks_without_proof_and_not_with_it(isolated_session):
    """(d) Without proof a desktop Stop blocks on incomplete tasks exactly as a
    terminal Stop does; once the session has used the plan tool it does not."""
    from autorun import plugins
    from autorun.task_lifecycle import TaskLifecycle

    def stop(session_id, env, prove):
        store = ThreadSafeDB()
        if prove:
            _dispatch("PostToolUse", session_id, store, env, tool_name=DESKTOP_TOOL, tool_input={})
        ctx = _hook_ctx(
            {"hook_event_name": "Stop", "session_id": session_id, "cli_type": "claude",
             "autorun_harness_env": env},
            store,
        )
        ctx.autorun_active = False
        ctx.autorun_stage = EventContext.STAGE_INACTIVE
        manager = TaskLifecycle(ctx=ctx)
        manager.create_task("1", {"subject": "Unfinished"}, "created")
        manager.update_task("1", {"status": "in_progress"}, "started")
        return plugins.app.dispatch(ctx)

    env = {**DESKTOP_ENV, **SWITCH}
    assert "Unfinished" in str(stop("stop-terminal", {"CLAUDE_CODE_ENTRYPOINT": "cli", **SWITCH}, False)), "control"
    assert "Unfinished" in str(stop("stop-desktop-no-proof", env, False))
    result = stop("stop-desktop-proof", env, True)
    assert result is None or "Unfinished" not in str(result)


def test_terminal_session_start_is_unchanged(isolated_session):
    text = _session_start("terminal-start", None)

    assert "ToolSearch" in text
    assert "select:Task" in text
    assert DESKTOP_TOOL not in text
