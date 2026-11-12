"""What the Stop gate and the prompt handlers cost on the paths every session runs.

Acceptance conditions (stop_gate_design_decisions.md, "hot-path cost"):
a Stop that changes nothing writes nothing; the gate reads one task snapshot
per Stop and derives dependency waits from it in memory; a UserPromptSubmit
with nothing waiting on the user reads no task rows and writes nothing; the
process-liveness check runs at Stop and SessionStart only, one ``pid_exists``
per distinct recorded harness PID, never per tool call.

Run against the SQLite store, where each call is observable. Its session
scope opens ``BEGIN IMMEDIATE`` even to read, so "writes nothing" is measured
as no staged row (``SQLiteStore._stage``, ``TaskRepository.put_task``), and the
number of transactions is held to what the gate opened before this change,
measured at ce4b1fac with the same scenario: 4 for a Stop whose tasks were
all delegated, 2 for a prompt (one of them the prompt-time cache check).
"""

from __future__ import annotations

import contextlib
import os
import json
import sqlite3
import uuid

import pytest

from autorun import plugins, session_manager as sm
from stop_gate_support import (  # noqa: F401  (gate_env_fixture registers gate_env)
    DELEGATE,
    Session,
    blocks,
    gate_env_fixture,
)

BASELINE_WAITING_STOP_TRANSACTIONS = 4
BASELINE_PROMPT_TRANSACTIONS = 2
TASK_LISTS = ("list_tasks", "list_excluding_statuses", "list_incomplete")


@pytest.fixture(name="calls")
def calls_fixture(gate_env, monkeypatch):
    """SQLite store; counts of transactions, staged writes and task-list reads."""
    monkeypatch.setitem(sm._CONFIG, "state_backend", "sqlite")
    sm._reset_for_testing()
    counts = dict.fromkeys(("transactions", "staged", "put_task", *TASK_LISTS), 0)
    real_write = sm.SQLiteStore.write_transaction
    real_stage = sm.SQLiteStore._stage

    @contextlib.contextmanager
    def counting_write(self, owner):
        if not owner.transaction_depth:
            counts["transactions"] += 1
        with real_write(self, owner) as conn:
            yield conn

    def counting_stage(self, *args, **kwargs):
        counts["staged"] += 1
        return real_stage(self, *args, **kwargs)

    monkeypatch.setattr(sm.SQLiteStore, "write_transaction", counting_write)
    monkeypatch.setattr(sm.SQLiteStore, "_stage", counting_stage)
    for name in (*TASK_LISTS, "put_task"):
        real = getattr(sm.TaskRepository, name)

        def counting(self, *args, _name=name, _real=real, **kwargs):
            counts[_name] += 1
            return _real(self, *args, **kwargs)

        monkeypatch.setattr(sm.TaskRepository, name, counting)
    yield counts
    sm._reset_for_testing()


def _reset(counts: dict) -> None:
    for key in counts:
        counts[key] = 0


def _session(label: str, **fields) -> Session:
    return Session(f"hot-{label}-{uuid.uuid4().hex[:8]}", "claude", **fields)


def test_waiting_only_stop_writes_nothing_and_reads_one_snapshot(calls):
    session = _session("waiting")
    session.seed("3", "Sweep the fixtures")
    session.seed("2", "Publish after the sweep", status="pending", blocked_by=["3"])
    session.stop(DELEGATE.format(id="3"))  # enters the waiting state (writes)
    session.stop("Waiting for the subagent.")  # the first pass-through may settle counters

    _reset(calls)
    response = session.stop("Still waiting for the subagent.")

    assert not blocks(response, "claude")
    assert calls["staged"] == 0 and calls["put_task"] == 0, f"a Stop that changed nothing wrote: {calls}"
    reads = sum(calls[name] for name in TASK_LISTS)
    assert reads == 1, f"the gate read {reads} task lists for one Stop: {calls}"
    assert calls["list_tasks"] == 0, "the gate read every task of the session, finished history included"
    assert calls["transactions"] <= BASELINE_WAITING_STOP_TRANSACTIONS, (
        f"a waiting-only Stop opened {calls['transactions']} transactions; it opened "
        f"{BASELINE_WAITING_STOP_TRANSACTIONS} before this change"
    )


def test_prompt_with_nothing_awaiting_the_user_reads_and_writes_nothing(calls):
    session = _session("prompt")
    session.seed("1", "Write the notes")
    session.prompt("first prompt")  # settles the loop-guard counter

    _reset(calls)
    session.prompt("keep going")

    assert calls["staged"] == 0 and calls["put_task"] == 0, f"a prompt with nothing to restore wrote: {calls}"
    reads = sum(calls[name] for name in TASK_LISTS)
    assert reads == 0, f"a prompt with nothing awaiting the user read task lists: {calls}"
    assert calls["transactions"] < BASELINE_PROMPT_TRANSACTIONS, (
        f"a prompt with nothing to restore opened {calls['transactions']} transactions; the task "
        f"handler must read its counter without the writer slot (baseline {BASELINE_PROMPT_TRANSACTIONS})"
    )


