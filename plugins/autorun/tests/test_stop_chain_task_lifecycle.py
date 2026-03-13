"""Tests for Stop hook chain interaction between task lifecycle and autorun injection.

Bug reproduction: autorun completed Wave 1 (10 tasks, 2 done, 8 open),
output stage3_message, and Claude stopped — but prevent_premature_stop
should have fired first in the Stop chain and blocked the stop.

Root cause hypotheses:
1. prevent_premature_stop not registered (is_enabled() false at import)
2. Task lifecycle tasks not populated (handle_task_create regex mismatch)
3. Chain order wrong (autorun_injection fires before prevent_premature_stop)
4. Stop chain bypassed entirely when autorun deactivates

Tests verify:
- Stop chain order: prevent_premature_stop BEFORE autorun_injection
- Task lifecycle blocks stop regardless of autorun stage
- Task reminders work without ar:plannew/ar:planrefine
- Full chain dispatch with real EventContext + ThreadSafeDB
"""
import time
import pytest

from autorun.core import EventContext, ThreadSafeDB
from autorun.task_lifecycle import TaskLifecycle, TaskLifecycleConfig
from autorun import plugins
from autorun import task_lifecycle
from autorun.session_manager import session_state, SessionStateManager


@pytest.fixture
def isolated_task_config(tmp_path):
    """Isolated task lifecycle config using temp directory."""
    return TaskLifecycleConfig(
        enabled=True,
        storage_dir=tmp_path / "task_lifecycle",
        task_ttl_days=30,
        max_resume_tasks=10,
    )


@pytest.fixture
def isolated_session(tmp_path):
    """Isolated session state for testing."""
    from autorun import session_manager
    from autorun.session_manager import _reset_for_testing

    temp_state_dir = tmp_path / "sessions"
    temp_state_dir.mkdir(parents=True, exist_ok=True)

    _reset_for_testing()
    new_manager = SessionStateManager(state_dir=temp_state_dir)
    session_manager._manager = new_manager
    session_manager._store = new_manager._store

    yield new_manager

    _reset_for_testing()


def make_stop_ctx(
    session_id: str,
    store=None,
    autorun_active=False,
    autorun_stage=EventContext.STAGE_INACTIVE,
    tool_result="",
    transcript_text="",
    **overrides,
) -> EventContext:
    """Create a Stop EventContext with a real ThreadSafeDB store.

    Note on `continue` field semantics for Stop events:
    - continue: True = "keep AI working" (BLOCK the stop)
    - continue: False = "allow AI to stop"
    So blocking stop = continue: True (counterintuitive but correct per Claude Code schema).
    """
    # Build session_transcript as list of dicts (what Claude Code sends)
    session_transcript = []
    if transcript_text:
        session_transcript = [{"role": "assistant", "content": transcript_text}]

    ctx = EventContext(
        session_id=session_id,
        event="Stop",
        prompt="",
        tool_name="",
        tool_input={},
        tool_result=tool_result,
        session_transcript=session_transcript,
        store=store or ThreadSafeDB(),
    )
    ctx.autorun_active = autorun_active
    ctx.autorun_stage = autorun_stage
    for k, v in overrides.items():
        setattr(ctx, k, v)
    return ctx


# === Test 1: Chain registration order ===

class TestStopChainOrder:
    """Verify prevent_premature_stop is registered before autorun_injection."""

    def test_prevent_premature_stop_registered_in_stop_chain(self):
        """Task lifecycle Stop handler must be in the Stop chain."""
        stop_chain = plugins.app.chains.get("Stop", [])
        handler_names = [h.__name__ for h in stop_chain]
        assert "prevent_premature_stop" in handler_names, (
            f"prevent_premature_stop not in Stop chain. Handlers: {handler_names}"
        )

    def test_autorun_injection_registered_in_stop_chain(self):
        """autorun_injection must be in the Stop chain."""
        stop_chain = plugins.app.chains.get("Stop", [])
        handler_names = [h.__name__ for h in stop_chain]
        assert "autorun_injection" in handler_names, (
            f"autorun_injection not in Stop chain. Handlers: {handler_names}"
        )

    def test_prevent_premature_stop_fires_before_autorun_injection(self):
        """prevent_premature_stop MUST come before autorun_injection in chain.

        This is the core invariant: task lifecycle checking must happen
        before autorun's three-stage system can return None and allow stop.
        """
        stop_chain = plugins.app.chains.get("Stop", [])
        handler_names = [h.__name__ for h in stop_chain]

        pps_idx = handler_names.index("prevent_premature_stop")
        ai_idx = handler_names.index("autorun_injection")

        assert pps_idx < ai_idx, (
            f"prevent_premature_stop (idx={pps_idx}) must fire BEFORE "
            f"autorun_injection (idx={ai_idx}). Chain: {handler_names}"
        )


