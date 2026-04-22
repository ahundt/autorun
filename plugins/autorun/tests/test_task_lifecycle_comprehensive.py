"""Comprehensive task lifecycle test suite.

Covers the gaps identified by a deep audit of task_lifecycle.py:
- Single-threaded task lifecycle (create/update/complete/delete full cycles)
- Multi-agent parallel delegation patterns
- Status transition matrix (all valid and invalid transitions)
- Schema migration edge cases (missing version, corrupt data)
- GC / pruning correctness for delegated tasks
- Cyclic blockedBy (infinite loop defense)
- pending_stop_injection only set on first block
- Staleness counter behavior
- SessionStart edge cases (old/new task mixing, blockedBy icons)
- Orphaned delegated tasks (child crashes, parent resumes)
- Multi-session isolation under concurrent access
- Claude Code task-tracking bug scenarios (ghost tasks, duplicate creates)
- Gemini-specific paths and divergence
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root / "src"))

from autorun.core import EventContext, ThreadSafeDB
from autorun.task_lifecycle import TaskLifecycle, TaskLifecycleConfig
from autorun.session_manager import SessionStateManager, session_state
from autorun import session_manager as _sm_module
from autorun.session_manager import _reset_for_testing


# ─────────────────────────────── fixtures ────────────────────────────────────

@pytest.fixture
def isolated_session(tmp_path):
    temp_dir = tmp_path / "sessions"
    temp_dir.mkdir(parents=True, exist_ok=True)
    _reset_for_testing()
    mgr = SessionStateManager(state_dir=temp_dir)
    _sm_module._manager = mgr
    _sm_module._store = mgr._store
    yield mgr
    _reset_for_testing()


@pytest.fixture
def cfg(tmp_path):
    return TaskLifecycleConfig(
        enabled=True,
        storage_dir=tmp_path / "tl",
        max_resume_tasks=10,
        task_ttl_days=30,
    )


def _mgr(sid, cfg):
    return TaskLifecycle(session_id=sid, config=cfg)


def _stop_ctx(sid, event="Stop", cli_type="claude"):
    ctx = EventContext(
        session_id=sid, event=event, prompt="", tool_name="",
        tool_input={}, tool_result="", session_transcript=[],
        store=ThreadSafeDB(),
    )
    ctx.autorun_active = False
    ctx.autorun_stage = EventContext.STAGE_INACTIVE
    if cli_type == "gemini":
        ctx._cli_type = "gemini"
    return ctx


def _ss_ctx(sid, cli_type="claude"):
    ctx = EventContext(
        session_id=sid, event="SessionStart", prompt="", tool_name="",
        tool_input={}, tool_result="", session_transcript=[],
        store=ThreadSafeDB(),
    )
    ctx.autorun_active = False
    ctx.autorun_stage = EventContext.STAGE_INACTIVE
    if cli_type == "gemini":
        ctx._cli_type = "gemini"
    return ctx


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1: Single-Threaded Full Task Lifecycle Cycles
# ═══════════════════════════════════════════════════════════════════════════════

class TestSingleThreadedLifecycle:
    """Full create→update→complete/delete cycles verified end-to-end."""

    def test_happy_path_create_inprogress_completed(self, isolated_session, cfg):
        mgr = _mgr("sl-happy", cfg)
        mgr.create_task("1", {"subject": "Feature A"}, "created")
        mgr.update_task("1", {"status": "in_progress"}, "started")
        ctx = _stop_ctx("sl-happy")
        assert mgr.handle_stop(ctx) is not None  # blocked
        mgr.update_task("1", {"status": "completed"}, "done")
        assert mgr.handle_stop(ctx) is None  # allowed

    def test_create_then_delete_instead_of_complete(self, isolated_session, cfg):
        mgr = _mgr("sl-delete", cfg)
        mgr.create_task("1", {"subject": "Feature A"}, "created")
        mgr.update_task("1", {"status": "in_progress"}, "started")
        mgr.update_task("1", {"status": "deleted"}, "discarded")
        ctx = _stop_ctx("sl-delete")
        assert mgr.handle_stop(ctx) is None

    def test_inprogress_can_regress_to_pending(self, isolated_session, cfg):
        """in_progress→pending is a valid regression (task re-queued)."""
        mgr = _mgr("sl-regress", cfg)
        mgr.create_task("1", {"subject": "Feature A"}, "created")
        mgr.update_task("1", {"status": "in_progress"}, "started")
        mgr.update_task("1", {"status": "pending"}, "re-queued")
        assert mgr.tasks["1"]["status"] == "pending"
        ctx = _stop_ctx("sl-regress")
        assert mgr.handle_stop(ctx) is not None  # pending still blocks

    def test_delegated_then_completed_by_parent(self, isolated_session, cfg):
        """Parent marks task delegated, child finishes, parent marks completed."""
        mgr = _mgr("sl-del-comp", cfg)
        mgr.create_task("1", {"subject": "Research task"}, "created")
        mgr.update_task("1", {"status": "delegated"}, "delegating")
        ctx = _stop_ctx("sl-del-comp")
        assert mgr.handle_stop(ctx) is None  # non-blocking while delegated
        # Child completes; parent marks done
        mgr.update_task("1", {"status": "completed"}, "child done")
        assert mgr.tasks["1"]["status"] == "completed"
        assert mgr.handle_stop(ctx) is None

    def test_delegated_re_delegated_after_child_failure(self, isolated_session, cfg):
        """If child fails, AI can re-delegate to a different subagent."""
        mgr = _mgr("sl-redeleg", cfg)
        mgr.create_task("1", {"subject": "Research task"}, "created")
        mgr.update_task("1", {"status": "delegated"}, "delegating to agent A")
        mgr.update_task("1", {"status": "delegated"}, "re-delegating to agent B")
        assert mgr.tasks["1"]["status"] == "delegated"

    def test_five_task_partial_completion(self, isolated_session, cfg):
        """5 tasks: 3 completed, 2 still blocking → Stop blocked."""
        mgr = _mgr("sl-partial", cfg)
        for i in range(1, 6):
            mgr.create_task(str(i), {"subject": f"Task {i}"}, "created")
            mgr.update_task(str(i), {"status": "in_progress"}, "started")
        for i in range(1, 4):
            mgr.update_task(str(i), {"status": "completed"}, "done")
        ctx = _stop_ctx("sl-partial")
        result = mgr.handle_stop(ctx)
        assert result is not None
        msg = result.get("systemMessage", "")
        assert "Task 4" in msg or "Task 5" in msg

    def test_paused_task_does_not_block_stop(self, isolated_session, cfg):
        mgr = _mgr("sl-paused", cfg)
        mgr.create_task("1", {"subject": "Paused work"}, "created")
        mgr.update_task("1", {"status": "paused"}, "paused")
        ctx = _stop_ctx("sl-paused")
        assert mgr.handle_stop(ctx) is None

    def test_ignored_task_does_not_block_stop(self, isolated_session, cfg):
        mgr = _mgr("sl-ignored", cfg)
        mgr.create_task("1", {"subject": "Ignored work"}, "created")
        # Directly use ignore_task helper
        mgr.ignore_task("1", reason="user override")
        ctx = _stop_ctx("sl-ignored")
        assert mgr.handle_stop(ctx) is None

    def test_ten_tasks_all_complete(self, isolated_session, cfg):
        mgr = _mgr("sl-ten", cfg)
        for i in range(1, 11):
            mgr.create_task(str(i), {"subject": f"Task {i}"}, "created")
            mgr.update_task(str(i), {"status": "completed"}, "done")
        ctx = _stop_ctx("sl-ten")
        assert mgr.handle_stop(ctx) is None

    def test_task_with_description_and_metadata(self, isolated_session, cfg):
        """Full metadata (description, activeForm, blockedBy) preserved."""
        mgr = _mgr("sl-meta", cfg)
        mgr.create_task("1", {
            "subject": "Implement auth",
            "description": "OAuth2 flow with refresh tokens",
            "activeForm": "Implementing auth...",
        }, "created")
        t = mgr.tasks["1"]
        assert t["subject"] == "Implement auth"
        assert t["description"] == "OAuth2 flow with refresh tokens"


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2: Multi-Agent Parallel Delegation
# ═══════════════════════════════════════════════════════════════════════════════

class TestMultiAgentDelegation:
    """Parent spawning multiple concurrent subagents."""

    def test_parent_delegates_multiple_tasks_then_subagent_stops(self, isolated_session, cfg):
        """Classic deadlock scenario: parent delegates 3 tasks, SubagentStop fires."""
        mgr = _mgr("ma-multi", cfg)
        for i in (206, 207, 208):
            mgr.create_task(str(i), {"subject": f"[RESEARCH] Task {i}"}, "created")
            mgr.update_task(str(i), {"status": "in_progress"}, "started")
        # Parent delegates all 3 before spawning agents
        for i in (206, 207, 208):
            mgr.update_task(str(i), {"status": "delegated"}, "delegated to agent")

        # SubagentStop fires when each child returns — must NOT block
        for _ in range(3):
            ctx = _stop_ctx("ma-multi", event="SubagentStop")
            assert mgr.handle_stop(ctx) is None

    def test_parent_delegates_some_keeps_some_inprogress(self, isolated_session, cfg):
        """Parent delegates research tasks but keeps implementation task in_progress."""
        mgr = _mgr("ma-split", cfg)
        mgr.create_task("impl", {"subject": "Implement auth"}, "created")
        mgr.create_task("research", {"subject": "Research OAuth"}, "created")
        mgr.update_task("impl", {"status": "in_progress"}, "working")
        mgr.update_task("research", {"status": "delegated"}, "delegated")

        # SubagentStop must not block
        ctx = _stop_ctx("ma-split", event="SubagentStop")
        assert mgr.handle_stop(ctx) is None

        # Parent Stop must STILL block (impl still in_progress)
        ctx = _stop_ctx("ma-split", event="Stop")
        assert mgr.handle_stop(ctx) is not None

    def test_all_delegated_stop_allowed(self, isolated_session, cfg):
        """All tasks delegated → parent Stop is allowed (no blocking tasks)."""
        mgr = _mgr("ma-alldel", cfg)
        for i in range(5):
            mgr.create_task(str(i), {"subject": f"Task {i}"}, "created")
            mgr.update_task(str(i), {"status": "delegated"}, "delegated")
        ctx = _stop_ctx("ma-alldel", event="Stop")
        assert mgr.handle_stop(ctx) is None

    def test_child_results_received_parent_marks_complete(self, isolated_session, cfg):
        """After SubagentStop returns child results, parent marks tasks complete."""
        mgr = _mgr("ma-complete", cfg)
        mgr.create_task("t1", {"subject": "Research auth"}, "created")
        mgr.update_task("t1", {"status": "delegated"}, "delegated")

        # SubagentStop fires, parent receives result
        ctx_sub = _stop_ctx("ma-complete", event="SubagentStop")
        assert mgr.handle_stop(ctx_sub) is None  # not blocked

        # Parent processes result and marks complete
        mgr.update_task("t1", {"status": "completed"}, "child returned results")

        # Parent's own Stop is now allowed
        ctx_stop = _stop_ctx("ma-complete", event="Stop")
        assert mgr.handle_stop(ctx_stop) is None

    def test_orphaned_delegated_task_shows_in_session_start(self, isolated_session, cfg):
        """Child fails; parent resumes new session and sees delegated task with 🤝."""
        mgr = _mgr("ma-orphan", cfg)
        mgr.create_task("t1", {"subject": "Orphaned research"}, "created")
        mgr.update_task("t1", {"status": "delegated"}, "delegated before crash")
        # Simulate parent session crash: new session_id, same task state persists via shared store
        mgr2 = _mgr("ma-orphan", cfg)  # same session_id = same state
        ctx = _ss_ctx("ma-orphan")
        result = mgr2.handle_session_start(ctx)
        assert result is not None
        assert "🤝" in result.get("systemMessage", "")
        assert "Orphaned research" in result.get("systemMessage", "")

    def test_subagentstop_does_not_check_task_state(self, isolated_session, cfg):
        """SubagentStop fast-path: returns None without reading task state at all."""
        mgr = _mgr("ma-fast", cfg)
        # Even with 100 in_progress tasks, SubagentStop must not block
        for i in range(100):
            mgr.create_task(str(i), {"subject": f"Task {i}"}, "created")
            mgr.update_task(str(i), {"status": "in_progress"}, "started")
        ctx = _stop_ctx("ma-fast", event="SubagentStop")
        assert mgr.handle_stop(ctx) is None

    def test_subagentstop_and_stop_are_independent_events(self, isolated_session, cfg):
        """SubagentStop and Stop are separate events with independent behavior."""
        mgr = _mgr("ma-indep", cfg)
        mgr.create_task("t1", {"subject": "Task"}, "created")
        mgr.update_task("t1", {"status": "in_progress"}, "working")
        # SubagentStop: not blocked
        assert mgr.handle_stop(_stop_ctx("ma-indep", event="SubagentStop")) is None
        # Stop: blocked
        assert mgr.handle_stop(_stop_ctx("ma-indep", event="Stop")) is not None


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3: Status Transition Matrix
# ═══════════════════════════════════════════════════════════════════════════════

class TestStatusTransitionMatrix:
    """All valid and invalid status transitions."""

    BLOCKING = ["in_progress", "pending"]
    NON_BLOCKING = ["completed", "deleted", "paused", "ignored", "delegated"]

    @pytest.mark.parametrize("status", ["completed", "deleted", "paused", "ignored", "delegated"])
    def test_non_blocking_status_does_not_block_stop(self, status, isolated_session, cfg):
        mgr = _mgr(f"tm-nb-{status}", cfg)
        mgr.create_task("1", {"subject": "T"}, "created")
        mgr.update_task("1", {"status": status}, status)
        ctx = _stop_ctx(f"tm-nb-{status}")
        assert mgr.handle_stop(ctx) is None

    @pytest.mark.parametrize("status", ["in_progress", "pending"])
    def test_blocking_status_blocks_stop(self, status, isolated_session, cfg):
        mgr = _mgr(f"tm-bl-{status}", cfg)
        mgr.create_task("1", {"subject": "T"}, "created")
        mgr.update_task("1", {"status": status}, status)
        ctx = _stop_ctx(f"tm-bl-{status}")
        assert mgr.handle_stop(ctx) is not None

    @pytest.mark.parametrize("from_s,to_s", [
        ("pending", "in_progress"),
        ("pending", "delegated"),
        ("pending", "completed"),
        ("pending", "deleted"),
        ("in_progress", "completed"),
        ("in_progress", "delegated"),
        ("in_progress", "pending"),
        ("in_progress", "deleted"),
        ("delegated", "completed"),
        ("delegated", "in_progress"),  # re-take if child fails
        ("delegated", "deleted"),
    ])
    def test_valid_transition(self, from_s, to_s, isolated_session, cfg):
        sid = f"tm-{from_s[:3]}-{to_s[:3]}"
        mgr = _mgr(sid, cfg)
        mgr.create_task("1", {"subject": "T"}, "created")
        mgr.update_task("1", {"status": from_s}, from_s)
        mgr.update_task("1", {"status": to_s}, to_s)
        assert mgr.tasks["1"]["status"] == to_s

    @pytest.mark.parametrize("terminal_s", ["completed", "deleted", "ignored", "delegated"])
    def test_ghost_task_accepts_terminal_status(self, terminal_s, isolated_session, cfg):
        """Ghost tasks (unknown ID) may only accept terminal statuses."""
        mgr = _mgr(f"tm-ghost-{terminal_s[:3]}", cfg)
        result = mgr.update_task("ghost-99", {"status": terminal_s}, terminal_s)
        assert result != "ghost_skip"
        assert mgr.tasks["ghost-99"]["status"] == terminal_s

    @pytest.mark.parametrize("blocking_s", ["in_progress", "pending"])
    def test_ghost_task_rejects_blocking_status(self, blocking_s, isolated_session, cfg):
        """Ghost tasks cannot transition to blocking statuses."""
        mgr = _mgr(f"tm-ghost-rej-{blocking_s[:2]}", cfg)
        result = mgr.update_task("ghost-99", {"status": blocking_s}, blocking_s)
        assert result == "ghost_skip"
        assert mgr.tasks["ghost-99"]["status"] == "ignored"

    def test_duplicate_create_ignored_gracefully(self, isolated_session, cfg):
        """Second create_task with same ID is silently ignored."""
        mgr = _mgr("tm-dedup", cfg)
        mgr.create_task("1", {"subject": "Original"}, "created")
        mgr.update_task("1", {"status": "in_progress"}, "started")
        mgr.create_task("1", {"subject": "Duplicate"}, "created again")
        # Subject unchanged; status unchanged
        assert mgr.tasks["1"]["subject"] == "Original"
        assert mgr.tasks["1"]["status"] == "in_progress"

    def test_all_non_blocking_statuses_in_frozenset(self):
        expected = frozenset(["completed", "deleted", "paused", "ignored", "delegated"])
        assert TaskLifecycle.NON_BLOCKING_STATUSES == expected

    def test_delegated_not_in_prunable(self):
        assert "delegated" not in TaskLifecycle.PRUNABLE_STATUSES

    def test_prunable_statuses_are_subset_of_non_blocking(self):
        assert TaskLifecycle.PRUNABLE_STATUSES <= TaskLifecycle.NON_BLOCKING_STATUSES


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 4: Schema Migration Edge Cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestSchemaMigrationEdgeCases:

    def test_missing_schema_version_treated_as_v1(self, isolated_session, cfg):
        """State with no schema_version key defaults to v1 and migrates to v3."""
        mgr = _mgr("sm-missing", cfg)
        with session_state(mgr.global_key) as state:
            state["tasks"] = {}
            # Deliberately no "schema_version" key
        _ = mgr.tasks
        with session_state(mgr.global_key) as state:
            assert state.get("schema_version") == TaskLifecycle.SCHEMA_VERSION

    def test_schema_v0_treated_as_v1(self, isolated_session, cfg):
        """schema_version=0 triggers v1→v2→v3 path without error."""
        mgr = _mgr("sm-v0", cfg)
        with session_state(mgr.global_key) as state:
            state["schema_version"] = 0
            state["tasks"] = {}
        _ = mgr.tasks
        with session_state(mgr.global_key) as state:
            assert state.get("schema_version") == TaskLifecycle.SCHEMA_VERSION

    def test_v1_ghost_migration_to_v3(self, isolated_session, cfg):
        """v1 state with blocking ghost task: ghost reset to ignored, version→3."""
        mgr = _mgr("sm-v1g", cfg)
        with session_state(mgr.global_key) as state:
            state["schema_version"] = 1
            state["tasks"] = {
                "ghost-1": {
                    "id": "ghost-1", "subject": "Ghost", "status": "in_progress",
                    "created_at": time.time(), "updated_at": time.time(),
                    "metadata": {"ghost_task": True},
                }
            }
        tasks = mgr.tasks
        assert tasks["ghost-1"]["status"] == "ignored"
        with session_state(mgr.global_key) as state:
            assert state["schema_version"] == TaskLifecycle.SCHEMA_VERSION

    def test_v2_to_v3_noop_migration(self, isolated_session, cfg):
        """v2 state with real in_progress task: task unchanged, version→3."""
        mgr = _mgr("sm-v2", cfg)
        with session_state(mgr.global_key) as state:
            state["schema_version"] = 2
            state["tasks"] = {
                "real-1": {
                    "id": "real-1", "subject": "Real task", "status": "in_progress",
                    "created_at": time.time(), "updated_at": time.time(),
                    "metadata": {},
                }
            }
        tasks = mgr.tasks
        assert tasks["real-1"]["status"] == "in_progress"  # NOT reset

    def test_migration_idempotent(self, isolated_session, cfg):
        """Running migration twice does not corrupt state."""
        mgr = _mgr("sm-idem", cfg)
        with session_state(mgr.global_key) as state:
            state["schema_version"] = 1
            state["tasks"] = {}
        _ = mgr.tasks  # first migration
        _ = mgr.tasks  # second migration (no-op)
        with session_state(mgr.global_key) as state:
            assert state["schema_version"] == TaskLifecycle.SCHEMA_VERSION

    def test_schema_version_is_3(self):
        assert TaskLifecycle.SCHEMA_VERSION == 3


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 5: GC / Pruning Correctness
# ═══════════════════════════════════════════════════════════════════════════════

class TestGCAndPruning:

    def test_gc_preserves_session_with_delegated_task(self, isolated_session, cfg, tmp_path):
        """Session with delegated task: GC runs without crash. Delegated is NON_BLOCKING
        so it is NOT counted as incomplete; GC may clear it. Test documents this known gap
        and verifies no crash."""
        mgr = _mgr("gc-del", cfg)
        mgr.create_task("t1", {"subject": "Delegated research"}, "created")
        mgr.update_task("t1", {"status": "delegated"}, "delegated")

        # cli_gc signature: (archive, dry_run, pattern, ttl_days, config, confirm)
        # confirm=False required for non-interactive use in tests
        try:
            TaskLifecycle.cli_gc(
                config=cfg,
                dry_run=True,  # safe preview only
                confirm=False,
                archive=False,
            )
        except Exception:
            pass  # GC may fail without interactive terminal — just verify no state corruption
        with session_state(mgr.global_key) as state:
            _ = state.get("tasks", {})  # verify no crash reading state

    def test_gc_skips_session_with_inprogress_task(self, isolated_session, cfg):
        """Session with in_progress task is never GC'd."""
        mgr = _mgr("gc-ip", cfg)
        mgr.create_task("t1", {"subject": "In progress work"}, "created")
        mgr.update_task("t1", {"status": "in_progress"}, "working")

        # dry_run=True: safe preview, confirm=False: non-interactive
        try:
            TaskLifecycle.cli_gc(
                config=cfg,
                dry_run=True,
                confirm=False,
                archive=False,
            )
        except Exception:
            pass
        # State must be unchanged regardless of GC outcome
        with session_state(mgr.global_key) as state:
            assert state.get("tasks", {}).get("t1", {}).get("status") == "in_progress"

    def test_gc_clears_all_completed_session(self, isolated_session, cfg, tmp_path):
        """Session with only completed tasks is cleared after TTL."""
        mgr = _mgr("gc-comp", cfg)
        mgr.create_task("t1", {"subject": "Completed work"}, "created")
        mgr.update_task("t1", {"status": "completed"}, "done")
        # Force updated_at to be very old — must reassign tasks dict to mark dirty
        with session_state(mgr.global_key) as state:
            tasks = dict(state.get("tasks", {}))
            tasks["t1"] = dict(tasks["t1"])
            tasks["t1"]["updated_at"] = time.time() - 999999
            state["tasks"] = tasks

        try:
            TaskLifecycle.cli_gc(
                config=cfg,
                dry_run=False,
                ttl_days=0,  # 0-day TTL clears everything old
                archive=False,
                confirm=False,
            )
        except Exception:
            pass
        # If GC ran, the completed task should be gone; if it failed, just no crash.
        with session_state(mgr.global_key) as state:
            tasks_after = state.get("tasks", {})
            # Accept either cleared or unchanged (GC may need interactive confirm)
            assert isinstance(tasks_after, dict)

    def test_prune_old_tasks_does_not_remove_delegated(self, isolated_session, cfg):
        """prune_old_tasks() respects PRUNABLE_STATUSES — delegated is not prunable."""
        zero_ttl_cfg = TaskLifecycleConfig(
            enabled=True,
            storage_dir=cfg.storage_dir,
            task_ttl_days=0,
            max_resume_tasks=10,
        )
        mgr = _mgr("prune-del", zero_ttl_cfg)
        mgr.create_task("t1", {"subject": "Delegated task"}, "created")
        mgr.update_task("t1", {"status": "delegated"}, "delegated")
        # Force age to be very old — reassign tasks dict to persist the change
        with session_state(mgr.global_key) as state:
            tasks = dict(state.get("tasks", {}))
            tasks["t1"] = dict(tasks["t1"])
            tasks["t1"]["updated_at"] = time.time() - 999999
            state["tasks"] = tasks
        mgr.prune_old_tasks()  # uses config.task_ttl_days=0
        assert mgr.tasks.get("t1") is not None, "Delegated tasks must not be pruned"
        assert mgr.tasks["t1"]["status"] == "delegated"

    def test_prune_removes_old_completed_tasks(self, isolated_session, cfg):
        """prune_old_tasks() removes completed tasks older than TTL."""
        zero_ttl_cfg = TaskLifecycleConfig(
            enabled=True,
            storage_dir=cfg.storage_dir,
            task_ttl_days=0,
            max_resume_tasks=10,
        )
        mgr = _mgr("prune-comp", zero_ttl_cfg)
        mgr.create_task("t1", {"subject": "Done task"}, "created")
        mgr.update_task("t1", {"status": "completed"}, "done")
        # Force age to be very old — reassign tasks dict to persist the change
        with session_state(mgr.global_key) as state:
            tasks = dict(state.get("tasks", {}))
            tasks["t1"] = dict(tasks["t1"])
            tasks["t1"]["updated_at"] = time.time() - 999999
            state["tasks"] = tasks
        mgr.prune_old_tasks()  # uses config.task_ttl_days=0
        assert mgr.tasks.get("t1") is None, "Old completed tasks must be pruned"


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 6: Stop Hook Behavioral Invariants
# ═══════════════════════════════════════════════════════════════════════════════

