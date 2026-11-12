"""A delegation ends on evidence about the subagent, never on a clock.

Evidence that ends one (stop_gate_design.md 3b): a SubagentStop for the
agent id (``delegation-returned``, which blocks until verified); the parent
harness process that ran the subagent having exited (``pending`` with a
notice); the harness's own running-agents list once captured; a task update.
``delegation_ttl_seconds`` is removed. A delegation records
``metadata.delegation = {agent_id, cli_type, parent_session_id, harness_pid,
mode, delegated_at}``. A legacy row from before the upgrade has no
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


def _seed_open_receipts(count: int, *, harness_pid: int, newer_than: float) -> None:
    """``count`` children of other sessions, spawned after ``newer_than`` and still running."""
    with session_state(TASK_DELEGATION_RECEIPT_SESSION) as state:
        receipts = dict(state.get("receipts", {}))
        for n in range(count):
            receipts[f"codex:other-{n:05d}-0e6f-7bb2"] = {
                "agent_id": f"other-{n:05d}-0e6f-7bb2",
                "cli_type": "codex",
                "parent_session_id": f"other-parent-{n}",
                "at": newer_than + n + 1,
                "returned": False,
                "harness_pid": harness_pid,
            }
        state["receipts"] = receipts


def _receipts() -> dict:
    with session_state(TASK_DELEGATION_RECEIPT_SESSION) as state:
        return dict(state.get("receipts", {}))


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


def test_open_receipt_is_reclaimed_only_when_its_harness_process_is_gone(gate_env):
    """Growth is bounded by evidence: a receipt whose parent harness exited is
    dropped at the next SubagentStop; one whose harness runs is kept."""
    gone, alive = dead_pid(), os.getppid()
    _seed_open_receipts(3, harness_pid=gone, newer_than=time.time())
    with session_state(TASK_DELEGATION_RECEIPT_SESSION) as state:
        receipts = dict(state["receipts"])
        receipts["codex:kept-0e6f-7bb2-9d1e"] = {
            "agent_id": "kept-0e6f-7bb2-9d1e", "cli_type": "codex", "parent_session_id": "p",
            "at": time.time(), "returned": False, "harness_pid": alive,
        }
        state["receipts"] = receipts
    reporter = _session("codex", "reclaim")

    reporter.dispatch("SubagentStop", agent_id="unrelated-0e6f-7bb2-9d1e")

    left = _receipts()
    assert "codex:kept-0e6f-7bb2-9d1e" in left, "a receipt whose harness still runs was dropped"
    assert not [key for key in left if key.startswith("codex:other-")], (
        f"receipts whose harness process exited were kept: {sorted(left)}"
    )


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
