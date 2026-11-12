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
import uuid

import pytest

from stop_gate_support import (  # noqa: F401  (gate_env_fixture registers gate_env)
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
