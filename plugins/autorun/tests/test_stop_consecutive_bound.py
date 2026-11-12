"""Regression tests for the Stop loop guard.

The guard counts consecutive main-agent Stops with no main-agent tool call
between them; it is session-scoped and advisory. Real tasks stay
authoritative: reaching the configured bound passes one Stop through (the
gate returns None with a notice, so stage verification still answers) and
must not complete, ignore, delete, pause, or otherwise mutate those tasks.
``_stop`` reproduces what the handler chain does with a None result.
"""

from __future__ import annotations

import copy
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
import multiprocessing
import time
from pathlib import Path

import pytest

from autorun import plugins
from autorun import task_lifecycle
from autorun.core import (
    AutorunApp,
    EventContext,
    ThreadSafeDB,
    dispatch_timeout_for_event,
    format_command_for_cli,
)
from autorun.config import CONFIG
from autorun.platforms import PLATFORMS, hook_platforms
from autorun.task_lifecycle import TaskLifecycle, TaskLifecycleConfig
from task_tool_evidence import task_tool_evidence


def _manager(tmp_path, *, stop_block_max_count: int) -> TaskLifecycle:
    config = TaskLifecycleConfig(
        enabled=True,
        storage_dir=tmp_path / "task-tracking",
        stop_block_max_count=stop_block_max_count,
        ghost_clear_enabled=False,
    )
    manager = TaskLifecycle(
        session_id=f"bounded-stop-{tmp_path.parent.name}-{tmp_path.name}",
        config=config,
    )
    manager.create_task(
        "plan-1",
        {
            "subject": "Finish release validation",
            "description": "A real task that must survive a bounded Stop yield.",
        },
        "created",
    )
    return manager


def _stop_context(manager: TaskLifecycle, store: ThreadSafeDB) -> EventContext:
    """A Stop context shaped like the one the daemon dispatches.

    `core.AutorunDaemon._dispatch_with_timeout` stamps every context it hands a
    handler with a deadline, and `session_manager.state_lock_timeout` spends
    that deadline rather than the flat `hook_state_lock_timeout_seconds` floor.
    A context built without one therefore waits 0.5s for the shared lock where
    production waits up to the 3s ceiling — so eight concurrent callbacks, the
    contention this module exists to test, queued past a budget no real Stop
    callback is given and raised `SessionTimeoutError` on a Windows runner.

    The deadline is `dispatch_timeout_for_event("Stop")` from now, exactly as
    the daemon computes it. Nothing about what is asserted changes: the bound
    is still `configured_max` blocks out of `callback_count` callbacks.
    """
    return EventContext(
        session_id=manager.session_id,
        event="Stop",
        tool_name="",
        tool_input={},
        tool_result="",
        session_transcript=[],
        store=store,
        cli_type="codex",
        # This module tests bounded-stop sequencing, not Codex's unknown-tool
        # fail-open policy. Model an authoritative checklist-tool receipt.
        active_tools=frozenset({"update_plan"}),
        deadline_monotonic=time.monotonic() + dispatch_timeout_for_event("Stop"),
    )


def _saved_manager(
    tmp_path,
    monkeypatch,
    *,
    stop_block_max_count: int = 3,
) -> TaskLifecycle:
    monkeypatch.setattr(
        task_lifecycle,
        "CONFIG_PATH",
        tmp_path / "task-lifecycle.config.json",
    )
    config = TaskLifecycleConfig(
        enabled=True,
        storage_dir=tmp_path / "task-tracking",
        stop_block_max_count=stop_block_max_count,
        ghost_clear_enabled=True,
    )
    config.save()
    manager = TaskLifecycle(
        session_id=f"bounded-stop-{tmp_path.name}",
        config=config,
    )
    manager.create_task(
        "plan-1",
        {"subject": "Finish release validation"},
        "created",
    )
    return manager


def _stop(manager: TaskLifecycle, ctx: EventContext) -> dict:
    """handle_stop's response, or what the chain flushes when it passes through."""
    response = manager.handle_stop(ctx)
    if response is not None:
        return response
    return ctx.respond("allow", "") if ctx._chain_notifications else {}


def _registered_handler(event: str, name: str):
    return next(handler for handler in plugins.app.chains[event] if handler.__name__ == name)