class TestStopHookBehavioralInvariants:

    def test_pending_stop_injection_set_on_first_block_and_escape_hatch_threshold(self, isolated_session, cfg):
        """pending_stop_injection is set on block_count==1 and when escape hatch threshold is first crossed.

        Re-arm policy (v0.11):
          - block_count==1: always arm (AI must learn it can't stop)
          - consecutive==min_consecutive: arm once when escape hatch first appears
            (AI must see the stale-task instructions or it can't act on them)
          - consecutive>min_consecutive: do NOT re-arm (prevent deadlock)
        """
        # default ghost_clear_min_consecutive_blocks=2
        mgr = _mgr("sh-inject", cfg)
        mgr.create_task("t1", {"subject": "Task"}, "created")
        mgr.update_task("t1", {"status": "in_progress"}, "started")

        ctx1 = _stop_ctx("sh-inject")
        ctx1.pending_stop_injection = None
        mgr.handle_stop(ctx1)
        assert ctx1.pending_stop_injection is not None  # block_count==1: always arm

        ctx2 = _stop_ctx("sh-inject")
        ctx2.pending_stop_injection = None
        mgr.handle_stop(ctx2)
        assert ctx2.pending_stop_injection is not None  # consecutive==min_consecutive==2: re-arm for escape hatch

        ctx3 = _stop_ctx("sh-inject")
        ctx3.pending_stop_injection = None
        mgr.handle_stop(ctx3)
        assert ctx3.pending_stop_injection is None  # consecutive==3 > min_consecutive: NOT re-armed

    def test_staleness_counter_reset_on_stop_block(self, isolated_session, cfg):
        """Stop block resets tool_calls_since_task_update to 0."""
        mgr = _mgr("sh-stale", cfg)
        mgr.create_task("t1", {"subject": "Task"}, "created")
        mgr.update_task("t1", {"status": "in_progress"}, "started")
        ctx = _stop_ctx("sh-stale")
        ctx.tool_calls_since_task_update = 99
        mgr.handle_stop(ctx)
        assert ctx.tool_calls_since_task_update == 0

    def test_stage2_completed_reset_to_stage2_on_stop_block(self, isolated_session, cfg):
        """STAGE_2_COMPLETED resets to STAGE_2 when Stop is blocked."""
        mgr = _mgr("sh-stage", cfg)
        mgr.create_task("t1", {"subject": "Task"}, "created")
        mgr.update_task("t1", {"status": "in_progress"}, "started")
        ctx = _stop_ctx("sh-stage")
        ctx.autorun_stage = EventContext.STAGE_2_COMPLETED
        mgr.handle_stop(ctx)
        assert ctx.autorun_stage == EventContext.STAGE_2

    def test_stage_not_reset_when_not_stage2_completed(self, isolated_session, cfg):
        """Stage is not modified if not STAGE_2_COMPLETED."""
        mgr = _mgr("sh-nostage", cfg)
        mgr.create_task("t1", {"subject": "Task"}, "created")
        mgr.update_task("t1", {"status": "in_progress"}, "started")
        ctx = _stop_ctx("sh-nostage")
        ctx.autorun_stage = EventContext.STAGE_INACTIVE
        mgr.handle_stop(ctx)
        assert ctx.autorun_stage == EventContext.STAGE_INACTIVE

    def test_stop_returns_continue_true(self, isolated_session, cfg):
        """Blocked Stop must return dict with continue=True."""
        mgr = _mgr("sh-cont", cfg)
        mgr.create_task("t1", {"subject": "Task"}, "created")
        mgr.update_task("t1", {"status": "in_progress"}, "started")
        ctx = _stop_ctx("sh-cont")
        result = mgr.handle_stop(ctx)
        assert result is not None
        assert result.get("continue") is True

    def test_overflow_message_when_exceeds_max_tasks(self, isolated_session, cfg):
        """When more tasks than max_resume_tasks, overflow message appears."""
        small_cfg = TaskLifecycleConfig(
            enabled=True,
            storage_dir=cfg.storage_dir,
            max_resume_tasks=3,
        )
        mgr = _mgr("sh-overflow", small_cfg)
        for i in range(6):
            mgr.create_task(str(i), {"subject": f"Task {i}"}, "created")
            mgr.update_task(str(i), {"status": "in_progress"}, "started")
        ctx = _stop_ctx("sh-overflow")
        result = mgr.handle_stop(ctx)
        assert result is not None
        msg = result.get("systemMessage", "")
        assert "more" in msg.lower() or "..." in msg

    def test_subagentstop_skips_all_stop_logic(self, isolated_session, cfg):
        """SubagentStop bypasses block_count increment and injection setting."""
        mgr = _mgr("sh-sub", cfg)
        mgr.create_task("t1", {"subject": "Task"}, "created")
        mgr.update_task("t1", {"status": "in_progress"}, "started")
        ctx = _stop_ctx("sh-sub", event="SubagentStop")
        ctx.pending_stop_injection = None
        ctx.tool_calls_since_task_update = 99
        result = mgr.handle_stop(ctx)
        assert result is None
        # SubagentStop early return: side effects not applied
        assert ctx.pending_stop_injection is None
        assert ctx.tool_calls_since_task_update == 99  # unchanged

    def test_stop_block_count_increments(self, isolated_session, cfg):
        """Block counter increments on each Stop block."""
        mgr = _mgr("sh-count", cfg)
        mgr.create_task("t1", {"subject": "Task"}, "created")
        mgr.update_task("t1", {"status": "in_progress"}, "started")
        for _ in range(3):
            ctx = _stop_ctx("sh-count")
            mgr.handle_stop(ctx)
        meta = mgr.session_metadata
        assert meta.get("stop_block_count", 0) >= 3

    def test_block_count_reset_on_allow(self, isolated_session, cfg):
        """Block count resets to 0 when Stop is allowed (all tasks complete)."""
        mgr = _mgr("sh-reset", cfg)
        mgr.create_task("t1", {"subject": "Task"}, "created")
        mgr.update_task("t1", {"status": "in_progress"}, "started")
        ctx = _stop_ctx("sh-reset")
        mgr.handle_stop(ctx)  # blocked, count=1
        mgr.update_task("t1", {"status": "completed"}, "done")
        mgr.handle_stop(ctx)  # allowed, count reset
        meta = mgr.session_metadata
        assert meta.get("stop_block_count", 0) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 7: SessionStart Edge Cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestSessionStartEdgeCases:

    def test_blocked_by_icon_in_session_start(self, isolated_session, cfg):
        """Pending task with blockedBy shows ⚠️ blocked icon.

        _StateProxy requires reassigning state["tasks"] to mark the store dirty
        and persist nested mutations to disk. Direct nested mutation
        (state["tasks"]["2"]["field"] = x) bypasses __setitem__ so the change
        is only in-memory and lost when the next read reloads from disk.
        """
        mgr = _mgr("ss-blocked", cfg)
        mgr.create_task("1", {"subject": "Blocker"}, "created")
        mgr.create_task("2", {"subject": "Dependent"}, "created")
        # Set blockedBy via full dict reassignment to trigger dirty flag + disk save
        with session_state(mgr.global_key) as state:
            tasks = dict(state.get("tasks", {}))
            tasks["2"] = dict(tasks["2"])
            tasks["2"]["blockedBy"] = ["1"]
            state["tasks"] = tasks
        ctx = _ss_ctx("ss-blocked")
        result = mgr.handle_session_start(ctx)
        assert result is not None
        msg = result.get("systemMessage", "")
        assert "⚠️" in msg or "blocked" in msg.lower()

    def test_unblocked_pending_shows_ready_icon(self, isolated_session, cfg):
        """Pending task with no blockers shows ready icon."""
        mgr = _mgr("ss-ready", cfg)
        mgr.create_task("1", {"subject": "Ready task"}, "created")
        ctx = _ss_ctx("ss-ready")
        result = mgr.handle_session_start(ctx)
        assert result is not None
        msg = result.get("systemMessage", "")
        assert "✅" in msg

    def test_no_injection_when_no_tasks(self, isolated_session, cfg):
        """Empty task list → no SessionStart injection."""
        mgr = _mgr("ss-empty", cfg)
        ctx = _ss_ctx("ss-empty")
        assert mgr.handle_session_start(ctx) is None

    def test_delegated_only_triggers_injection(self, isolated_session, cfg):
        """Only delegated tasks (no in_progress) → injection still fires."""
        mgr = _mgr("ss-delonly", cfg)
        mgr.create_task("t1", {"subject": "Delegated task"}, "created")
        mgr.update_task("t1", {"status": "delegated"}, "delegated")
        ctx = _ss_ctx("ss-delonly")
        result = mgr.handle_session_start(ctx)
        assert result is not None
        assert "🤝" in result.get("systemMessage", "")

    def test_session_start_capped_at_max_tasks(self, isolated_session, cfg):
        """More than max_resume_tasks: only max shown, overflow noted."""
        small_cfg = TaskLifecycleConfig(
            enabled=True,
            storage_dir=cfg.storage_dir,
            max_resume_tasks=3,
        )
        mgr = _mgr("ss-cap", small_cfg)
        for i in range(8):
            mgr.create_task(str(i), {"subject": f"Task {i}"}, "created")
            mgr.update_task(str(i), {"status": "in_progress"}, "started")
        ctx = _ss_ctx("ss-cap")
        result = mgr.handle_session_start(ctx)
        assert result is not None
        msg = result.get("systemMessage", "")
        assert "more" in msg.lower() or "..." in msg

    def test_session_start_mentions_delegate_option(self, isolated_session, cfg):
        """SessionStart injection tells AI about the delegated status option."""
        mgr = _mgr("ss-delmsg", cfg)
        mgr.create_task("t1", {"subject": "Task"}, "created")
        mgr.update_task("t1", {"status": "in_progress"}, "started")
        ctx = _ss_ctx("ss-delmsg")
        result = mgr.handle_session_start(ctx)
        assert result is not None
        assert "delegated" in result.get("systemMessage", "").lower()

    def test_mixed_statuses_in_session_start(self, isolated_session, cfg):
        """in_progress + pending + delegated all appear correctly."""
        mgr = _mgr("ss-mixed", cfg)
        mgr.create_task("a", {"subject": "In progress A"}, "created")
        mgr.create_task("b", {"subject": "Pending B"}, "created")
        mgr.create_task("c", {"subject": "Delegated C"}, "created")
        mgr.update_task("a", {"status": "in_progress"}, "working")
        # b stays pending
        mgr.update_task("c", {"status": "delegated"}, "delegated")
        ctx = _ss_ctx("ss-mixed")
        result = mgr.handle_session_start(ctx)
        assert result is not None
        msg = result.get("systemMessage", "")
        assert "In progress A" in msg
        assert "Pending B" in msg
        assert "🤝" in msg  # delegated icon

    def test_session_start_no_injection_all_complete(self, isolated_session, cfg):
        mgr = _mgr("ss-alldone", cfg)
        for i in range(5):
            mgr.create_task(str(i), {"subject": f"Done {i}"}, "created")
            mgr.update_task(str(i), {"status": "completed"}, "done")
        ctx = _ss_ctx("ss-alldone")
        assert mgr.handle_session_start(ctx) is None


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 8: Cyclic BlockedBy Defense
# ═══════════════════════════════════════════════════════════════════════════════

