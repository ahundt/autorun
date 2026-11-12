"""A delegation ends on evidence about the subagent, never on a clock.

Evidence that ends one (stop_gate_design.md 3b): a SubagentStop for the
agent id (``delegation-returned``, which blocks until verified); the parent
harness process that ran the subagent having exited (``pending`` with a
notice); the harness's own running-agents list once captured; a task update.
``delegation_ttl_seconds`` is removed. A delegation records
``metadata.delegation = {agent_id, cli_type, parent_session_id, harness_pid,
harness_started_at_units, mode, delegated_at}``. A legacy row from before the upgrade has no
``harness_pid`` and becomes ``delegation-returned`` so the agent verifies it
(decisions file, default 3).

The clock-free return over hours and days, and the revert when the harness
process dies at a Stop, are the end-to-end scenario in test_stop_gate_e2e.py.
"""

from __future__ import annotations

import os
import time
import uuid

import pytest

from autorun import session_manager as sm, task_lifecycle as tl
from autorun.session_manager import TASK_DELEGATION_RECEIPT_SESSION, session_state
from stop_gate_support import (  # noqa: F401  (gate_env_fixture registers gate_env)
    CODEX_SPAWN_ID,
    DELEGATE,
    SPAWNS,
    Session,
    blocks,
    dead_pid,
    gate_env_fixture,
    text,
)

def _session(cli_type: str, label: str, **fields) -> Session:
    return Session(f"live-{label}-{cli_type}-{uuid.uuid4().hex[:8]}", cli_type, **fields)


@pytest.mark.parametrize("cli_type", sorted(SPAWNS))
def test_delegation_records_the_agent_and_the_harness_process(gate_env, cli_type):
    session = _session(cli_type, "record")
    session.seed("7", "Sweep the fixtures")
    _tool, _result, agent_id = SPAWNS[cli_type]

    session.delegate("7")

    task = session.task("7")
    assert task["status"] == "delegated"
    record = task.get("metadata", {}).get("delegation")
    assert isinstance(record, dict), f"{cli_type}: metadata.delegation was not stored: {task.get('metadata')}"
    assert record.get("agent_id") == agent_id
    assert record.get("cli_type") == cli_type
    assert record.get("parent_session_id") == session.session_id
    assert record.get("harness_pid") == os.getpid(), "harness_pid must come from the ctx that consumed the marker"
    assert record.get("harness_started_at_units") == session.harness_started_at_units
    if cli_type == "claude":
        assert record.get("mode") == "async", "an async_launched spawn must be recorded as mode async"


def test_legacy_delegation_without_recorded_pid_asks_for_verification(gate_env):
    """A row delegated before the upgrade carries delegated_to_session and
    delegated_at but no harness_pid: liveness is unknown, so the agent verifies."""
    session = _session("claude", "legacy")
    session.seed("7", "Sweep the fixtures")
    session.manager.update_task(
        "7",
        {"status": "delegated", "metadata": {"delegated_to_session": "b9c8d7e6f5a4b3c2d", "delegated_at": 1_762_800_000.0}},
        "delegated before the upgrade",
    )

    response = session.stop("Done for now.")

    assert session.status("7") == "delegation-returned", (
        f"a legacy delegation with no recorded harness process became {session.status('7')!r}, not delegation-returned"
    )
    assert blocks(response, "claude"), f"the returned legacy delegation did not block for verification: {response}"
    assert "liveness not recorded before upgrade" in text(response), f"no upgrade notice: {response}"


@pytest.mark.parametrize("cli_type", sorted(SPAWNS))
def test_dead_harness_pid_reverts_at_session_start(gate_env, cli_type):
    """Evidence 2 is checked at SessionStart as well as at Stop."""
    session = _session(cli_type, "resume", harness_pid=dead_pid())
    session.seed("7", "Sweep the fixtures")
    session.delegate("7")
    assert session.status("7") == "delegated"

    resumed = Session(session.session_id, cli_type, store=session.store)
    response = resumed.dispatch("SessionStart", source="resume")

    assert resumed.status("7") == "pending", (
        f"{cli_type}: the harness process that ran the subagent is gone, yet #7 stayed {resumed.status('7')!r}"
    )
    assert "back to pending" in text(response), f"{cli_type}: no notice at SessionStart: {response}"


def test_reused_harness_pid_with_different_birth_reverts_delegation(gate_env, monkeypatch):
    """A live unrelated process that reused the recorded PID is end evidence."""
    from autorun import ipc
    import psutil

    reused_pid = 424_242
    recorded_birth = 1_700_000_000_000_000
    session = _session(
        "codex",
        "pid-reused",
        harness_pid=reused_pid,
        harness_started_at_units=recorded_birth,
    )
    session.seed("7", "Sweep the fixtures")
    session.delegate("7")

    real_pid_exists = psutil.pid_exists
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: True if pid == reused_pid else real_pid_exists(pid))
    monkeypatch.setattr(ipc, "process_start_units", lambda pid: recorded_birth + 1 if pid == reused_pid else 0)
    resumed = Session(session.session_id, "codex", store=session.store)
    resumed.stop("Waiting for the subagent.")

    assert resumed.status("7") == "pending", "a different process instance with the same PID kept the delegation alive"