def _decision(response: dict) -> str:
    return "block" if response.get("decision") == "block" else "allow"


def _message(response: dict) -> str:
    return response.get("reason", response.get("systemMessage", ""))


def _process_stop(
    storage_dir: str,
    session_id: str,
    stop_block_max_count: int,
) -> dict:
    """Issue one Stop from an isolated spawned process."""
    manager = TaskLifecycle(
        session_id=session_id,
        config=TaskLifecycleConfig(
            enabled=True,
            storage_dir=Path(storage_dir),
            stop_block_max_count=stop_block_max_count,
            ghost_clear_enabled=False,
        ),
    )
    # ThreadSafeDB defaults to HOOK_STATE_LOCK_TIMEOUT (0.5s), a production
    # latency guarantee: a hook must never hang the harness waiting on state.
    # This test manufactures contention no hook sees -- six processes entering
    # the same critical section at once -- and on a Windows runner six
    # serialised read-modify-write cycles exceed 500ms, so the last waiter got
    # filelock.Timeout and the run failed on lock latency rather than on the
    # bound it exists to check. The assertion here is that exactly
    # stop_block_max_count Stops block, which is a correctness property and
    # independent of how long the queue takes to drain. Ten seconds is far
    # above the observed contention and far below the suite timeout, so a
    # genuine deadlock still fails rather than hanging.
    return _stop(manager, _stop_context(manager, ThreadSafeDB(state_timeout=10.0)))


@pytest.mark.parametrize("prompt", ["Discuss the release.", "/test"])
def test_user_prompt_chain_runs_before_command_or_passthrough(prompt):
    app = AutorunApp()
    calls = []

    @app.on("UserPromptSubmit")
    def record_prompt_boundary(ctx):
        calls.append(("chain", ctx.prompt))
        return None

    @app.command("/test")
    def test_command(ctx):
        calls.append(("command", ctx.prompt))
        return "ok"

    app.dispatch(
        EventContext(
            session_id="user-prompt-chain",
            event="UserPromptSubmit",
            prompt=prompt,
            store=ThreadSafeDB(),
            cli_type="claude",
            active_tools=task_tool_evidence("claude"),
        )
    )

    assert calls[0] == ("chain", prompt)
    if prompt == "/test":
        assert calls[1] == ("command", prompt)


@pytest.mark.parametrize("configured_max", [1, 3, 10])
def test_first_through_n_stops_block_and_n_plus_one_allows(
    tmp_path,
    configured_max,
):
    manager = _manager(tmp_path, stop_block_max_count=configured_max)
    store = ThreadSafeDB()

    decisions = [_decision(_stop(manager, _stop_context(manager, store))) for _ in range(configured_max + 1)]

    assert decisions == ["block"] * configured_max + ["allow"]


def test_allowed_stop_retains_tasks_and_keeps_counter_above_bound(tmp_path):
    configured_max = 3
    manager = _manager(tmp_path, stop_block_max_count=configured_max)
    store = ThreadSafeDB()
    tasks_before = copy.deepcopy(manager.tasks)

    responses = [_stop(manager, _stop_context(manager, store)) for _ in range(configured_max + 2)]

    assert [_decision(response) for response in responses] == [
        "block",
        "block",
        "block",
        "allow",
        "allow",
    ]
    assert manager.tasks == tasks_before
    assert manager.session_metadata["stop_block_count"] == configured_max + 2
    assert "retained 1 incomplete task" in _message(responses[configured_max])
    assert _message(responses[configured_max + 1]) == ""


def test_allow_clears_pending_replay_without_mutating_task(tmp_path):
    manager = _manager(tmp_path, stop_block_max_count=1)
    store = ThreadSafeDB()

    first_context = _stop_context(manager, store)
    assert _decision(manager.handle_stop(first_context)) == "block"
    assert first_context.pending_stop_injection

    second_context = _stop_context(manager, store)
    assert _decision(_stop(manager, second_context)) == "allow"
    assert second_context.pending_stop_injection is None
    assert manager.get_incomplete_tasks(exclude_blocking=True)[0]["id"] == "plan-1"