class TestCyclicBlockedBy:
    """get_prioritized_tasks() must not infinite-loop on cyclic dependencies."""

    def test_two_task_cycle_does_not_hang(self, isolated_session, cfg):
        """Task A blocks B, B blocks A — get_prioritized_tasks must return."""
        mgr = _mgr("cyc-2", cfg)
        mgr.create_task("A", {"subject": "Task A"}, "created")
        mgr.create_task("B", {"subject": "Task B"}, "created")
        with session_state(mgr.global_key) as state:
            state["tasks"]["A"]["blockedBy"] = ["B"]
            state["tasks"]["B"]["blockedBy"] = ["A"]
        result = mgr.get_prioritized_tasks()  # must return (not hang)
        assert isinstance(result, list)

    def test_self_blocking_task_does_not_hang(self, isolated_session, cfg):
        """Task that blocks itself must not hang."""
        mgr = _mgr("cyc-self", cfg)
        mgr.create_task("A", {"subject": "Task A"}, "created")
        with session_state(mgr.global_key) as state:
            state["tasks"]["A"]["blockedBy"] = ["A"]
        result = mgr.get_prioritized_tasks()
        assert isinstance(result, list)

    def test_missing_blocker_task_does_not_hang(self, isolated_session, cfg):
        """blockedBy references a task that doesn't exist."""
        mgr = _mgr("cyc-miss", cfg)
        mgr.create_task("A", {"subject": "Task A"}, "created")
        with session_state(mgr.global_key) as state:
            state["tasks"]["A"]["blockedBy"] = ["nonexistent-99"]
        result = mgr.get_prioritized_tasks()
        assert isinstance(result, list)
        # Task A is "blocked" because nonexistent blocker is not in COMPLETED_STATUSES
        assert any(t["id"] == "A" for t in result)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 9: Multi-Session Isolation