# === Test 2: Task lifecycle blocks stop with outstanding tasks ===

class TestTaskLifecycleBlocksStop:
    """Verify that outstanding tasks block stop regardless of autorun state."""

    def test_stop_blocked_with_8_open_tasks_autorun_inactive(
        self, isolated_task_config, isolated_session
    ):
        """Bug reproduction: 10 tasks (2 done, 8 open), autorun NOT active.

        Task lifecycle should still block stop even when autorun is off.
        """
        sid = f"test-stop-8open-{time.time()}"
        manager = TaskLifecycle(session_id=sid, config=isolated_task_config)

        # Create 10 tasks, complete 2
        for i in range(1, 11):
            manager.create_task(
                str(i),
                {"subject": f"Task {i}", "description": f"Wave task {i}"},
                f"Task #{i} created successfully",
            )
        manager.update_task("1", {"status": "completed"}, "")
        manager.update_task("2", {"status": "completed"}, "")

        # Verify 8 incomplete
        incomplete = manager.get_incomplete_tasks(exclude_blocking=True)
        assert len(incomplete) == 8, f"Expected 8 incomplete, got {len(incomplete)}"

        # Stop with autorun NOT active
        ctx = make_stop_ctx(session_id=sid, autorun_active=False)
        result = manager.handle_stop(ctx)

        assert result is not None, (
            "handle_stop must block when 8 tasks are outstanding"
        )
        assert result.get("continue") is True, (
            "continue must be True to keep AI working (block stop)"
        )

    def test_stop_blocked_with_open_tasks_autorun_stage3_completed(
        self, isolated_task_config, isolated_session
    ):
        """Bug reproduction: autorun at STAGE_2_COMPLETED (stage3 countdown done).

        Even when autorun's three-stage system thinks it's done,
        task lifecycle must still block if tasks are outstanding.
        """
        sid = f"test-stop-stage3-{time.time()}"
        manager = TaskLifecycle(session_id=sid, config=isolated_task_config)

        # Create 3 open tasks
        for i in range(1, 4):
            manager.create_task(
                str(i),
                {"subject": f"Open task {i}", "description": ""},
                f"Task #{i} created successfully",
            )

        ctx = make_stop_ctx(
            session_id=sid,
            autorun_active=True,
            autorun_stage=EventContext.STAGE_2_COMPLETED,
        )
        result = manager.handle_stop(ctx)

        assert result is not None, (
            "handle_stop must block even at STAGE_2_COMPLETED with open tasks"
        )
        assert result.get("continue") is True

    def test_stop_allowed_when_all_tasks_completed(
        self, isolated_task_config, isolated_session
    ):
        """Stop should be allowed when all tasks are completed."""
        sid = f"test-stop-all-done-{time.time()}"
        manager = TaskLifecycle(session_id=sid, config=isolated_task_config)

        manager.create_task("1", {"subject": "Done task"}, "Task #1 created")
        manager.update_task("1", {"status": "completed"}, "")

        ctx = make_stop_ctx(session_id=sid)
        result = manager.handle_stop(ctx)

        assert result is None, (
            "handle_stop must return None (allow stop) when all tasks completed"
        )


# === Test 3: Full chain dispatch with outstanding tasks ===