def test_non_task_posttooluse_resets_stop_sequence(
    tmp_path,
    monkeypatch,
):
    manager = _saved_manager(tmp_path, monkeypatch)
    store = ThreadSafeDB()

    for _ in range(2):
        assert _decision(_stop(manager, _stop_context(manager, store))) == "block"
    assert manager.session_metadata["stop_block_count"] == 2

    context = EventContext(
        session_id=manager.session_id,
        event="PostToolUse",
        tool_name="Read",
        tool_input={"file_path": "README.md"},
        tool_result="contents",
        store=store,
        cli_type="claude",
        active_tools=task_tool_evidence("claude"),
    )
    plugins.reset_stop_loop_guard_on_tool_activity(context)

    metadata = manager.session_metadata
    assert metadata["stop_block_count"] == 0


def test_subagent_posttooluse_does_not_reset_the_parent_stop_sequence(
    tmp_path,
    monkeypatch,
):
    """A subagent shares the parent's session on Claude; its tool call is not
    the main agent acting, so the parent's loop guard keeps counting."""
    manager = _saved_manager(tmp_path, monkeypatch)
    store = ThreadSafeDB()

    for _ in range(2):
        assert _decision(_stop(manager, _stop_context(manager, store))) == "block"

    context = EventContext(
        session_id=manager.session_id,
        event="PostToolUse",
        tool_name="Read",
        tool_input={"file_path": "README.md"},
        tool_result="contents",
        store=store,
        cli_type="claude",
        agent_id="subagent-7f3e",
        active_tools=task_tool_evidence("claude"),
    )
    plugins.reset_stop_loop_guard_on_tool_activity(context)

    assert manager.session_metadata["stop_block_count"] == 2


def test_productive_stop_tool_cycles_never_reach_bound(tmp_path, monkeypatch):
    manager = _saved_manager(
        tmp_path,
        monkeypatch,
        stop_block_max_count=3,
    )
    store = ThreadSafeDB()

    for cycle in range(8):
        response = _stop(manager, _stop_context(manager, store))
        assert _decision(response) == "block", f"productive cycle {cycle + 1}"
        plugins.reset_stop_loop_guard_on_tool_activity(
            EventContext(
                session_id=manager.session_id,
                event="PostToolUse",
                tool_name="Read",
                tool_input={"file_path": f"evidence-{cycle}.md"},
                tool_result="contents",
                store=store,
                cli_type="codex",
                active_tools=frozenset({"update_plan"}),
            )
        )
        assert manager.session_metadata["stop_block_count"] == 0


def test_parallel_posttooluse_resets_are_idempotent(tmp_path, monkeypatch):
    manager = _saved_manager(tmp_path, monkeypatch)
    store = ThreadSafeDB()
    for _ in range(2):
        assert _decision(_stop(manager, _stop_context(manager, store))) == "block"

    contexts = [
        EventContext(
            session_id=manager.session_id,
            event="PostToolUse",
            tool_name="Read",
            tool_input={"file_path": f"evidence-{index}.md"},
            tool_result="contents",
            store=store,
            cli_type="codex",
            active_tools=task_tool_evidence("codex"),
        )
        for index in range(8)
    ]
    with ThreadPoolExecutor(max_workers=len(contexts)) as executor:
        list(executor.map(plugins.reset_stop_loop_guard_on_tool_activity, contexts))

    metadata = manager.session_metadata
    assert metadata["stop_block_count"] == 0
    assert list(manager.tasks) == ["plan-1"]


def test_user_prompt_resets_sequence_before_next_stop(
    tmp_path,
    monkeypatch,
):
    manager = _saved_manager(tmp_path, monkeypatch)
    store = ThreadSafeDB()

    for _ in range(manager.config.stop_block_max_count):
        assert _decision(_stop(manager, _stop_context(manager, store))) == "block"

    handler = _registered_handler(
        "UserPromptSubmit",
        "reset_stop_sequence_on_user_prompt",
    )
    handler(
        EventContext(
            session_id=manager.session_id,
            event="UserPromptSubmit",
            prompt="Continue the release work.",
            store=store,
            cli_type="codex",
            active_tools=task_tool_evidence("codex"),
        )
    )

    assert manager.session_metadata["stop_block_count"] == 0
    assert _decision(_stop(manager, _stop_context(manager, store))) == "block"