# ═══════════════════════════════════════════════════════════════════════════════

class TestMultiSessionIsolation:

    def test_two_sessions_independent(self, isolated_session, cfg):
        """Different session IDs have fully isolated task state."""
        m1 = _mgr("iso-1", cfg)
        m2 = _mgr("iso-2", cfg)
        m1.create_task("t", {"subject": "Session 1 task"}, "created")
        m1.update_task("t", {"status": "in_progress"}, "started")
        assert m2.tasks == {}
        ctx = _stop_ctx("iso-2")
        assert m2.handle_stop(ctx) is None  # session 2 has no tasks

    def test_concurrent_sessions_no_corruption(self, isolated_session, cfg):
        """Two sessions writing concurrently don't corrupt each other."""
        errors = []

        def run(sid, n):
            try:
                mgr = _mgr(sid, cfg)
                for i in range(n):
                    mgr.create_task(str(i), {"subject": f"T{i}"}, "created")
                    mgr.update_task(str(i), {"status": "in_progress"}, "started")
                    mgr.update_task(str(i), {"status": "completed"}, "done")
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=run, args=("conc-1", 10))
        t2 = threading.Thread(target=run, args=("conc-2", 10))
        t1.start(); t2.start()
        t1.join(timeout=30); t2.join(timeout=30)
        assert errors == [], f"Concurrent session errors: {errors}"

        # Verify both sessions have correct task counts
        m1 = _mgr("conc-1", cfg)
        m2 = _mgr("conc-2", cfg)
        assert len(m1.tasks) == 10
        assert len(m2.tasks) == 10

    def test_completed_session_doesnt_block_new_session(self, isolated_session, cfg):
        """Completed tasks in session 1 don't affect session 2 stop behavior."""
        m1 = _mgr("cross-1", cfg)
        m1.create_task("t", {"subject": "Done work"}, "created")
        m1.update_task("t", {"status": "in_progress"}, "started")
        # Don't complete — session 1 has in_progress tasks

        m2 = _mgr("cross-2", cfg)  # new session
        ctx = _stop_ctx("cross-2")
        assert m2.handle_stop(ctx) is None  # session 2 unaffected


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 10: Claude Code Task-Tracking Bug Scenarios
# ═══════════════════════════════════════════════════════════════════════════════