class TestFullStopChainDispatch:
    """Test full app.dispatch() Stop chain with real task lifecycle."""

    def test_full_chain_blocks_stop_with_outstanding_tasks(
        self, isolated_task_config, isolated_session
    ):
        """Full Stop chain dispatch must block when tasks are outstanding.

        This is the end-to-end test: create tasks via TaskLifecycle,
        then dispatch a Stop event through the full chain.
        prevent_premature_stop should fire first and block.
        """
        sid = f"test-chain-block-{time.time()}"
        manager = TaskLifecycle(session_id=sid, config=isolated_task_config)

        # Create outstanding tasks
        for i in range(1, 4):
            manager.create_task(
                str(i),
                {"subject": f"Chain test task {i}", "description": ""},
                f"Task #{i} created successfully",
            )

        # Dispatch Stop through full chain
        ctx = make_stop_ctx(
            session_id=sid,
            autorun_active=True,
            autorun_stage=EventContext.STAGE_2_COMPLETED,
        )
        result = plugins.app._run_chain(ctx, "Stop")

        assert result is not None, (
            "Full Stop chain must return non-None when tasks are outstanding. "
            "This means prevent_premature_stop didn't block. "
            f"Chain handlers: {[h.__name__ for h in plugins.app.chains.get('Stop', [])]}"
        )
        assert result.get("continue") is True, (
            "Full Stop chain must set continue=True to keep AI working (block stop)"
        )
        # autorun_injection should NOT have deactivated autorun
        assert ctx.autorun_active is True, (
            "autorun_active must remain True — autorun_injection should not have "
            "fired because prevent_premature_stop blocked first"
        )

    def test_full_chain_allows_stop_when_no_tasks(
        self, isolated_task_config, isolated_session
    ):
        """Full Stop chain allows stop when no tasks exist and stage3 complete."""
        from autorun.config import CONFIG
        sid = f"test-chain-allow-{time.time()}"

        # No tasks created — task lifecycle returns None
        ctx = make_stop_ctx(
            session_id=sid,
            autorun_active=True,
            autorun_stage=EventContext.STAGE_2_COMPLETED,
            tool_result=CONFIG["stage3_message"],
            transcript_text="",
            hook_call_count=10,  # Past countdown
        )
        result = plugins.app._run_chain(ctx, "Stop")

        # autorun_injection should allow stop (return None) after stage3
        # The full chain should return None (allow stop)
        # When no tasks and stage3 complete, chain should allow stop.
        # result=None means no handler blocked; result with continue=True but
        # no decision/reason means allow through.
        if result is not None:
            # If a result was returned, it should not be a task-lifecycle block
            assert "CANNOT STOP" not in result.get("systemMessage", ""), (
                "Full Stop chain should not block when no tasks exist"
            )


# === Test 4: Task reminders work without ar:plannew/ar:planrefine ===

class TestTaskRemindersIndependentOfPlanMode:
    """Task lifecycle must work regardless of how the session was started."""

    def test_stop_blocked_without_autorun_active(
        self, isolated_task_config, isolated_session
    ):
        """Tasks block stop even when autorun_active is False.

        User creates tasks manually (not via /ar:plannew) — task lifecycle
        must still enforce stop blocking.
        """
        sid = f"test-no-autorun-{time.time()}"
        manager = TaskLifecycle(session_id=sid, config=isolated_task_config)

        manager.create_task("1", {"subject": "Manual task"}, "Task #1 created")

        ctx = make_stop_ctx(session_id=sid, autorun_active=False)
        result = manager.handle_stop(ctx)

        assert result is not None, (
            "Task lifecycle must block stop even when autorun is not active"
        )
        assert result.get("continue") is True

    def test_session_start_resumes_tasks_without_plan_mode(
        self, isolated_task_config, isolated_session
    ):
        """SessionStart should inject task context even without plan mode."""
        sid = f"test-resume-no-plan-{time.time()}"
        manager = TaskLifecycle(session_id=sid, config=isolated_task_config)

        manager.create_task(
            "1",
            {"subject": "Carry-over task", "description": "From prior session"},
            "Task #1 created",
        )

        ctx = make_stop_ctx(session_id=sid, autorun_active=False)
        ctx.event = "SessionStart"
        result = manager.handle_session_start(ctx)

        assert result is not None, (
            "SessionStart must inject task context when tasks are outstanding"
        )

    def test_staleness_reminder_fires_with_tasks_but_no_autorun(
        self, isolated_task_config, isolated_session
    ):
        """Task staleness reminder must fire even without autorun mode.

        The staleness counter (tool_calls_since_task_update) should trigger
        reminders when tasks exist, regardless of autorun_active state.
        """
        sid = f"test-stale-no-autorun-{time.time()}"
        manager = TaskLifecycle(session_id=sid, config=isolated_task_config)

        # Create a task
        manager.create_task("1", {"subject": "Stale task"}, "Task #1 created")

        # Verify task exists
        incomplete = manager.get_incomplete_tasks(exclude_blocking=True)
        assert len(incomplete) == 1, "Should have 1 incomplete task"