def test_session_start_resets_sequence_before_resume_enforcement(
    tmp_path,
    monkeypatch,
):
    manager = _saved_manager(tmp_path, monkeypatch)
    store = ThreadSafeDB()

    for _ in range(manager.config.stop_block_max_count):
        assert _decision(_stop(manager, _stop_context(manager, store))) == "block"

    handler = _registered_handler("SessionStart", "resume_incomplete_tasks")
    response = handler(
        EventContext(
            session_id=manager.session_id,
            event="SessionStart",
            store=store,
            cli_type="codex",
            active_tools=frozenset({"update_plan"}),
        )
    )

    assert response is not None
    assert manager.session_metadata["stop_block_count"] == 0
    assert _decision(_stop(manager, _stop_context(manager, store))) == "block"


def test_concurrent_stop_callbacks_share_one_atomic_session_bound(tmp_path):
    configured_max = 3
    callback_count = 8
    manager = _manager(tmp_path, stop_block_max_count=configured_max)
    store = ThreadSafeDB()

    with ThreadPoolExecutor(max_workers=callback_count) as executor:
        responses = list(
            executor.map(
                lambda _index: _stop(manager, _stop_context(manager, store)),
                range(callback_count),
            )
        )

    decisions = [_decision(response) for response in responses]
    messages = [_message(response) for response in responses]
    assert decisions.count("block") == configured_max
    assert decisions.count("allow") == callback_count - configured_max
    assert sum(bool(message) for message in messages if message) == configured_max + 1
    assert sum("retained 1 incomplete task" in message for message in messages) == 1
    assert manager.session_metadata["stop_block_count"] == callback_count
    assert list(manager.tasks) == ["plan-1"]


def test_same_working_directory_keeps_session_stop_sequences_independent(tmp_path):
    config = TaskLifecycleConfig(
        enabled=True,
        storage_dir=tmp_path / "task-tracking",
        stop_block_max_count=1,
        ghost_clear_enabled=False,
    )
    managers = [TaskLifecycle(session_id=session_id, config=config) for session_id in ("same-cwd-session-a", "same-cwd-session-b")]
    for manager in managers:
        manager.create_task(
            "plan-1",
            {"subject": f"Finish work for {manager.session_id}"},
            "created",
        )

    stores = [ThreadSafeDB(), ThreadSafeDB()]
    assert [_decision(_stop(manager, _stop_context(manager, store))) for manager, store in zip(managers, stores)] == ["block", "block"]
    assert [_decision(_stop(manager, _stop_context(manager, store))) for manager, store in zip(managers, stores)] == ["allow", "allow"]
    assert [manager.session_metadata["stop_block_count"] for manager in managers] == [2, 2]


def test_spawned_processes_share_one_persistent_session_bound(tmp_path):
    configured_max = 3
    callback_count = 6
    manager = _manager(tmp_path, stop_block_max_count=configured_max)
    spawn_context = multiprocessing.get_context("spawn")

    with ProcessPoolExecutor(
        max_workers=callback_count,
        mp_context=spawn_context,
    ) as executor:
        responses = list(
            executor.map(
                _process_stop,
                [str(manager.config.storage_dir)] * callback_count,
                [manager.session_id] * callback_count,
                [configured_max] * callback_count,
            )
        )

    protocols = PLATFORMS["codex"].hook_protocol
    assert sum(response.get("decision") == protocols.stop_blocking_decision for response in responses) == configured_max
    assert sum(response.get("decision") != protocols.stop_blocking_decision for response in responses) == callback_count - configured_max
    assert manager.session_metadata["stop_block_count"] == callback_count
    assert list(manager.tasks) == ["plan-1"]


def test_n_plus_one_yield_is_audited_once(tmp_path):
    configured_max = 2
    config = TaskLifecycleConfig(
        enabled=True,
        storage_dir=tmp_path / "task-tracking",
        stop_block_max_count=configured_max,
        ghost_clear_enabled=False,
        debug_logging=True,
    )
    manager = TaskLifecycle(session_id="bounded-stop-audit", config=config)
    manager.create_task("plan-1", {"subject": "Retain this task"}, "created")
    store = ThreadSafeDB()

    for _ in range(configured_max + 3):
        _stop(manager, _stop_context(manager, store))

    audit_text = manager.audit_log.read_text(encoding="utf-8")
    assert audit_text.count("[STOP_LOOP_GUARD]") == 1
    assert "retained 1 incomplete task" in audit_text
    assert list(manager.tasks) == ["plan-1"]