def test_same_harness_pid_and_birth_keeps_delegation_open(gate_env, monkeypatch):
    """The exact recorded process identity remains valid while that process lives."""
    from autorun import ipc
    import psutil

    harness_pid = 424_243
    recorded_birth = 1_700_000_000_000_000
    session = _session(
        "codex",
        "pid-same",
        harness_pid=harness_pid,
        harness_started_at_units=recorded_birth,
    )
    session.seed("7", "Sweep the fixtures")
    session.delegate("7")

    real_pid_exists = psutil.pid_exists
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: True if pid == harness_pid else real_pid_exists(pid))
    monkeypatch.setattr(ipc, "process_start_units", lambda pid: recorded_birth if pid == harness_pid else 0)
    resumed = Session(session.session_id, "codex", store=session.store)
    resumed.stop("Waiting for the subagent.")

    assert resumed.status("7") == "delegated", "the same live process identity ended the delegation"


def _seed_open_receipts(count: int, *, harness_pid: int, newer_than: float) -> None:
    """``count`` children of other sessions, spawned after ``newer_than`` and still running."""
    for n in range(count):
        agent_id = f"other-{n:05d}-0e6f-7bb2"
        with session_state(tl._delegation_receipt_session_id("codex", agent_id)) as state:
            state["receipt"] = {
                "agent_id": agent_id,
                "cli_type": "codex",
                "parent_session_id": f"other-parent-{n}",
                "parent_global_key": f"__task_lifecycle__other-parent-{n}",
                "at": newer_than + n + 1,
                "returned": False,
                "claimed": False,
                "harness_pid": harness_pid,
            }


def _receipts() -> dict:
    receipts = {}
    with session_state(TASK_DELEGATION_RECEIPT_SESSION) as state:
        receipts.update(state.get("receipts", {}))
    for receipt_session in sm.get_session_manager().list_sessions(namespace="task_delegation_receipts"):
        if receipt_session == TASK_DELEGATION_RECEIPT_SESSION:
            continue
        with session_state(receipt_session) as state:
            receipt = state.get("receipt")
            if isinstance(receipt, dict):
                receipts[f"{receipt.get('cli_type')}:{receipt.get('agent_id')}"] = dict(receipt)
    return receipts


def _replace_receipt(key: str, update) -> None:
    cli_type, agent_id = key.split(":", 1)
    receipt_session = tl._delegation_receipt_session_id(cli_type, agent_id)
    with session_state(receipt_session) as state:
        if isinstance(state.get("receipt"), dict):
            state["receipt"] = update(dict(state["receipt"]))
            return
    with session_state(TASK_DELEGATION_RECEIPT_SESSION) as state:
        receipts = dict(state.get("receipts", {}))
        receipts[key] = update(dict(receipts[key]))
        state["receipts"] = receipts


def test_open_receipt_survives_any_number_of_newer_open_receipts(gate_env):
    """A Codex child returns in its own session; only its receipt names the parent.
    However many other children were spawned after it and are still running,
    that receipt must still be there when the child reports."""
    parent = _session("codex", "receipts")
    parent.seed("7", "Sweep the fixtures")
    parent.delegate("7")
    assert parent.status("7") == "delegated"
    _seed_open_receipts(1024, harness_pid=os.getpid(), newer_than=time.time())

    parent.dispatch("SubagentStop", session_id=f"{parent.session_id}-child", agent_id=CODEX_SPAWN_ID)

    assert parent.status("7") == "delegation-returned", (
        f"the child's return was not routed to its parent: #7 is {parent.status('7')!r}, "
        "because its receipt was evicted by a count of newer open receipts"
    )


def test_receipt_read_reclaims_only_gone_harness_identity(gate_env):
    """Keyed reads reclaim the named dead receipt without scanning its peers."""
    gone, alive = dead_pid(), os.getppid()
    _seed_open_receipts(3, harness_pid=gone, newer_than=time.time())
    kept_id = "kept-0e6f-7bb2-9d1e"
    parent = tl.TaskLifecycle(session_id="p")
    parent.atomic_update_metadata(lambda metadata: metadata.update({"agent_spawns": []}))
    with session_state(tl._delegation_receipt_session_id("codex", kept_id)) as state:
        state["receipt"] = {
            "agent_id": kept_id,
            "cli_type": "codex",
            "parent_session_id": "p",
            "parent_global_key": "__task_lifecycle__p",
            "at": time.time(),
            "returned": False,
            "claimed": False,
            "harness_pid": alive,
        }
    reporter = _session("codex", "reclaim")

    for n in range(3):
        agent_id = f"other-{n:05d}-0e6f-7bb2"
        assert reporter.manager._delegation_receipt("codex", agent_id) is None
    assert reporter.manager._delegation_receipt("codex", kept_id) is not None

    left = _receipts()
    assert "codex:kept-0e6f-7bb2-9d1e" in left, "a receipt whose harness still runs was dropped"
    assert not [key for key in left if key.startswith("codex:other-")], (
        f"receipts whose harness process exited were kept: {sorted(left)}"
    )