def test_staleness_threshold_visits_only_unfinished_tasks(calls, monkeypatch):
    session = _session("staleness-history")
    session.seed("waiting", "Resume after a decision", status="paused")
    store = sm.get_session_manager().task_repository()._store
    with store.operation_scope() as owner:
        with store.write_transaction(owner) as conn:
            conn.executemany(
                "INSERT INTO tasks(session, task_id, status, updated_at, payload_json) VALUES (?, ?, 'completed', 1, ?)",
                [
                    (session.manager.global_key, str(i), json.dumps({"id": str(i)}))
                    for i in range(30000)
                ],
            )

    statements = []
    steps = [0]
    real_connect = store._connect

    def connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        conn.set_trace_callback(statements.append)

        def progress():
            steps[0] += 100
            return 0

        conn.set_progress_handler(progress, 100)
        return conn

    monkeypatch.setattr(store, "_connect", connect)
    crossing = session.context(
        "PostToolUse",
        tool_name="Bash",
        tool_input={"command": "printf threshold"},
        tool_result="ok",
    )
    crossing.task_staleness_initial_threshold = 1
    response = plugins.app.dispatch(crossing)

    snapshot = next(
        sql
        for sql in statements
        if sql.startswith("SELECT task_id, status, updated_at, payload_json FROM tasks")
    )
    with store.operation_scope() as owner:
        plan = [row[3] for row in owner.connection.execute("EXPLAIN QUERY PLAN " + snapshot)]
    assert "AUTORUN HAS NO ACTIVE TASKS RECORDED" not in json.dumps(response or {}), response
    assert any("tasks_retention (session=? AND prunable=?)" in row for row in plan), plan
    assert steps[0] < 5000, f"staleness threshold visited completed history: {steps[0]} VM steps; {plan}"


def test_liveness_runs_at_stop_only_once_per_distinct_harness_pid(gate_env, monkeypatch):
    import psutil

    checked: list[int] = []
    real = psutil.pid_exists

    def counting(pid):
        checked.append(pid)
        return real(pid)

    monkeypatch.setattr(psutil, "pid_exists", counting)
    other = os.getppid()  # alive, and not the pid the later hooks report
    session = _session("liveness", harness_pid=other)
    for task_id in ("4", "5", "6"):
        session.seed(task_id, f"Sweep part {task_id}")
    session.stop(DELEGATE.format(id="4,5,6"))
    assert {session.status(task_id) for task_id in ("4", "5", "6")} == {"delegated"}

    resumed = Session(session.session_id, "claude", store=session.store, harness_pid=os.getpid())
    checked.clear()
    for _ in range(5):
        resumed.tool("Bash", {"command": "ls"}, "ok")
    assert checked == [], f"PostToolUse checked process liveness: {checked}"

    resumed.stop("Waiting.")
    assert checked == [other], f"one Stop checked {checked}; expected one check of the one recorded pid {other}"


@pytest.mark.parametrize("historical", [False, True])
def test_stop_snapshot_visits_only_open_tasks(calls, monkeypatch, historical, tmp_path):
    from test_task_status_not_in_schema import _create_pre_3472b3ee_database

    session = _session("history")
    session.seed("open", "Finish the remaining work", status="pending")
    store = sm.get_session_manager().task_repository()._store
    if historical:
        path = tmp_path / "old.sqlite3"
        _create_pre_3472b3ee_database(path)
        old = sm.SQLiteStore(path)
        old.initialize()
        with sqlite3.connect(path) as src, sqlite3.connect(store.db_path) as dst:
            src.backup(dst)
        session.seed("open", "Finish the remaining work", status="pending")
    with store.operation_scope() as owner:
        with store.write_transaction(owner) as conn:
            conn.executemany(
                "INSERT INTO tasks(session, task_id, status, updated_at, payload_json) VALUES (?, ?, 'completed', 1, ?)",
                [(session.manager.global_key, str(i), json.dumps({"id": str(i)})) for i in range(30000)],
            )
    statements = []
    steps = [0]
    real_connect = store._connect

    def connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        conn.set_trace_callback(statements.append)
        def progress():
            steps[0] += 100
            return 0
        conn.set_progress_handler(progress, 100)
        return conn

    monkeypatch.setattr(store, "_connect", connect)
    assert blocks(session.stop("There is work left."), "claude")
    snapshot = next(sql for sql in statements if sql.startswith("SELECT task_id, status, updated_at, payload_json FROM tasks"))
    with store.operation_scope() as owner:
        plan = [row[3] for row in owner.connection.execute("EXPLAIN QUERY PLAN " + snapshot)]
    assert any("tasks_retention (session=? AND prunable=?)" in row for row in plan), plan
    assert steps[0] < 5000, f"Stop visited completed history: {steps[0]} VM steps; {plan}"