@pytest.mark.parametrize(
    "cli_type",
    sorted(name for name, platform in PLATFORMS.items() if platform.has_hooks),
)
@pytest.mark.parametrize("stop_hook_active", [False, True])
def test_all_registered_harnesses_encode_block_then_bounded_allow(
    tmp_path,
    cli_type,
    stop_hook_active,
):
    manager = _manager(
        tmp_path / cli_type / str(stop_hook_active),
        stop_block_max_count=1,
    )
    store = ThreadSafeDB()

    def context() -> EventContext:
        ctx = _stop_context(manager, store)
        return EventContext(
            session_id=ctx.session_id,
            event=ctx.event,
            tool_name=ctx.tool_name,
            tool_input=ctx.tool_input,
            tool_result=ctx.tool_result,
            session_transcript=[],
            store=store,
            cli_type=cli_type,
            active_tools=task_tool_evidence(cli_type),
            stop_hook_active=stop_hook_active,
        )

    blocked = _stop(manager, context())
    allowed = _stop(manager, context())

    protocol = PLATFORMS[cli_type].hook_protocol
    assert blocked["decision"] == protocol.stop_blocking_decision
    assert blocked["reason"]
    assert allowed.get("decision") != protocol.stop_blocking_decision
    if PLATFORMS[cli_type].deferred_stop_notice_events:
        assert _message(allowed) == "", (
            f"{cli_type} must queue the notice for {sorted(PLATFORMS[cli_type].deferred_stop_notice_events)}"
        )
    elif allowed:
        assert format_command_for_cli("/ar:task status", cli_type) in _message(allowed)
    else:
        assert protocol.requires_json_for_unhandled_hook
    assert manager.get_incomplete_tasks(exclude_blocking=True)[0]["id"] == "plan-1"


@pytest.mark.parametrize("cli_type", sorted(p.name for p in hook_platforms()))
@pytest.mark.parametrize("stop_marker", ["", "stage1_message"])
def test_an_active_autorun_run_answers_a_stop_by_continuing(tmp_path, cli_type, stop_marker):
    """A Stop during an active run continues it, with no tmux observer.

    The Stop hook's continuation is the only re-prompt path: nothing starts an
    external process to type into the pane. A premature stop and a stage
    transition must both come back as the harness's own blocking decision.
    """
    transcript = CONFIG[stop_marker] if stop_marker else "I am done for now."
    ctx = EventContext(
        session_id=f"autorun-continue-{tmp_path.name}-{cli_type}",
        event="Stop",
        tool_name="",
        tool_input={},
        tool_result="",
        session_transcript=[{"role": "assistant", "content": transcript}],
        store=ThreadSafeDB(),
        cli_type=cli_type,
        deadline_monotonic=time.monotonic() + dispatch_timeout_for_event("Stop"),
    )
    ctx.autorun_active = True
    ctx.autorun_stage = EventContext.STAGE_1

    response = plugins.autorun_injection(ctx)

    protocol = PLATFORMS[cli_type].hook_protocol
    assert response is not None, "an active run let the session stop"
    assert response["decision"] == protocol.stop_blocking_decision
    assert response["reason"]
    assert ctx.autorun_active is True


def test_user_prompt_reset_failure_is_logged_and_prompt_dispatch_continues(
    monkeypatch,
    caplog,
):
    handler = _registered_handler(
        "UserPromptSubmit",
        "reset_stop_sequence_on_user_prompt",
    )

    def fail_reset(_self, _ctx):
        raise OSError("isolated reset failure")

    monkeypatch.setattr(TaskLifecycle, "restore_awaiting_user", fail_reset)
    response = handler(
        EventContext(
            session_id="reset-failure",
            event="UserPromptSubmit",
            prompt="Continue discussing.",
            store=ThreadSafeDB(),
            cli_type="codex",
            active_tools=task_tool_evidence("codex"),
        )
    )

    assert response is None
    assert "Task Stop-sequence prompt reset error: isolated reset failure" in caplog.text
