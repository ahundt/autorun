"""Delegation receipt operations stay keyed as the open-receipt set grows."""

from __future__ import annotations

import json
import os
import time
import uuid

import pytest

from autorun import session_manager as sm, task_lifecycle as tl
from autorun.core import ThreadSafeDB
from stop_gate_support import (  # noqa: F401  (gate_env_fixture registers gate_env)
    CODEX_SPAWN_ID,
    Session,
    gate_env_fixture,
)

OPEN_RECEIPTS = 5_000
MAX_RECEIPT_ENTRIES_TOUCHED = 4
MAX_SQL_STATEMENTS = 40
MAX_SQL_VM_STEPS = 2_000


def _seed_open_receipts(store, count: int) -> None:
    now = time.time()
    rows = []
    for index in range(count):
        agent_id = f"scale-{index:05d}"
        receipt_session = tl._delegation_receipt_session_id("codex", agent_id)
        receipt = {
            "agent_id": agent_id,
            "cli_type": "codex",
            "parent_session_id": f"scale-parent-{index:05d}",
            "parent_global_key": f"__task_lifecycle__scale-parent-{index:05d}",
            "at": now,
            "returned": False,
            "claimed": False,
            "harness_pid": os.getpid(),
        }
        rows.append((receipt_session, json.dumps(receipt, separators=(",", ":")), now))
    with store.operation_scope() as owner:
        with store.write_transaction(owner) as conn:
            conn.executemany(
                "INSERT INTO sessions(session, namespace, last_modified) VALUES (?, 'task_delegation_receipts', ?)",
                ((receipt_session, updated_at) for receipt_session, _payload, updated_at in rows),
            )
            conn.executemany(
                "INSERT INTO state(session, field, value_json, updated_at) VALUES (?, 'receipt', ?, ?)",
                rows,
            )


def _measure(store, monkeypatch, action) -> dict[str, int]:
    statements: list[str] = []
    vm_steps = [0]
    receipt_entries = [0]
    real_connect = store._connect
    real_bound = tl._bounded_delegation_receipts

    def connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        conn.set_trace_callback(statements.append)

        def progress():
            vm_steps[0] += 1
            return 0

        conn.set_progress_handler(progress, 1)
        return conn

    def count_entries(receipts, *, reclaim=False):
        receipt_entries[0] += len(receipts)
        return real_bound(receipts, reclaim=reclaim)

    with monkeypatch.context() as patch:
        patch.setattr(store, "_connect", connect)
        patch.setattr(tl, "_bounded_delegation_receipts", count_entries)
        action()

    return {
        "statements": len(statements),
        "vm_steps": vm_steps[0],
        "receipt_entries": receipt_entries[0],
    }


def test_spawn_and_subagent_stop_are_keyed_with_5000_open_receipts(gate_env, monkeypatch):
    """One spawn and one SubagentStop have constant keyed-store work at 5,000 rows."""
    monkeypatch.setitem(sm._CONFIG, "state_backend", "sqlite")
    sm._reset_for_testing()
    store = sm.get_session_manager()._store
    _seed_open_receipts(store, OPEN_RECEIPTS)
    parent = Session(f"receipt-scale-{uuid.uuid4().hex[:8]}", "codex", store=ThreadSafeDB())

    spawn_cost = _measure(
        store,
        monkeypatch,
        lambda: parent.tool(
            "spawn_agent",
            {"message": "sweep"},
            {"agent_id": CODEX_SPAWN_ID, "status": "spawned"},
        ),
    )

    parent.seed("7", "Sweep the fixtures")
    parent.delegate("7")
    child = Session(
        f"{parent.session_id}-child",
        "codex",
        store=parent.store,
    )
    return_cost = _measure(
        store,
        monkeypatch,
        lambda: child.dispatch("SubagentStop", agent_id=CODEX_SPAWN_ID),
    )

    receipt_roots = sm.get_session_manager().list_sessions(
        namespace="task_delegation_receipts"
    )
    assert len(receipt_roots) == OPEN_RECEIPTS, (
        "SubagentStop left an empty keyed receipt root behind: "
        f"{len(receipt_roots)} roots for {OPEN_RECEIPTS} still-open receipts"
    )

    print(f"spawn cost with {OPEN_RECEIPTS} open receipts: {spawn_cost}")
    print(f"SubagentStop cost with {OPEN_RECEIPTS} open receipts: {return_cost}")
    for operation, cost in (("spawn", spawn_cost), ("SubagentStop", return_cost)):
        assert cost["receipt_entries"] <= MAX_RECEIPT_ENTRIES_TOUCHED, (
            f"{operation} traversed {cost['receipt_entries']} receipt entries"
        )
        assert cost["statements"] <= MAX_SQL_STATEMENTS, cost
        assert cost["vm_steps"] <= MAX_SQL_VM_STEPS, cost
