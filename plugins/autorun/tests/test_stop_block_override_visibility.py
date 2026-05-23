"""Regression test for "infinite non-overridable stop failure" bug (v0.11.0).

Bug: When the AI repeatedly tries to Stop with incomplete tasks, the user-visible
override actions (`/ar:sos` and `/ar:task-ignore`) must be reliably reachable
by the AI on EVERY block — not only on the first block or when the
consecutive-identical counter happens to hit `min_consecutive`.

Failure mode (pre-fix):
  handle_stop() only stored `pending_stop_injection` on:
    1. block_count == 1
    2. ghost_enabled and consecutive == min_consecutive
  All other block paths returned a Stop systemMessage that on Claude Code only
  shows in the user terminal (Stop has no hookSpecificOutput → AI never sees it
  via additionalContext). The AI was then stuck "infinite non-overridable".

Pin matrix:
  cli_type      × block_count     × consecutive    × ghost_enabled
  claude/gemini × 1..50           × 1..10          × True/False

Each scenario must satisfy:
  - response systemMessage contains the override actions (user terminal)
  - ctx.pending_stop_injection is set with the override actions
    (so the next PostToolUse will deliver them to the AI)
"""
from __future__ import annotations

import time
import pytest

from autorun.core import EventContext, ThreadSafeDB
from autorun.task_lifecycle import TaskLifecycle, TaskLifecycleConfig


# === Sentinels — exact substrings that MUST appear in the user-visible channel ===
OVERRIDE_SOS = "/ar:sos"
OVERRIDE_TASK_IGNORE = "/ar:task-ignore"


# === Test fixtures (parallel to test_stop_chain_task_lifecycle.py) ===

@pytest.fixture
def isolated_task_config(tmp_path):
    """Isolated task lifecycle config — fresh storage dir per test."""
    return TaskLifecycleConfig(
        enabled=True,
        storage_dir=tmp_path / "task_lifecycle",
        task_ttl_days=30,
        max_resume_tasks=10,
        ghost_clear_enabled=True,
        ghost_clear_min_consecutive_blocks=2,
    )


@pytest.fixture
def isolated_session(tmp_path):
    """Isolated session state for testing."""
    from autorun import session_manager
    from autorun.session_manager import SessionStateManager, _reset_for_testing

    temp_state_dir = tmp_path / "sessions"
    temp_state_dir.mkdir(parents=True, exist_ok=True)

    _reset_for_testing()
    new_manager = SessionStateManager(state_dir=temp_state_dir)
    session_manager._manager = new_manager
    session_manager._store = new_manager._store

    yield new_manager

    _reset_for_testing()


def _make_stop_ctx(session_id: str, cli_type: str, store=None) -> EventContext:
    """Create a Stop EventContext with explicit cli_type."""
    return EventContext(
        session_id=session_id,
        event="Stop",
        tool_name="",
        tool_input={},
        tool_result="",
        session_transcript=[],
        store=store or ThreadSafeDB(),
        cli_type=cli_type,
    )


def _seed_tasks(manager: TaskLifecycle, n: int, prefix: str = "T") -> None:
    """Seed N in_progress tasks with deterministic IDs."""
    for i in range(n):
        tid = f"{prefix}{i}"
        def updater(tasks, _tid=tid, _i=i):
            tasks[_tid] = {
                "id": _tid,
                "subject": f"Task {_tid}",
                "description": "",
                "activeForm": "",
                "status": "in_progress",
                "created_at": time.time(),
                "updated_at": time.time(),
                "session_id": manager.session_id,
                "owner": None,
                "blockedBy": [],
                "blocks": [],
                "metadata": {},
                "tool_outputs": [],
            }
        manager.atomic_update_tasks(updater)


def _add_one_task(manager: TaskLifecycle, tid: str) -> None:
    """Add a single task to mutate the id-hash (resets consecutive)."""
    def updater(tasks):
        tasks[tid] = {
            "id": tid,
            "subject": f"Task {tid}",
            "description": "",
            "activeForm": "",
            "status": "in_progress",
            "created_at": time.time(),
            "updated_at": time.time(),
            "session_id": manager.session_id,
            "owner": None,
            "blockedBy": [],
            "blocks": [],
            "metadata": {},
            "tool_outputs": [],
        }
    manager.atomic_update_tasks(updater)


# === Core regression: override actions MUST reach AI on every block ===

def _simulate_posttooluse_delivery(session_id: str, store) -> None:
    """Simulate the PostToolUse handler that consumes pending_stop_injection.

    Mirrors `deliver_pending_stop_injection` (task_lifecycle.py:1818-1851):
    reads the pending injection then clears it. Without this clearing step,
    pending_stop_injection appears "still set" across consecutive Stop events
    only because nothing delivered the first one.
    """
    key = f"{session_id}:pending_stop_injection"
    if store.get(key):
        store.set(key, None)