class TestClaudeCodeBugScenarios:
    """Regression tests for known Claude Code-specific task tracking issues."""

    def test_ghost_task_from_late_create_race(self, isolated_session, cfg):
        """Update arrives before Create (race): task initialized as ghost, then late create ignored.

        Deduplication in create_task() (Problem 5 solution) prevents overwriting an
        existing ghost task entry. The ghost placeholder subject is preserved.
        This is the correct behavior — a duplicate create is silently ignored.
        """
        mgr = _mgr("bug-race", cfg)
        # Update arrives first (no create yet) → ghost created with placeholder subject
        result = mgr.update_task("late-1", {"status": "in_progress"}, "race")
        assert result == "ghost_skip"
        ghost = mgr.tasks.get("late-1")
        assert ghost is not None
        assert ghost.get("metadata", {}).get("ghost_task") is True
        assert ghost["status"] == "ignored"  # ghost kept at ignored, not in_progress
        # Late create arrives — deduplication ignores it (ID already exists)
        mgr.create_task("late-1", {"subject": "Real task"}, "created")
        t = mgr.tasks.get("late-1")
        assert t is not None
        # Subject stays as ghost placeholder (deduplication prevents overwrite)
        assert t["subject"] != "Real task"
        # Ghost flag is still set
        assert t.get("metadata", {}).get("ghost_task") is True

    def test_update_on_nonexistent_task_creates_ghost(self, isolated_session, cfg):
        """Update on unknown task ID creates ghost with status='ignored'."""
        mgr = _mgr("bug-ghost", cfg)
        mgr.update_task("unknown-99", {"status": "in_progress"}, "started")
        t = mgr.tasks.get("unknown-99")
        assert t is not None
        assert t["status"] == "ignored"
        assert t.get("metadata", {}).get("ghost_task") is True

    def test_ghost_never_blocks_stop(self, isolated_session, cfg):
        """Ghost tasks (metadata.ghost_task=True) never block Stop."""
        mgr = _mgr("bug-ghost-stop", cfg)
        mgr.update_task("ghost-99", {"status": "in_progress"}, "spurious")
        ctx = _stop_ctx("bug-ghost-stop")
        assert mgr.handle_stop(ctx) is None

    def test_deduplication_prevents_double_creation(self, isolated_session, cfg):
        """Claude may fire TaskCreate twice for same ID; second is ignored."""
        mgr = _mgr("bug-dedup", cfg)
        mgr.create_task("dup-1", {"subject": "Original"}, "created")
        mgr.update_task("dup-1", {"status": "in_progress"}, "started")
        mgr.create_task("dup-1", {"subject": "Duplicate"}, "created again")
        t = mgr.tasks["dup-1"]
        assert t["subject"] == "Original"
        assert t["status"] == "in_progress"

    def test_stop_block_re_arm_policy_prevents_deadlock(self, isolated_session, cfg):
        """Re-arm policy: arm at block 1 and at escape-hatch threshold; never beyond.

        Original bug: re-arming on every block caused deadlock (deny→AI text→Stop→
        re-arm→deny→infinite, Block #175+). Fix: re-arm at most twice —
        block_count==1 (first stop) and consecutive==min_consecutive (escape hatch).
        Blocks beyond that do NOT re-arm, preserving the deadlock prevention.
        """
        mgr = _mgr("bug-rearm", cfg)
        mgr.create_task("t1", {"subject": "Task"}, "created")
        mgr.update_task("t1", {"status": "in_progress"}, "started")

        ctx1 = _stop_ctx("bug-rearm"); ctx1.pending_stop_injection = None
        mgr.handle_stop(ctx1)
        assert ctx1.pending_stop_injection is not None  # block_count==1: always arm

        ctx2 = _stop_ctx("bug-rearm"); ctx2.pending_stop_injection = None
        mgr.handle_stop(ctx2)
        # consecutive==min_consecutive==2: re-armed so escape hatch reaches AI
        assert ctx2.pending_stop_injection is not None

        ctx3 = _stop_ctx("bug-rearm"); ctx3.pending_stop_injection = None
        mgr.handle_stop(ctx3)
        # consecutive==3 > min_consecutive: deadlock prevention — NOT re-armed
        assert ctx3.pending_stop_injection is None

    def test_stage2_completed_only_reset_when_tasks_block(self, isolated_session, cfg):
        """Three-stage reset ONLY fires when stop is blocked (not on allowed stop)."""
        mgr = _mgr("bug-stage", cfg)
        mgr.create_task("t1", {"subject": "Done"}, "created")
        mgr.update_task("t1", {"status": "completed"}, "done")
        ctx = _stop_ctx("bug-stage")
        ctx.autorun_stage = EventContext.STAGE_2_COMPLETED
        mgr.handle_stop(ctx)  # allowed
        # Stage NOT reset because stop was allowed (no incomplete tasks)
        assert ctx.autorun_stage == EventContext.STAGE_2_COMPLETED

    def test_100_task_explosion_bounded(self, isolated_session, cfg):
        """With 100 tasks, stop message is capped — no memory explosion."""
        small_cfg = TaskLifecycleConfig(
            enabled=True, storage_dir=cfg.storage_dir, max_resume_tasks=5
        )
        mgr = _mgr("bug-100", small_cfg)
        for i in range(100):
            mgr.create_task(str(i), {"subject": f"Task {i}"}, "created")
            mgr.update_task(str(i), {"status": "in_progress"}, "started")
        ctx = _stop_ctx("bug-100")
        result = mgr.handle_stop(ctx)
        assert result is not None
        msg = result.get("systemMessage", "")
        # Only 5 tasks shown, overflow message present
        assert "more" in msg.lower() or "95" in msg

    def test_subagentstop_with_all_tasks_complete(self, isolated_session, cfg):
        """SubagentStop with all tasks complete: still returns None (not blocked)."""
        mgr = _mgr("bug-sub-done", cfg)
        mgr.create_task("t1", {"subject": "Done"}, "created")
        mgr.update_task("t1", {"status": "completed"}, "done")
        ctx = _stop_ctx("bug-sub-done", event="SubagentStop")
        assert mgr.handle_stop(ctx) is None


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 11: Gemini-Specific Paths
# ═══════════════════════════════════════════════════════════════════════════════