def test_no_pid_receipt_is_reclaimed_when_parent_no_longer_delegates_to_child(gate_env):
    """Task state, not elapsed time, retires a claimed receipt with no PID."""
    parent = _session("codex", "no-pid-task-evidence")
    parent.seed("7", "Sweep the fixtures")
    parent.delegate("7")
    key = f"codex:{CODEX_SPAWN_ID}"

    def without_process(receipt):
        receipt.pop("harness_pid", None)
        receipt.pop("harness_started_at_units", None)
        receipt.pop("claimed", None)  # pre-keyed records infer this from the parent ledger
        return receipt

    _replace_receipt(key, without_process)
    parent.manager.update_task("7", {"status": "completed"}, "work completed")

    child = _session("codex", "no-pid-child")
    assert child.manager._delegation_receipt("codex", CODEX_SPAWN_ID) is None
    assert key not in _receipts(), "the completed parent task left an unbounded no-PID receipt"


@pytest.mark.parametrize("backend", ["json", "sqlite"])
def test_task_gc_removes_no_pid_receipt_with_deleted_parent(gate_env, monkeypatch, backend):
    """Deleting the parent task root is durable end evidence for its receipts."""
    monkeypatch.setitem(sm._CONFIG, "state_backend", backend)
    sm._reset_for_testing()
    parent = _session("codex", f"gc-no-pid-{backend}")
    parent.seed("7", "Sweep the fixtures")
    parent.delegate("7")
    key = f"codex:{CODEX_SPAWN_ID}"

    def without_process(receipt):
        receipt.pop("harness_pid", None)
        receipt.pop("harness_started_at_units", None)
        receipt.pop("claimed", None)
        return receipt

    _replace_receipt(key, without_process)
    parent.manager.update_task("7", {"status": "completed"}, "work completed")

    assert tl.TaskLifecycle.cli_gc(
        archive=True,
        dry_run=False,
        ttl_days=0,
        config=tl.TaskLifecycleConfig.load(),
        confirm=False,
        current_session_id="protected-other-session",
    ) == 0

    assert key not in _receipts(), f"{backend}: task gc left the deleted parent's receipt"


def test_every_unreturned_spawn_stays_in_the_ledger(gate_env):
    """20 children spawned before any is claimed: each stays claimable with
    its recorded mode, so naming the first one later still finds it."""
    session = _session("codex", "ledger")
    agent_ids = [f"01997b21-0e6f-7bb2-9d1e-{n:012d}" for n in range(20)]
    for agent_id in agent_ids:
        session.tool("spawn_agent", {"message": "sweep"}, {"agent_id": agent_id, "status": "spawned"})
    session.seed("7", "Sweep the fixtures")

    session.stop(DELEGATE.format(id=f"7, agent_session_id={agent_ids[0]}"))

    record = session.task("7")["metadata"]["delegation"]
    assert record["agent_id"] == agent_ids[0]
    assert record["mode"] == "async", (
        f"the first of 20 unclaimed spawns was evicted from the ledger by a count: mode {record['mode']!r}"
    )


def test_dead_harness_evidence_does_not_end_a_newer_delegation(gate_env, monkeypatch):
    """Liveness is checked outside the state lock. If the task is delegated
    again (new child, live harness) between that check and the write, the
    evidence about the old delegation must not send the new one to pending."""
    import psutil

    from autorun.task_lifecycle import TaskLifecycle

    old_harness = dead_pid()
    session = _session("codex", "race", harness_pid=old_harness)
    session.seed("7", "Sweep the fixtures")
    session.delegate("7")
    newer = {
        "agent_id": "01997b21-0e6f-7bb2-9d1e-0000000000ff",
        "cli_type": "codex",
        "parent_session_id": session.session_id,
        "harness_pid": os.getpid(),
        "mode": "async",
        "delegated_at": time.time(),
    }
    real = psutil.pid_exists

    def redelegated_meanwhile(pid):
        if pid == old_harness:
            def redelegate(tasks):
                tasks["7"]["metadata"]["delegation"] = dict(newer)
            TaskLifecycle(session_id=session.session_id).atomic_update_tasks(redelegate)
        return real(pid)

    monkeypatch.setattr(psutil, "pid_exists", redelegated_meanwhile)
    resumed = Session(session.session_id, "codex", store=session.store)
    resumed.stop("Waiting for the subagent.")

    task = session.task("7")
    assert task["status"] == "delegated", (
        f"evidence about the old delegation's harness ended the newer delegation: #7 is {task['status']!r}"
    )
    assert task["metadata"]["delegation"]["agent_id"] == newer["agent_id"]
