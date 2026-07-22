"""Lifecycle integration contract for the row-backed task store."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parent.parent
SRC_DIR = PLUGIN_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autorun import session_manager as sm  # noqa: E402
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