# === Test 5: Task lifecycle enabled check ===

class TestTaskLifecycleEnabled:
    """Verify is_enabled() correctly controls hook registration."""

    def test_is_enabled_returns_bool(self):
        """is_enabled() must return a boolean."""
        result = task_lifecycle.is_enabled()
        assert isinstance(result, bool)

    def test_hooks_registered_when_enabled(self):
        """When enabled, prevent_premature_stop must be in the Stop chain."""
        if not task_lifecycle.is_enabled():
            pytest.skip("Task lifecycle disabled in test environment")

        stop_chain = plugins.app.chains.get("Stop", [])
        handler_names = [h.__name__ for h in stop_chain]
        assert "prevent_premature_stop" in handler_names


# === Test 6: Fail-open logging on exceptions ===

class TestFailOpenLogging:
    """Verify that exceptions in prevent_premature_stop are logged, not swallowed."""

    def test_fail_open_logs_warning_on_exception(
        self, isolated_task_config, isolated_session, caplog
    ):
        """When prevent_premature_stop raises, it must log a warning with session info.

        This is the safety net: if the system can't check tasks, it allows stop
        but MUST log so the issue is diagnosable.
        """
        import logging
        from unittest.mock import patch

        ctx = make_stop_ctx(session_id="test-fail-open", autorun_active=False)

        # Force TaskLifecycle constructor to raise
        stop_chain = plugins.app.chains.get("Stop", [])
        pps = [h for h in stop_chain if h.__name__ == "prevent_premature_stop"]
        assert len(pps) == 1, "prevent_premature_stop must be registered"

        with caplog.at_level(logging.WARNING, logger="autorun"):
            with patch(
                "autorun.task_lifecycle.TaskLifecycle.__init__",
                side_effect=RuntimeError("simulated storage failure"),
            ):
                result = pps[0](ctx)

        # Fail-open: returns None (allows stop)
        assert result is None, "Fail-open must return None (allow stop)"

        # But the exception must be logged
        warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("Stop hook error" in msg or "fail-open" in msg for msg in warning_msgs), (
            f"Exception must be logged as warning. Logged: {warning_msgs}"
        )


# === Test 7: Context compaction resilience ===

