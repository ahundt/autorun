"""Lifecycle integration contract for the row-backed task store."""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parent.parent
SRC_DIR = PLUGIN_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autorun import session_manager as sm  # noqa: E402
from autorun.config import CONFIG  # noqa: E402
from autorun.core import EventContext, ThreadSafeDB  # noqa: E402
from autorun.session_manager import TaskRepository, session_state  # noqa: E402
from autorun.task_lifecycle import TaskLifecycle, TaskLifecycleConfig  # noqa: E402


@pytest.fixture
def sqlite_lifecycle(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    monkeypatch.setenv("AUTORUN_TEST_STATE_DIR", str(state_dir))
    monkeypatch.setitem(sm._CONFIG, "state_backend", "sqlite")
    sm._reset_for_testing()
    lifecycle = TaskLifecycle(
        session_id="sqlite-contract",
        config=TaskLifecycleConfig(storage_dir=tmp_path / "task-logs"),
    )
    yield lifecycle
    sm._reset_for_testing()


def _task(task_id: str, status: str = "pending") -> dict:
    now = time.time()
    return {
        "id": task_id,
        "subject": f"Task {task_id}",
        "description": "",
        "activeForm": "",
        "status": status,
        "created_at": now,
        "updated_at": now,
        "session_id": "sqlite-contract",
        "owner": None,
        "blockedBy": [],
        "blocks": [],
        "metadata": {},
        "tool_outputs": [],
    }


def _stop_context(event: str = "Stop", transcript_path: str = "") -> EventContext:
    ctx = EventContext(
        session_id="sqlite-contract",
        event=event,
        tool_name="",
        tool_input={},
        tool_result="",
        session_transcript=[],
        transcript_path=transcript_path,
        store=ThreadSafeDB(),
        cli_type="claude",
    )
    ctx.autorun_active = True
    ctx.autorun_stage = EventContext.STAGE_1
    return ctx


def _seed_delegation(lifecycle: TaskLifecycle, *, expired: bool = False) -> str:
    agent_id = "a1b2c3d4e5f6a7b8c"
    delegated_at = (
        time.time() - float(CONFIG["delegation_ttl_seconds"]) - 60
        if expired
        else time.time()
    )
    lifecycle.create_task("delegated", {"subject": "Delegated"}, "created")
    repository = sm.get_session_manager().task_repository()
    task = repository.get_task(lifecycle.global_key, "delegated")
    task["status"] = "delegated"
    task["metadata"] = {
        "delegated_to_session": agent_id,
        "delegated_at": delegated_at,
    }
    repository.put_task(lifecycle.global_key, "delegated", task)
    with session_state(lifecycle.global_key) as state:
        state["session_metadata"] = {
            "agent_spawns": [
                {
                    "id": agent_id,
                    "at": delegated_at,
                    "claimed": True,
                    "returned": False,
                }
            ]
        }
    return agent_id


def test_legacy_task_blob_moves_to_rows_once_and_is_removed(sqlite_lifecycle):
    lifecycle = sqlite_lifecycle
    with session_state(lifecycle.global_key) as state:
        state["schema_version"] = lifecycle.SCHEMA_VERSION
        state["tasks"] = {
            "open": _task("open"),
            "done": _task("done", "completed"),
        }

    assert set(lifecycle.tasks) == {"open", "done"}

    manager = sm.get_session_manager()
    repository = manager.task_repository()
    assert repository is not None
    assert repository.get_task(lifecycle.global_key, "open")["status"] == "pending"
    with session_state(lifecycle.global_key) as state:
        assert "tasks" not in state
        assert state["task_rows_migrated"] is True


def test_bulk_task_update_applies_fields_and_dependencies_atomically(sqlite_lifecycle):
    lifecycle = sqlite_lifecycle
    lifecycle.create_task("one", {"subject": "One"}, "created")
    lifecycle.create_task("two", {"subject": "Two"}, "created")

    lifecycle.update_tasks(
        [
            {"taskId": "one", "status": "in_progress"},
            {
                "taskId": "two",
                "status": "pending",
                "addBlockedBy": ["one"],
                "addBlocks": ["three"],
            },
        ],
        "bulk update",
    )

    tasks = lifecycle.tasks
    assert tasks["one"]["status"] == "in_progress"
    assert tasks["two"]["blockedBy"] == ["one"]
    assert tasks["two"]["blocks"] == ["three"]
    assert tasks["one"]["tool_outputs"][-1] == "bulk update"
    assert tasks["two"]["tool_outputs"][-1] == "bulk update"


def test_bulk_task_update_rejects_duplicate_ids_before_writing(sqlite_lifecycle):
    lifecycle = sqlite_lifecycle
    lifecycle.create_task("one", {"subject": "One"}, "created")

    with pytest.raises(ValueError, match="duplicate taskId"):
        lifecycle.update_tasks(
            [
                {"taskId": "one", "status": "in_progress"},
                {"taskId": "one", "status": "completed"},
            ],
            "invalid bulk update",
        )

    assert lifecycle.tasks["one"]["status"] == "pending"
    assert lifecycle.tasks["one"]["tool_outputs"] == ["created"]


def test_task_update_rejects_conflicting_single_and_bulk_shapes(sqlite_lifecycle):
    ctx = EventContext(
        session_id=sqlite_lifecycle.session_id,
        event="PostToolUse",
        tool_name="TaskUpdate",
        tool_input={
            "taskId": "one",
            "taskUpdates": [{"taskId": "two", "status": "completed"}],
        },
        tool_result="invalid shape",
        session_transcript=[],
        store=ThreadSafeDB(),
        cli_type="claude",
    )

    with pytest.raises(ValueError, match="exactly one of taskId or taskUpdates"):
        sqlite_lifecycle.__class__(ctx=ctx, config=sqlite_lifecycle.config).handle_task_update(ctx)


def test_opencode_todos_replace_only_their_own_tasks(sqlite_lifecycle):
    lifecycle = sqlite_lifecycle.__class__(
        ctx=EventContext(
            session_id=sqlite_lifecycle.session_id,
            event="PostToolUse",
            tool_name="todowrite",
            tool_input={
                "todos": [
                    {
                        "id": "oc-1",
                        "content": "Ship parity",
                        "status": "in_progress",
                        "priority": "high",
                    }
                ]
            },
            tool_result="todo.updated",
            session_transcript=[],
            store=ThreadSafeDB(),
            cli_type="opencode",
        ),
        config=sqlite_lifecycle.config,
    )
    lifecycle.create_task("claude-1", {"subject": "Keep me"}, "created")
    lifecycle.handle_bulk_todos(lifecycle.ctx)

    assert set(lifecycle.tasks) == {"claude-1", "oc-1"}
    assert lifecycle.tasks["oc-1"]["subject"] == "Ship parity"
    assert lifecycle.tasks["oc-1"]["metadata"]["source"] == "opencode_todo"

    second_ctx = EventContext(
        session_id=lifecycle.session_id,
        event="PostToolUse",
        tool_name="todowrite",
        tool_input={
            "todos": [{"id": "oc-2", "content": "New todo", "status": "pending"}]
        },
        tool_result="todo.updated",
        session_transcript=[],
        store=ThreadSafeDB(),
        cli_type="opencode",
    )
    lifecycle.handle_bulk_todos(second_ctx)
    assert set(lifecycle.tasks) == {"claude-1", "oc-2"}

    cancelled_ctx = EventContext(
        session_id=lifecycle.session_id,
        event="PostToolUse",
        tool_name="todowrite",
        tool_input={
            "todos": [{"id": "oc-3", "content": "Cancelled", "status": "cancelled"}]
        },
        tool_result="todo.updated",
        session_transcript=[],
        store=ThreadSafeDB(),
        cli_type="opencode",
    )
    lifecycle.handle_bulk_todos(cancelled_ctx)
    assert set(lifecycle.tasks) == {"claude-1", "oc-3"}
    assert lifecycle.tasks["oc-3"]["status"] == "deleted"

    clear_ctx = EventContext(
        session_id=lifecycle.session_id,
        event="PostToolUse",
        tool_name="todowrite",
        tool_input={"todos": []},
        tool_result="todo.updated",
        session_transcript=[],
        store=ThreadSafeDB(),
        cli_type="opencode",
    )
    lifecycle.handle_bulk_todos(clear_ctx)
    assert set(lifecycle.tasks) == {"claude-1"}


def _dispatch_opencode_todos(session_id: str, todos: list, store: ThreadSafeDB) -> None:
    """Drive the registered PostToolUse task handler, not handle_bulk_todos."""
    from autorun import plugins

    plugins.app.dispatch(
        EventContext(
            session_id=session_id,
            event="PostToolUse",
            tool_name="todowrite",
            tool_input={"todos": todos},
            tool_result=json.dumps({"todos": todos}),
            session_transcript=[],
            store=store,
            cli_type="opencode",
        )
    )


def test_empty_todo_list_clears_opencode_tasks_through_the_registered_handler(
    sqlite_lifecycle,
):
    """The live path is the dispatcher, and it must route ``todos: []``.

    A truthiness gate there means clearing the OpenCode todo list leaves the
    stale in_progress task behind while a direct handle_bulk_todos call (the
    only thing the earlier test exercised) clears it.
    """
    store = ThreadSafeDB()
    sid = sqlite_lifecycle.session_id
    _dispatch_opencode_todos(sid, [{"id": "oc-1", "content": "Live", "status": "in_progress"}], store)
    assert "oc-1" in TaskLifecycle(session_id=sid, config=sqlite_lifecycle.config).tasks

    _dispatch_opencode_todos(sid, [], store)
    assert "oc-1" not in TaskLifecycle(session_id=sid, config=sqlite_lifecycle.config).tasks


def test_opencode_todo_statuses_are_normalized_before_persistence(sqlite_lifecycle):
    """Both ``cancelled`` spellings mean deleted; anything else falls back to
    pending instead of raising out of the SQLite status policy and dropping
    the whole sync (or, on JSON, blocking Stop forever on an unknown status).
    """
    store = ThreadSafeDB()
    sid = sqlite_lifecycle.session_id
    _dispatch_opencode_todos(
        sid,
        [
            {"id": "a", "content": "uk", "status": "cancelled"},
            {"id": "b", "content": "us", "status": "canceled"},
            {"id": "c", "content": "typo", "status": "done"},
            {"id": "d", "content": "ok", "status": "completed"},
        ],
        store,
    )
    tasks = TaskLifecycle(session_id=sid, config=sqlite_lifecycle.config).tasks
    assert {k: tasks[k]["status"] for k in ("a", "b", "c", "d")} == {
        "a": "deleted", "b": "deleted", "c": "pending", "d": "completed",
    }


def test_todos_without_ids_never_collide_with_explicit_ids(sqlite_lifecycle):
    """Index fallback ids must not overwrite a todo that carries that id."""
    store = ThreadSafeDB()
    sid = sqlite_lifecycle.session_id
    _dispatch_opencode_todos(
        sid,
        [{"id": "2", "content": "explicit two"}, {"content": "no id at position two"}],
        store,
    )
    tasks = TaskLifecycle(session_id=sid, config=sqlite_lifecycle.config).tasks
    subjects = sorted(t["subject"] for t in tasks.values() if t["session_id"] == sid)
    assert subjects == ["explicit two", "no id at position two"]


def test_concurrent_bulk_dependency_edits_preserve_every_edge(sqlite_lifecycle):
    lifecycle = sqlite_lifecycle
    lifecycle.create_task("shared", {"subject": "Shared"}, "created")

    def apply(index):
        TaskLifecycle(
            session_id=lifecycle.session_id,
            config=lifecycle.config,
        ).update_tasks(
            [{"taskId": "shared", "addBlockedBy": [f"dep-{index}"]}],
            f"bulk-{index}",
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(apply, range(24)))

    assert set(lifecycle.tasks["shared"]["blockedBy"]) == {
        f"dep-{index}" for index in range(24)
    }


def test_subagent_stop_returns_row_backed_delegation(sqlite_lifecycle):
    agent_id = _seed_delegation(sqlite_lifecycle)
    ctx = _stop_context(
        "SubagentStop",
        f"/tmp/example-session/agent-{agent_id}.jsonl",
    )

    assert TaskLifecycle(ctx=ctx, config=sqlite_lifecycle.config).handle_stop(ctx) is None
    assert sqlite_lifecycle.tasks["delegated"]["status"] == "delegation-returned"


def test_codex_child_session_returns_parent_row_backed_delegation(
    sqlite_lifecycle,
):
    """The cross-session agent receipt and task transition share SQLite."""
    parent = sqlite_lifecycle
    agent_id = "01997b21-0e6f-7bb2-9d1e-4f0a2c3d5e6f"
    store = ThreadSafeDB()
    spawn_ctx = EventContext(
        session_id=parent.session_id,
        event="PostToolUse",
        tool_name="spawn_agent",
        tool_result={"agent_id": agent_id, "status": "spawned"},
        store=store,
        cli_type="codex",
    )
    parent.record_agent_spawn(spawn_ctx)
    parent.create_task("codex", {"subject": "Codex child"}, "created")
    parent.update_task(
        "codex",
        {
            "status": "delegated",
            "metadata": {
                "delegated_to_session": agent_id,
                "delegated_at": time.time(),
            },
        },
        "delegated",
    )
    child_ctx = EventContext(
        session_id="sqlite-codex-child",
        event="SubagentStop",
        tool_name="",
        agent_id=agent_id,
        transcript_path="/tmp/codex/parent.jsonl",
        agent_transcript_path=f"/tmp/codex/agent-{agent_id}.jsonl",
        store=store,
        cli_type="codex",
    )

    assert TaskLifecycle(ctx=child_ctx, config=parent.config).handle_stop(child_ctx) is None
    assert parent.tasks["codex"]["status"] == "delegation-returned"


def test_expired_row_backed_delegation_reverts_to_pending(sqlite_lifecycle):
    _seed_delegation(sqlite_lifecycle, expired=True)
    ctx = _stop_context()

    response = TaskLifecycle(ctx=ctx, config=sqlite_lifecycle.config).handle_stop(ctx)

    assert response is not None
    assert sqlite_lifecycle.tasks["delegated"]["status"] == "pending"


def test_blob_to_row_conversion_rolls_back_as_one_unit(
    sqlite_lifecycle, monkeypatch
):
    lifecycle = sqlite_lifecycle
    original = {"one": _task("one"), "two": _task("two")}
    with session_state(lifecycle.global_key) as state:
        state["schema_version"] = lifecycle.SCHEMA_VERSION
        state["tasks"] = original

    real_put = TaskRepository.put_task
    calls = 0

    def fail_second_put(repository, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated conversion crash")
        return real_put(repository, *args, **kwargs)

    monkeypatch.setattr(TaskRepository, "put_task", fail_second_put)
    with pytest.raises(RuntimeError, match="simulated conversion crash"):
        _ = lifecycle.tasks

    monkeypatch.setattr(TaskRepository, "put_task", real_put)
    repository = sm.get_session_manager().task_repository()
    assert repository.list_tasks(lifecycle.global_key) == {}
    with session_state(lifecycle.global_key) as state:
        assert state["tasks"] == original
        assert "task_rows_migrated" not in state


def test_hot_update_does_not_list_or_decode_unrelated_tasks(
    sqlite_lifecycle, monkeypatch
):
    lifecycle = sqlite_lifecycle
    lifecycle.create_task("target", {"subject": "Target"}, "created")
    repository = sm.get_session_manager().task_repository()
    assert repository is not None
    store = repository._store
    now = time.time()
    with store.operation_scope(30.0) as owner:
        with store.write_transaction(owner) as connection:
            connection.execute(
                "INSERT INTO tasks "
                "(session, task_id, status, updated_at, payload_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    lifecycle.global_key,
                    "unrelated",
                    "completed",
                    now,
                    "not valid json",
                ),
            )

    def forbid_bulk_read(*_args, **_kwargs):
        raise AssertionError("a one-task update used the bulk task path")

    monkeypatch.setattr(TaskRepository, "list_tasks", forbid_bulk_read)
    lifecycle.update_task("target", {"status": "completed"}, "done")

    assert repository.get_task(lifecycle.global_key, "target")["status"] == "completed"


def test_complete_output_history_moves_to_events_while_task_row_stays_bounded(
    sqlite_lifecycle
):
    lifecycle = sqlite_lifecycle
    lifecycle.create_task("long", {"subject": "Long"}, "created")
    for index in range(100):
        lifecycle.update_task("long", {}, f"output-{index}")

    repository = sm.get_session_manager().task_repository()
    task = repository.get_task(lifecycle.global_key, "long")
    events = repository.events(lifecycle.global_key, task_id="long")

    assert len(task["tool_outputs"]) == lifecycle.TASK_OUTPUT_RECENT_LIMIT
    assert task["tool_outputs"][-1] == "output-99"
    assert [event["payload"]["output"] for event in events] == [
        "created", *(f"output-{index}" for index in range(100))
    ]
    assert all(event["projected_at"] is not None for event in events)


def test_retried_hook_update_does_not_duplicate_output_or_event(sqlite_lifecycle):
    lifecycle = TaskLifecycle(
        ctx=EventContext(
            session_id="sqlite-contract",
            event="PostToolUse",
            tool_name="update_plan",
            tool_input={"task_id": "retried", "result": "same output"},
            session_transcript=[{"role": "assistant", "content": "tool call"}],
        ),
        config=sqlite_lifecycle.config,
    )
    lifecycle.create_task("retried", {"subject": "Retried"}, "created")

    lifecycle.update_task("retried", {"status": "in_progress"}, "same output")
    lifecycle.update_task("retried", {"status": "in_progress"}, "same output")

    repository = sm.get_session_manager().task_repository()
    task = repository.get_task(lifecycle.global_key, "retried")
    events = repository.events(lifecycle.global_key, task_id="retried")

    assert task["tool_outputs"] == ["created", "same output"]
    assert [event["payload"]["output"] for event in events] == [
        "created",
        "same output",
    ]


def test_cli_clear_removes_sqlite_task_rows_and_events(sqlite_lifecycle):
    lifecycle = sqlite_lifecycle
    lifecycle.create_task("clear-me", {"subject": "Clear me"}, "created")
    with session_state(lifecycle.global_key) as state:
        state["clear_marker"] = "present"

    assert TaskLifecycle.cli_clear(
        session_id=lifecycle.session_id, confirm=False
    ) == 0

    repository = sm.get_session_manager().task_repository()
    assert repository.list_tasks(lifecycle.global_key) == {}
    assert repository.events(lifecycle.global_key) == []
    with session_state(lifecycle.global_key) as state:
        assert "clear_marker" not in state


def test_clear_session_rolls_back_dependents_and_closes_connection_on_failure(
    sqlite_lifecycle
):
    lifecycle = sqlite_lifecycle
    lifecycle.create_task("preserved", {"subject": "Preserved"}, "created")
    manager = sm.get_session_manager()
    repository = manager.task_repository()
    store = repository._store
    with store.operation_scope(30.0) as owner:
        with store.write_transaction(owner) as connection:
            connection.execute(
                "CREATE TRIGGER refuse_session_clear "
                "BEFORE DELETE ON sessions BEGIN "
                "SELECT RAISE(ABORT, 'injected clear failure'); END"
            )

    with pytest.raises(sm.SessionBackendError, match="injected clear failure"):
        manager.clear_session(lifecycle.global_key)

    assert set(repository.list_tasks(lifecycle.global_key)) == {"preserved"}
    assert len(repository.events(lifecycle.global_key, task_id="preserved")) == 1
    assert store.open_connection_count() == 0


def test_cli_clear_failure_preserves_parent_delegation_receipt(sqlite_lifecycle):
    """Receipt authority is removed only after the parent session commits."""
    lifecycle = sqlite_lifecycle
    lifecycle.create_task("preserved", {"subject": "Preserved"}, "created")
    key = "claude:a1b2c3d4e5f6a7b8c"
    with session_state("__task_delegation_receipts__") as state:
        state["receipts"] = {
            key: {
                "agent_id": "a1b2c3d4e5f6a7b8c",
                "cli_type": "claude",
                "parent_session_id": lifecycle.session_id,
                "parent_global_key": lifecycle.global_key,
                "at": time.time(),
                "returned": False,
            }
        }

    store = sm.get_session_manager().task_repository()._store
    with store.operation_scope(30.0) as owner:
        with store.write_transaction(owner) as connection:
            connection.execute(
                "CREATE TRIGGER refuse_cli_clear "
                "BEFORE DELETE ON sessions BEGIN "
                "SELECT RAISE(ABORT, 'injected cli clear failure'); END"
            )

    assert TaskLifecycle.cli_clear(
        session_id=lifecycle.session_id, confirm=False
    ) == 1
    with session_state("__task_delegation_receipts__") as state:
        assert key in state["receipts"]
    assert set(lifecycle.tasks) == {"preserved"}


def test_cli_clear_receipt_failure_rolls_back_parent_deletion(sqlite_lifecycle):
    """Failure while pruning the registry preserves parent authority too."""
    lifecycle = sqlite_lifecycle
    lifecycle.create_task("preserved", {"subject": "Preserved"}, "created")
    key = "claude:a1b2c3d4e5f6a7b8c"
    with session_state("__task_delegation_receipts__") as state:
        state["receipts"] = {
            key: {
                "agent_id": "a1b2c3d4e5f6a7b8c",
                "cli_type": "claude",
                "parent_session_id": lifecycle.session_id,
                "parent_global_key": lifecycle.global_key,
                "at": time.time(),
                "returned": False,
            }
        }

    store = sm.get_session_manager().task_repository()._store
    with store.operation_scope(30.0) as owner:
        with store.write_transaction(owner) as connection:
            connection.execute(
                "CREATE TRIGGER refuse_receipt_cleanup "
                "BEFORE DELETE ON state "
                "WHEN OLD.session = '__task_delegation_receipts__' BEGIN "
                "SELECT RAISE(ABORT, 'injected receipt cleanup failure'); END"
            )

    assert TaskLifecycle.cli_clear(
        session_id=lifecycle.session_id, confirm=False
    ) == 1
    assert set(lifecycle.tasks) == {"preserved"}
    with session_state("__task_delegation_receipts__") as state:
        assert key in state["receipts"]
    assert store.open_connection_count() == 0


def test_cli_clear_all_failure_rolls_back_every_parent_and_receipt(sqlite_lifecycle):
    """A later parent-delete failure rolls back earlier deletes in the batch."""
    first = sqlite_lifecycle
    second = TaskLifecycle(
        session_id="sqlite-clear-second",
        config=first.config,
    )
    first.create_task("first", {"subject": "First"}, "created")
    second.create_task("second", {"subject": "Second"}, "created")
    receipts = {}
    for lifecycle, agent_id in (
        (first, "a1b2c3d4e5f6a7b8c"),
        (second, "b1c2d3e4f5a6b7c8d"),
    ):
        receipts[f"claude:{agent_id}"] = {
            "agent_id": agent_id,
            "cli_type": "claude",
            "parent_session_id": lifecycle.session_id,
            "parent_global_key": lifecycle.global_key,
            "at": time.time(),
            "returned": False,
        }
    with session_state("__task_delegation_receipts__") as state:
        state["receipts"] = receipts

    store = sm.get_session_manager().task_repository()._store
    with store.operation_scope(30.0) as owner:
        with store.write_transaction(owner) as connection:
            connection.execute(
                "CREATE TRIGGER refuse_second_parent_clear "
                "BEFORE DELETE ON sessions "
                f"WHEN OLD.session = '{second.global_key}' BEGIN "
                "SELECT RAISE(ABORT, 'injected second parent failure'); END"
            )

    assert TaskLifecycle.cli_clear(all_sessions=True, confirm=False) == 1
    assert set(first.tasks) == {"first"}
    assert set(second.tasks) == {"second"}
    with session_state("__task_delegation_receipts__") as state:
        assert state["receipts"] == receipts
    assert store.open_connection_count() == 0


def test_legacy_gc_refuses_sqlite_instead_of_claiming_no_data(
    sqlite_lifecycle, capsys
):
    lifecycle = sqlite_lifecycle
    lifecycle.create_task("keep-me", {"subject": "Keep me"}, "created")

    assert TaskLifecycle.cli_gc(dry_run=True, confirm=False) == 1

    message = capsys.readouterr().out
    assert "not available for the SQLite backend" in message
    assert "--state-maintenance" in message
    assert set(lifecycle.tasks) == {"keep-me"}


def test_legacy_outputs_are_imported_to_events_without_loss(sqlite_lifecycle):
    lifecycle = sqlite_lifecycle
    legacy = _task("legacy")
    legacy["tool_outputs"] = [f"old-{index}" for index in range(80)]
    with session_state(lifecycle.global_key) as state:
        state["schema_version"] = lifecycle.SCHEMA_VERSION
        state["tasks"] = {"legacy": legacy}

    assert lifecycle.tasks["legacy"]["tool_outputs"] == [
        f"old-{index}" for index in range(16, 80)
    ]
    repository = sm.get_session_manager().task_repository()
    assert [
        event["payload"]["output"]
        for event in repository.events(lifecycle.global_key, task_id="legacy")
    ] == [f"old-{index}" for index in range(80)]


def test_stop_query_does_not_decode_terminal_rows(sqlite_lifecycle):
    lifecycle = sqlite_lifecycle
    lifecycle.create_task("open", {"subject": "Open"}, "created")
    # Establish row migration before injecting a deliberately undecodable
    # terminal payload. The Stop query must exclude it in SQL.
    assert [task["id"] for task in lifecycle.get_incomplete_tasks()] == ["open"]
    repository = sm.get_session_manager().task_repository()
    assert repository is not None
    store = repository._store
    with store.operation_scope(30.0) as owner:
        with store.write_transaction(owner) as connection:
            connection.execute(
                "INSERT INTO tasks "
                "(session, task_id, status, updated_at, payload_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    lifecycle.global_key,
                    "done",
                    "completed",
                    time.time(),
                    json.dumps({"broken": "\ud800"}),
                ),
            )

    assert [task["id"] for task in lifecycle.get_incomplete_tasks()] == ["open"]


