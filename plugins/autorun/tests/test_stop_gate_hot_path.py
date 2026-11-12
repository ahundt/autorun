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
import uuid

import pytest

from autorun import session_manager as sm
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