@pytest.mark.parametrize("cli_type", ["claude", "gemini"])
@pytest.mark.parametrize("block_count", [1, 2, 3, 5, 10])
@pytest.mark.parametrize("ghost_enabled", [True, False])
@pytest.mark.parametrize("change_tasks", [False, True])
def test_override_visible_in_pending_injection_every_block(
    cli_type, block_count, ghost_enabled, change_tasks,
    isolated_task_config, isolated_session, tmp_path,
):
    """For any (cli_type, block_count, ghost_enabled, change_tasks),
    after `block_count` Stop events — each one followed by a simulated
    PostToolUse delivery that clears pending_stop_injection — the LAST
    Stop must again populate pending_stop_injection so the AI can see
    the override actions on its next PostToolUse.

    `change_tasks=True` resets the consecutive-identical counter every
    block (simulates the AI churning task lists between stops — the
    original failure path where pre-fix code never re-armed
    pending_stop_injection after block_count > 1).
    """
    cfg = TaskLifecycleConfig(
        enabled=True,
        storage_dir=tmp_path / "task_lifecycle",
        task_ttl_days=30,
        max_resume_tasks=10,
        ghost_clear_enabled=ghost_enabled,
        ghost_clear_min_consecutive_blocks=2,
    )
    sid = f"stop-override-{cli_type}-{block_count}-{int(ghost_enabled)}-{int(change_tasks)}-{time.time_ns()}"
    manager = TaskLifecycle(session_id=sid, config=cfg)
    _seed_tasks(manager, 2, prefix="seed")

    store = ThreadSafeDB()
    last_response = None
    last_ctx = None

    for i in range(block_count):
        if change_tasks and i > 0:
            _add_one_task(manager, f"churn-{i}")
        ctx = _make_stop_ctx(sid, cli_type=cli_type, store=store)
        last_response = manager.handle_stop(ctx)
        last_ctx = ctx
        # Realistic flow: between Stop events the AI executes tools, the
        # PostToolUse handler delivers and CLEARS pending_stop_injection.
        # Skip clearing on the final iteration so the assertion sees the
        # value the last Stop produced.
        if i < block_count - 1:
            _simulate_posttooluse_delivery(sid, store)

    assert last_response is not None, "handle_stop must block while tasks remain"
    assert last_response.get("continue") is True

    sys_msg = last_response.get("systemMessage", "")
    reason = last_response.get("reason", "")

    assert OVERRIDE_SOS in sys_msg or OVERRIDE_SOS in reason, (
        f"[{cli_type} block#{block_count} ghost={ghost_enabled} churn={change_tasks}] "
        f"systemMessage must contain '{OVERRIDE_SOS}'. Got systemMessage[:200]={sys_msg[:200]!r}"
    )
    assert OVERRIDE_TASK_IGNORE in sys_msg or OVERRIDE_TASK_IGNORE in reason, (
        f"[{cli_type} block#{block_count} ghost={ghost_enabled} churn={change_tasks}] "
        f"systemMessage must contain '{OVERRIDE_TASK_IGNORE}'. Got systemMessage[:200]={sys_msg[:200]!r}"
    )

    pending = last_ctx.pending_stop_injection
    assert pending is not None, (
        f"[{cli_type} block#{block_count} ghost={ghost_enabled} churn={change_tasks}] "
        f"ctx.pending_stop_injection must be set so AI sees override on next PostToolUse. "
        f"This is the 'infinite non-overridable stop failure' bug — when this is None, "
        f"the AI keeps Stopping without ever learning how to override."
    )
    assert OVERRIDE_SOS in pending, (
        f"[{cli_type} block#{block_count} ghost={ghost_enabled} churn={change_tasks}] "
        f"pending_stop_injection must contain '{OVERRIDE_SOS}'. Got: {pending[:200]!r}"
    )
    assert OVERRIDE_TASK_IGNORE in pending, (
        f"[{cli_type} block#{block_count} ghost={ghost_enabled} churn={change_tasks}] "
        f"pending_stop_injection must contain '{OVERRIDE_TASK_IGNORE}'. Got: {pending[:200]!r}"
    )


# === Codex parity (forward-compatible — must not regress when Codex is added) ===