def test_delegation_marker_updates_row_and_receipt_in_one_transaction(
    sqlite_lifecycle
):
    lifecycle = sqlite_lifecycle
    lifecycle.create_task("work", {"subject": "Work"}, "created")

    assert lifecycle._consume_delegation_marker(
        "message-identity", ["work"],
        allowed_task_ids=["work"], session="child-session",
    ) == ["work"]

    repository = sm.get_session_manager().task_repository()
    task = repository.get_task(lifecycle.global_key, "work")
    assert task["status"] == "delegated"
    assert task["metadata"]["delegated_to_session"] == "child-session"
    with session_state(lifecycle.global_key) as state:
        assert "message-identity" in state["session_metadata"][
            "consumed_delegation_markers"
        ]

    assert lifecycle._consume_delegation_marker(
        "message-identity", ["work"],
        allowed_task_ids=["work"], session="other-child",
    ) == []


def test_process_restart_recovers_every_outstanding_status(sqlite_lifecycle):
    lifecycle = sqlite_lifecycle
    for task_id, status in (
        ("pending", "pending"),
        ("active", "in_progress"),
        ("paused", "paused"),
        ("delegated", "delegated"),
        ("done", "completed"),
    ):
        lifecycle.create_task(task_id, {"subject": task_id.title()}, "created")
        if status != "pending":
            lifecycle.update_task(task_id, {"status": status}, status)

    # Drop every process-local manager/cache and reconstruct the lifecycle from
    # the same durable state directory, as a daemon restart or resumed CLI does.
    config = lifecycle.config
    sm._reset_for_testing()
    resumed = TaskLifecycle(session_id="sqlite-contract", config=config)
    ctx = EventContext(
        session_id="sqlite-contract",
        event="SessionStart",
        prompt="",
        tool_input={},
        tool_result="",
        session_transcript=[],
        store=ThreadSafeDB(),
        cli_type="claude",
        source="resume",
    )
    ctx.autorun_active = False
    ctx.autorun_stage = EventContext.STAGE_INACTIVE

    response = resumed.handle_session_start(ctx)
    rendered = str(response)
    assert "Pending" in rendered
    assert "Active" in rendered
    assert "Paused" in rendered and "paused — resume when ready" in rendered
    assert "Delegated" in rendered and "delegated — check if complete" in rendered
    assert "Done" not in rendered

    tasks = resumed.tasks
    assert tasks["paused"]["status"] == "paused"
    assert tasks["delegated"]["status"] == "delegated"