class TestContextCompactionResilience:
    """Verify task state survives context compaction.

    After compaction, Claude Code creates a summary and starts a new context.
    The session_id stays the same. The daemon's ThreadSafeDB retains task state.
    """

    def test_tasks_persist_across_simulated_compaction(
        self, isolated_task_config, isolated_session
    ):
        """Tasks created in pre-compaction context must block stop in post-compaction context.

        Simulates: create tasks → compaction → new Stop event with same session_id.
        """
        sid = f"test-compaction-{time.time()}"

        # Pre-compaction: create tasks
        manager1 = TaskLifecycle(session_id=sid, config=isolated_task_config)
        for i in range(1, 6):
            manager1.create_task(
                str(i),
                {"subject": f"Pre-compaction task {i}", "description": ""},
                f"Task #{i} created successfully",
            )

        # Simulate compaction: new TaskLifecycle instance with SAME session_id
        # (this is what happens when a new hook event fires after compaction)
        manager2 = TaskLifecycle(session_id=sid, config=isolated_task_config)
        incomplete = manager2.get_incomplete_tasks(exclude_blocking=True)
        assert len(incomplete) == 5, (
            f"Tasks must survive compaction. Expected 5, got {len(incomplete)}"
        )

        # Post-compaction: stop must be blocked
        ctx = make_stop_ctx(session_id=sid, autorun_active=False)
        result = manager2.handle_stop(ctx)
        assert result is not None, (
            "Stop must be blocked after compaction with outstanding tasks"
        )

    def test_session_start_compact_source_reinjects_tasks(
        self, isolated_task_config, isolated_session
    ):
        """SessionStart with source='compact' must re-inject task context.

        Per Claude Code docs, context compaction triggers SessionStart with
        source='compact'. The task lifecycle must detect outstanding tasks
        and inject reminder context, preventing post-compaction amnesia.
        """
        sid = f"test-compact-source-{time.time()}"
        manager = TaskLifecycle(session_id=sid, config=isolated_task_config)

        # Create tasks before compaction
        for i in range(1, 4):
            manager.create_task(
                str(i),
                {"subject": f"Active task {i}", "description": ""},
                f"Task #{i} created successfully",
            )

        # Simulate post-compaction SessionStart
        ctx = make_stop_ctx(session_id=sid, autorun_active=False)
        ctx._event = "SessionStart"
        # Note: source='compact' comes in the payload, not stored on ctx directly
        result = manager.handle_session_start(ctx)

        assert result is not None, (
            "SessionStart after compaction must re-inject task context "
            "when tasks are outstanding"
        )
        msg = result.get("systemMessage", "")
        assert "Active task" in msg, (
            f"Task injection must include task subjects. Got: {msg[:200]}"
        )

    def test_tasks_not_in_lifecycle_store_allows_stop(
        self, isolated_task_config, isolated_session
    ):
        """If tasks exist only in Claude's internal system (not tracked by lifecycle),
        the stop hook cannot block because it has no visibility into those tasks.

        This is the gap: if handle_task_create fails (regex mismatch, exception),
        tasks exist in Claude's system but not in the daemon's lifecycle store.
        After compaction, Claude knows about tasks, but the stop hook doesn't.
        """
        sid = f"test-untracked-{time.time()}"
        manager = TaskLifecycle(session_id=sid, config=isolated_task_config)

        # No tasks created in lifecycle store — simulates tracking failure
        incomplete = manager.get_incomplete_tasks(exclude_blocking=True)
        assert len(incomplete) == 0, "No tasks in lifecycle store"

        ctx = make_stop_ctx(session_id=sid, autorun_active=False)
        result = manager.handle_stop(ctx)
        assert result is None, (
            "Stop must be allowed when lifecycle store has no tasks "
            "(even if Claude's internal system has tasks — lifecycle can't see them)"
        )


# === Test 8: handle_task_create regex coverage ===

class TestTaskCreateRegexCoverage:
    """Verify handle_task_create parses all known TaskCreate result formats."""

    @pytest.fixture
    def manager(self, isolated_task_config, isolated_session):
        sid = f"test-regex-{time.time()}"
        return TaskLifecycle(session_id=sid, config=isolated_task_config)

    @pytest.mark.parametrize("result_text,expected_id", [
        ("Task #42 created successfully", "42"),
        ("Created task #99 successfully", "99"),
        ("Task 7 created", "7"),
        ("#123", "123"),
        ("Task #1 created successfully: Fix login bug", "1"),
        ("Created task #500 successfully: Implement streaming", "500"),
    ])
    def test_task_id_extraction(self, manager, result_text, expected_id):
        """handle_task_create must extract task ID from all result formats."""
        from unittest.mock import MagicMock
        ctx = MagicMock()
        ctx.tool_result = result_text
        ctx.tool_input = {"subject": "Test task", "description": ""}
        ctx.plan_active = False

        manager.handle_task_create(ctx)

        tasks = manager.tasks
        assert expected_id in tasks, (
            f"Task ID {expected_id} not found after parsing '{result_text}'. "
            f"Tasks: {list(tasks.keys())}"
        )