@pytest.mark.parametrize("block_count", [1, 3, 5])
def test_override_visible_for_codex_cli_type(
    block_count, isolated_task_config, isolated_session, tmp_path,
):
    """Codex uses Claude-equivalent strict hook schema. Same override
    visibility invariant must hold once Codex platform is registered (C3).
    Test skips if Codex not yet a known cli_type (pre-C3) — becomes active
    automatically after C3 ships.
    """
    try:
        from autorun.config import detect_cli_type
        known = detect_cli_type({"cli_type": "codex"})
        if known != "codex":
            pytest.skip("Codex platform not yet registered (pre-C3)")
    except Exception:
        pytest.skip("Codex platform not yet registered (pre-C3)")

    cfg = TaskLifecycleConfig(
        enabled=True,
        storage_dir=tmp_path / "task_lifecycle",
        max_resume_tasks=10,
        ghost_clear_enabled=True,
        ghost_clear_min_consecutive_blocks=2,
    )
    sid = f"stop-codex-{block_count}-{time.time_ns()}"
    manager = TaskLifecycle(session_id=sid, config=cfg)
    _seed_tasks(manager, 2, prefix="seed")

    store = ThreadSafeDB()
    last_response = None
    last_ctx = None
    for _ in range(block_count):
        ctx = _make_stop_ctx(sid, cli_type="codex", store=store)
        last_response = manager.handle_stop(ctx)
        last_ctx = ctx

    sys_msg = last_response.get("systemMessage", "")
    pending = last_ctx.pending_stop_injection or ""
    assert OVERRIDE_SOS in sys_msg
    assert OVERRIDE_TASK_IGNORE in sys_msg
    assert OVERRIDE_SOS in pending
    assert OVERRIDE_TASK_IGNORE in pending


# === Pinpoint the original failing case (block 2+ with churning tasks) ===

def test_pending_injection_set_when_consecutive_below_min_threshold(
    isolated_task_config, isolated_session, tmp_path,
):
    """Pre-fix: pending_stop_injection was None when:
       - block_count > 1 AND consecutive < min_consecutive

    This is the exact "no matter how long the errors proceed for" path —
    AI keeps stopping with churning tasks (consecutive=1 every time) and
    never sees min_consecutive trigger.
    """
    cfg = TaskLifecycleConfig(
        enabled=True,
        storage_dir=tmp_path / "task_lifecycle",
        max_resume_tasks=10,
        ghost_clear_enabled=True,
        ghost_clear_min_consecutive_blocks=2,
    )
    sid = f"stop-churn-{time.time_ns()}"
    manager = TaskLifecycle(session_id=sid, config=cfg)
    _seed_tasks(manager, 2, prefix="seed")

    store = ThreadSafeDB()
    # First Stop: block_count=1, consecutive=1 — original code DID set pending.
    ctx1 = _make_stop_ctx(sid, cli_type="claude", store=store)
    manager.handle_stop(ctx1)
    assert ctx1.pending_stop_injection is not None  # known-good before fix

    # Second Stop with changed tasks: block_count=2, consecutive=1 again
    # because id-hash differs. Pre-fix this returned without setting pending.
    _add_one_task(manager, "churn-A")
    ctx2 = _make_stop_ctx(sid, cli_type="claude", store=store)
    manager.handle_stop(ctx2)
    assert ctx2.pending_stop_injection is not None, (
        "block_count=2 consecutive=1 case is the original failure. "
        "AI must still get override delivery."
    )
    assert OVERRIDE_SOS in ctx2.pending_stop_injection
    assert OVERRIDE_TASK_IGNORE in ctx2.pending_stop_injection

    # Third Stop with another new task: block_count=3, consecutive=1
    _add_one_task(manager, "churn-B")
    ctx3 = _make_stop_ctx(sid, cli_type="claude", store=store)
    manager.handle_stop(ctx3)
    assert ctx3.pending_stop_injection is not None
    assert OVERRIDE_SOS in ctx3.pending_stop_injection
    assert OVERRIDE_TASK_IGNORE in ctx3.pending_stop_injection


# === Sanity: no regression of base injection content ===

def test_base_injection_includes_task_subject_and_count(
    isolated_task_config, isolated_session, tmp_path,
):
    cfg = isolated_task_config
    sid = f"stop-base-{time.time_ns()}"
    manager = TaskLifecycle(session_id=sid, config=cfg)
    _seed_tasks(manager, 3, prefix="seed")

    ctx = _make_stop_ctx(sid, cli_type="claude")
    resp = manager.handle_stop(ctx)
    sys_msg = resp.get("systemMessage", "")
    assert "CANNOT STOP" in sys_msg
    assert "Task seed0" in sys_msg
    assert OVERRIDE_SOS in sys_msg
    assert OVERRIDE_TASK_IGNORE in sys_msg