@pytest.mark.parametrize("status", ["paused", "delegated"])
def test_nonblocking_recovery_does_not_arm_pretool_denial(
    sqlite_lifecycle, status
):
    lifecycle = sqlite_lifecycle
    lifecycle.create_task("work", {"subject": "Work"}, "created")
    lifecycle.update_task("work", {"status": status}, status)
    ctx = EventContext(
        session_id="sqlite-contract",
        event="SessionStart",
        prompt="",
        tool_input={},
        tool_result="",
        session_transcript=[],
        store=ThreadSafeDB(),
        cli_type="claude",
        source="resume",
    )
    ctx.autorun_active = False
    ctx.autorun_stage = EventContext.STAGE_INACTIVE

    assert lifecycle.handle_session_start(ctx) is not None
    assert ctx.task_staleness_enforce_next is False


def test_next_task_id_is_the_session_sequence_and_never_reuses_a_number(sqlite_lifecycle):
    """The daemon mints Pi-family ids, so the sequence has one owner and one shape:
    the smallest integer above every numeric id already recorded, whatever the
    status, so a deleted "3" is never handed out again; non-numeric ids from
    other sources (plan-2, opencode ids) do not shift the sequence."""
    lifecycle = sqlite_lifecycle
    assert lifecycle.next_task_id() == "1"
    lifecycle.create_task("1", {"subject": "One"}, "created")
    lifecycle.create_task("2", {"subject": "Two"}, "created")
    lifecycle.create_task("plan-9", {"subject": "Plan"}, "created")
    assert lifecycle.next_task_id() == "3"
    lifecycle.create_task("3", {"subject": "Three"}, "created")
    lifecycle.update_task("3", {"status": "deleted"}, "gone")
    assert lifecycle.next_task_id() == "4"
    lifecycle.create_task("10", {"subject": "Ten"}, "created")
    assert lifecycle.next_task_id() == "11"


def test_task_projection_lists_in_creation_order_not_id_string_order(sqlite_lifecycle):
    """Sequential ids are strings in storage; "10" must not sort before "9"."""
    lifecycle = sqlite_lifecycle
    for task_id in ("9", "10", "11"):
        lifecycle.create_task(task_id, {"subject": f"Task {task_id}"}, "created")
    lifecycle.update_task("10", {"addBlockedBy": ["11"]}, "blocked")

    rows = lifecycle.task_projection(limit=100)["tasks"]

    # ready tasks in creation order, then the blocked one
    assert [row["id"] for row in rows] == ["9", "11", "10"]