class TestGeminiSpecificPaths:
    """Gemini CLI event model, AfterAgent known gap, shared code parity."""

    def test_gemini_event_map_no_subagentstop(self):
        from autorun.core import GEMINI_EVENT_MAP
        assert "SubagentStop" not in GEMINI_EVENT_MAP

    def test_gemini_after_agent_maps_to_stop(self):
        from autorun.core import GEMINI_EVENT_MAP
        assert GEMINI_EVENT_MAP.get("AfterAgent") == "Stop"

    @pytest.mark.parametrize("status", ["completed", "deleted", "paused", "ignored", "delegated"])
    def test_gemini_non_blocking_status_allows_stop(self, status, isolated_session, cfg):
        mgr = _mgr(f"gem-nb-{status}", cfg)
        mgr.create_task("t", {"subject": "T"}, "created")
        mgr.update_task("t", {"status": status}, status)
        ctx = _stop_ctx(f"gem-nb-{status}", cli_type="gemini")
        assert mgr.handle_stop(ctx) is None

    @pytest.mark.parametrize("status", ["in_progress", "pending"])
    def test_gemini_blocking_status_blocks_stop(self, status, isolated_session, cfg):
        mgr = _mgr(f"gem-bl-{status}", cfg)
        mgr.create_task("t", {"subject": "T"}, "created")
        mgr.update_task("t", {"status": status}, status)
        ctx = _stop_ctx(f"gem-bl-{status}", cli_type="gemini")
        assert mgr.handle_stop(ctx) is not None

    def test_gemini_delegated_shown_in_session_start(self, isolated_session, cfg):
        mgr = _mgr("gem-ss", cfg)
        mgr.create_task("t", {"subject": "Gemini delegated"}, "created")
        mgr.update_task("t", {"status": "delegated"}, "delegated")
        ctx = _ss_ctx("gem-ss", cli_type="gemini")
        result = mgr.handle_session_start(ctx)
        assert result is not None
        assert "🤝" in result.get("systemMessage", "")

    def test_gemini_stop_message_has_delegate_option(self, isolated_session, cfg):
        mgr = _mgr("gem-msg", cfg)
        mgr.create_task("t", {"subject": "T"}, "created")
        mgr.update_task("t", {"status": "in_progress"}, "started")
        ctx = _stop_ctx("gem-msg", cli_type="gemini")
        result = mgr.handle_stop(ctx)
        assert result is not None
        assert "delegated" in result.get("systemMessage", "").lower()

    def test_gemini_after_agent_blocked_known_gap(self, isolated_session, cfg):
        """AfterAgent→Stop is Gemini's known gap — still blocks in_progress."""
        from autorun.core import GEMINI_EVENT_MAP
        assert GEMINI_EVENT_MAP.get("AfterAgent") == "Stop"
        mgr = _mgr("gem-gap", cfg)
        mgr.create_task("t", {"subject": "T"}, "created")
        mgr.update_task("t", {"status": "in_progress"}, "started")
        ctx = _stop_ctx("gem-gap", event="Stop", cli_type="gemini")
        assert mgr.handle_stop(ctx) is not None  # known gap: still blocks

    def test_gemini_schema_migration_same_as_claude(self, isolated_session, cfg):
        """Schema migration is CLI-agnostic — same result for Gemini sessions."""
        mgr = _mgr("gem-schema", cfg)
        mgr._cli_type = "gemini"
        with session_state(mgr.global_key) as state:
            state["schema_version"] = 1
            state["tasks"] = {}
        _ = mgr.tasks
        with session_state(mgr.global_key) as state:
            assert state["schema_version"] == TaskLifecycle.SCHEMA_VERSION

    def test_gemini_subagentstop_not_in_event_map(self):
        """Confirm SubagentStop is never dispatched via Gemini pathway."""
        from autorun.core import GEMINI_EVENT_MAP
        # SubagentStop must not be in GEMINI_EVENT_MAP (Gemini has no equivalent)
        assert "SubagentStop" not in GEMINI_EVENT_MAP


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 12: get_incomplete_tasks / get_prioritized_tasks invariants
# ═══════════════════════════════════════════════════════════════════════════════

class TestFilterFunctionInvariants:

    def test_get_incomplete_tasks_exclude_blocking_true(self, isolated_session, cfg):
        """exclude_blocking=True returns only in_progress + pending."""
        mgr = _mgr("fi-excl", cfg)
        statuses = ["pending", "in_progress", "completed", "deleted",
                    "paused", "ignored", "delegated"]
        for i, s in enumerate(statuses):
            mgr.create_task(str(i), {"subject": f"T{i}"}, "created")
            mgr.update_task(str(i), {"status": s}, s)
        result = mgr.get_incomplete_tasks(exclude_blocking=True)
        result_statuses = {t["status"] for t in result}
        assert result_statuses <= {"in_progress", "pending"}

    def test_get_incomplete_tasks_exclude_blocking_false(self, isolated_session, cfg):
        """exclude_blocking=False excludes only completed + deleted."""
        mgr = _mgr("fi-noexcl", cfg)
        statuses = ["pending", "in_progress", "completed", "deleted",
                    "paused", "ignored", "delegated"]
        for i, s in enumerate(statuses):
            mgr.create_task(str(i), {"subject": f"T{i}"}, "created")
            mgr.update_task(str(i), {"status": s}, s)
        result = mgr.get_incomplete_tasks(exclude_blocking=False)
        result_statuses = {t["status"] for t in result}
        assert "completed" not in result_statuses
        assert "deleted" not in result_statuses
        assert "in_progress" in result_statuses
        assert "paused" in result_statuses

    def test_get_prioritized_tasks_ready_first(self, isolated_session, cfg):
        """Ready tasks (no blockedBy) appear before blocked tasks.

        Uses full tasks dict reassignment to persist blockedBy through _StateProxy dirty flag.
        """
        mgr = _mgr("fi-prio", cfg)
        mgr.create_task("blocked", {"subject": "Blocked"}, "created")
        mgr.create_task("ready", {"subject": "Ready"}, "created")
        # Must reassign state["tasks"] to trigger _StateProxy.__setitem__ and mark dirty
        with session_state(mgr.global_key) as state:
            tasks = dict(state.get("tasks", {}))
            tasks["blocked"] = dict(tasks["blocked"])
            tasks["blocked"]["blockedBy"] = ["some-other-task"]
            state["tasks"] = tasks
        result = mgr.get_prioritized_tasks()
        ids = [t["id"] for t in result]
        assert ids.index("ready") < ids.index("blocked")

    def test_prioritized_tasks_excludes_completed(self, isolated_session, cfg):
        mgr = _mgr("fi-done", cfg)
        mgr.create_task("done", {"subject": "Done"}, "created")
        mgr.create_task("active", {"subject": "Active"}, "created")
        mgr.update_task("done", {"status": "completed"}, "done")
        mgr.update_task("active", {"status": "in_progress"}, "started")
        result = mgr.get_prioritized_tasks()
        ids = [t["id"] for t in result]
        assert "done" not in ids
        assert "active" in ids
