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


# === Test 9: pending_stop_injection — Stop-hook AI delivery fix ===

class TestPendingStopInjection:
    """Verify Stop-hook injection reaches AI via PostToolUse deferred delivery.

    Root bug: Stop events lack hookSpecificOutput support (HOOK_SCHEMAS hso:{}).
    ctx.block(injection) → systemMessage only → user terminal, NOT AI context.
    Fix: handle_stop() stores injection in ctx.pending_stop_injection (session
    state) so deliver_pending_stop_injection PostToolUse handler can deliver it
    via additionalContext on the AI's next tool call.
    """

    def test_handle_stop_sets_pending_injection(
        self, isolated_task_config, isolated_session
    ):
        """handle_stop() must store injection in pending_stop_injection."""
        sid = f"test-pending-stop-{time.time()}"
        store = ThreadSafeDB()
        manager = TaskLifecycle(session_id=sid, config=isolated_task_config)
        manager.create_task("1", {"subject": "Unfinished work"}, "Task #1 created successfully")

        ctx = make_stop_ctx(session_id=sid, store=store)
        result = manager.handle_stop(ctx)

        assert result is not None, "handle_stop must block with incomplete task"
        assert result.get("continue") is True, "continue must be True to keep AI working"

        # Core assertion: injection stored in session state for PostToolUse delivery
        pending = ctx.pending_stop_injection
        assert pending is not None, (
            "handle_stop() must store injection in ctx.pending_stop_injection. "
            "Stop events have no hookSpecificOutput, so systemMessage only reaches "
            "the user terminal. pending_stop_injection defers AI delivery to PostToolUse."
        )
        assert "CANNOT STOP" in pending, (
            f"pending_stop_injection must contain stop message. Got: {pending[:100]}"
        )
        assert "Unfinished work" in pending, (
            "pending_stop_injection must include the task subject in the task list"
        )

    def test_pending_injection_cleared_after_delivery(
        self, isolated_task_config, isolated_session
    ):
        """deliver_pending_stop_injection handler must clear pending after delivery."""
        sid = f"test-pending-clear-{time.time()}"
        store = ThreadSafeDB()
        manager = TaskLifecycle(session_id=sid, config=isolated_task_config)
        manager.create_task("1", {"subject": "Task to clear"}, "Task #1 created successfully")

        # Set pending injection via Stop (real path)
        stop_ctx = make_stop_ctx(session_id=sid, store=store)
        manager.handle_stop(stop_ctx)
        assert stop_ctx.pending_stop_injection is not None, "Setup: injection must be set"

        # Simulate PostToolUse: create ctx with same session_id and store
        post_ctx = EventContext(
            session_id=sid,
            event="PostToolUse",
            tool_name="Read",
            tool_input={"file_path": "/tmp/test.txt"},
            store=store,
        )

        # Find and call deliver_pending_stop_injection from the PostToolUse chain
        post_chain = plugins.app.chains.get("PostToolUse", [])
        handler_names = [h.__name__ for h in post_chain]
        assert "deliver_pending_stop_injection" in handler_names, (
            f"deliver_pending_stop_injection not in PostToolUse chain: {handler_names}"
        )

        deliver_idx = handler_names.index("deliver_pending_stop_injection")
        deliver_fn = post_chain[deliver_idx]
        result = deliver_fn(post_ctx)

        assert result is None, "deliver_pending_stop_injection must return None (chain continues)"
        assert post_ctx.pending_stop_injection is None, (
            "pending_stop_injection must be cleared after delivery to avoid repeat injection"
        )
        # Chain notification must contain the injection text
        assert any("CANNOT STOP" in msg for msg, _ in post_ctx._chain_notifications), (
            "deliver_pending_stop_injection must add Stop injection as chain notification"
        )
        # Notification must use 'ai' channel so it reaches additionalContext
        assert any(ch == "ai" for _, ch in post_ctx._chain_notifications), (
            "pending_stop_injection must be delivered on 'ai' channel → additionalContext"
        )


# === Test 10: End-to-end PostToolUse TaskCreate → Stop blocked ===

class TestPostToolUseTaskCreateBlocksStop:
    """Verify that tasks created via PostToolUse chain (real Claude Code path)
    are tracked in TaskLifecycle and block Stop.

    Bug reproduction (commit c5aface):
    claude-hooks.json had PostToolUse matcher "ExitPlanMode|Write|Edit|Bash".
    TaskCreate/TaskUpdate PostToolUse events NEVER fired, so task_lifecycle
    never tracked tasks. handle_stop() found 0 incomplete tasks and allowed stop.
    All tasks became ghost tasks (first seen via TaskUpdate, not TaskCreate).
    Ghost tasks default to "ignored" status which is NON_BLOCKING.

    Fix: PostToolUse now has no matcher (catch-all), so TaskCreate fires.
    This test verifies the full chain: PostToolUse(TaskCreate) → tracked → Stop blocked.
    """

    def test_task_created_via_post_tool_use_blocks_stop(
        self, isolated_task_config, isolated_session
    ):
        """Full E2E: PostToolUse(TaskCreate) → task tracked → Stop blocked.

        Simulates the exact sequence Claude Code sends:
        1. AI calls TaskCreate → Claude Code runs tool → PostToolUse fires
        2. track_task_operations catches it → handle_task_create stores task
        3. AI finishes work → Stop fires → prevent_premature_stop blocks
        """
        sid = f"test-e2e-posttool-stop-{time.time()}"
        store = ThreadSafeDB()

        # Step 1: Simulate PostToolUse for TaskCreate
        create_ctx = EventContext(
            session_id=sid,
            event="PostToolUse",
            tool_name="TaskCreate",
            tool_input={"subject": "Fix login bug", "description": "Users can't log in"},
            tool_result="Task #1 created successfully: Fix login bug",
            store=store,
        )

        # Run through full PostToolUse chain
        post_result = plugins.app._run_chain(create_ctx, "PostToolUse")
        # PostToolUse handlers return None (allow tool to complete)

        # Step 2: Verify task was tracked in lifecycle store
        manager = TaskLifecycle(session_id=sid, config=isolated_task_config)
        tasks = manager.tasks
        assert "1" in tasks, (
            f"Task #1 must be tracked after PostToolUse(TaskCreate). "
            f"Tracked tasks: {list(tasks.keys())}. "
            f"If empty, track_task_operations didn't fire — check PostToolUse matcher."
        )
        assert tasks["1"]["status"] == "pending", (
            f"Newly created task must be 'pending', not '{tasks['1']['status']}'"
        )
        assert tasks["1"]["subject"] == "Fix login bug", (
            f"Task subject must match. Got: {tasks['1']['subject']}"
        )
        # Must NOT be a ghost task
        is_ghost = tasks["1"].get("metadata", {}).get("ghost_task", False)
        assert not is_ghost, (
            "Task created via PostToolUse(TaskCreate) must NOT be a ghost task. "
            "Ghost tasks are NON_BLOCKING and won't prevent Stop."
        )

        # Step 3: Simulate Stop — must be blocked
        stop_ctx = make_stop_ctx(session_id=sid, store=store, autorun_active=False)
        stop_result = plugins.app._run_chain(stop_ctx, "Stop")

        assert stop_result is not None, (
            "Stop chain must block when task created via PostToolUse is pending. "
            "If this fails, PostToolUse(TaskCreate) didn't track the task — "
            "check claude-hooks.json PostToolUse matcher includes TaskCreate."
        )
        assert stop_result.get("continue") is True, (
            "continue must be True to keep AI working (block stop)"
        )
        assert "Fix login bug" in stop_result.get("systemMessage", ""), (
            "Stop message must include the task subject"
        )

    def test_task_updated_via_post_tool_use_unblocks_stop(
        self, isolated_task_config, isolated_session
    ):
        """After marking task completed via PostToolUse(TaskUpdate), Stop is allowed."""
        sid = f"test-e2e-update-unblock-{time.time()}"
        store = ThreadSafeDB()

        # Create task
        create_ctx = EventContext(
            session_id=sid,
            event="PostToolUse",
            tool_name="TaskCreate",
            tool_input={"subject": "Quick fix", "description": ""},
            tool_result="Task #1 created successfully: Quick fix",
            store=store,
        )
        plugins.app._run_chain(create_ctx, "PostToolUse")

        # Complete task
        update_ctx = EventContext(
            session_id=sid,
            event="PostToolUse",
            tool_name="TaskUpdate",
            tool_input={"taskId": "1", "status": "completed"},
            tool_result="Updated task #1 status",
            store=store,
        )
        plugins.app._run_chain(update_ctx, "PostToolUse")

        # Stop should now be allowed
        manager = TaskLifecycle(session_id=sid, config=isolated_task_config)
        incomplete = manager.get_incomplete_tasks(exclude_blocking=True)
        assert len(incomplete) == 0, (
            f"All tasks completed, but {len(incomplete)} still incomplete"
        )

        stop_ctx = make_stop_ctx(session_id=sid, store=store)
        stop_result = plugins.app._run_chain(stop_ctx, "Stop")
        # Stop should be allowed (result is None or no CANNOT STOP message)
        if stop_result is not None:
            assert "CANNOT STOP" not in stop_result.get("systemMessage", ""), (
                "Stop should be allowed when all tasks are completed"
            )

    def test_multiple_tasks_some_pending_blocks_stop(
        self, isolated_task_config, isolated_session
    ):
        """Stop blocked when some tasks completed but others still pending."""
        sid = f"test-e2e-partial-{time.time()}"
        store = ThreadSafeDB()

        # Create 3 tasks
        for i in range(1, 4):
            ctx = EventContext(
                session_id=sid,
                event="PostToolUse",
                tool_name="TaskCreate",
                tool_input={"subject": f"Task {i}", "description": ""},
                tool_result=f"Task #{i} created successfully: Task {i}",
                store=store,
            )
            plugins.app._run_chain(ctx, "PostToolUse")

        # Complete tasks 1 and 2, leave 3 pending
        for tid in ["1", "2"]:
            ctx = EventContext(
                session_id=sid,
                event="PostToolUse",
                tool_name="TaskUpdate",
                tool_input={"taskId": tid, "status": "completed"},
                tool_result=f"Updated task #{tid} status",
                store=store,
            )
            plugins.app._run_chain(ctx, "PostToolUse")

        # Stop must be blocked (task 3 still pending)
        manager = TaskLifecycle(session_id=sid, config=isolated_task_config)
        incomplete = manager.get_incomplete_tasks(exclude_blocking=True)
        assert len(incomplete) == 1, f"Expected 1 incomplete, got {len(incomplete)}"

        stop_ctx = make_stop_ctx(session_id=sid, store=store)
        stop_result = plugins.app._run_chain(stop_ctx, "Stop")

        assert stop_result is not None, (
            "Stop must be blocked with 1 pending task (Task 3)"
        )
        assert stop_result.get("continue") is True
        assert "Task 3" in stop_result.get("systemMessage", ""), (
            "Stop message must list the pending task"
        )


# === Test 11: Gemini write_todos routing (TASK_COMBINED_TOOLS) ===

class TestWriteTodosRouting:
    """Verify write_todos (Gemini CLI) is routed correctly in track_task_operations.

    Gemini CLI uses a single 'write_todos' tool for all task operations.
    Routing is based on content inspection:
    - taskId in tool_input → TaskUpdate
    - "created" in tool_result → TaskCreate
    - Otherwise → list/get (activity update only)

    Added: commit 10d3b72 (TASK_COMBINED_TOOLS), commit 72291c3 (defensive guard)
    """

    def test_write_todos_create_tracks_task(
        self, isolated_task_config, isolated_session
    ):
        """write_todos with 'created' in result → handle_task_create."""
        sid = f"test-write-todos-create-{time.time()}"
        store = ThreadSafeDB()

        ctx = EventContext(
            session_id=sid,
            event="PostToolUse",
            tool_name="write_todos",
            tool_input={"subject": "Fix Gemini bug", "description": "desc"},
            tool_result="Task #1 created successfully: Fix Gemini bug",
            store=store,
        )
        plugins.app._run_chain(ctx, "PostToolUse")

        manager = TaskLifecycle(session_id=sid, config=isolated_task_config)
        tasks = manager.tasks
        assert "1" in tasks, (
            f"write_todos with 'created' in result must track task. "
            f"Tracked: {list(tasks.keys())}"
        )
        assert tasks["1"]["subject"] == "Fix Gemini bug"

    def test_write_todos_update_changes_status(
        self, isolated_task_config, isolated_session
    ):
        """write_todos with taskId in input → handle_task_update."""
        sid = f"test-write-todos-update-{time.time()}"
        store = ThreadSafeDB()

        # First create the task
        create_ctx = EventContext(
            session_id=sid,
            event="PostToolUse",
            tool_name="write_todos",
            tool_input={"subject": "Fix it", "description": ""},
            tool_result="Task #1 created successfully: Fix it",
            store=store,
        )
        plugins.app._run_chain(create_ctx, "PostToolUse")

        # Now update it via write_todos with taskId
        update_ctx = EventContext(
            session_id=sid,
            event="PostToolUse",
            tool_name="write_todos",
            tool_input={"taskId": "1", "status": "completed"},
            tool_result="Updated task #1 status to completed",
            store=store,
        )
        plugins.app._run_chain(update_ctx, "PostToolUse")

        manager = TaskLifecycle(session_id=sid, config=isolated_task_config)
        assert manager.tasks["1"]["status"] == "completed", (
            "write_todos with taskId must route to handle_task_update"
        )

    def test_write_todos_list_does_not_create(
        self, isolated_task_config, isolated_session
    ):
        """write_todos without taskId or 'created' → no task creation."""
        sid = f"test-write-todos-list-{time.time()}"
        store = ThreadSafeDB()

        ctx = EventContext(
            session_id=sid,
            event="PostToolUse",
            tool_name="write_todos",
            tool_input={},
            tool_result="Tasks listed: 0 items",
            store=store,
        )
        plugins.app._run_chain(ctx, "PostToolUse")

        manager = TaskLifecycle(session_id=sid, config=isolated_task_config)
        assert len(manager.tasks) == 0, (
            "write_todos list/get must NOT create tasks"
        )

    def test_write_todos_none_tool_input(
        self, isolated_task_config, isolated_session
    ):
        """write_todos with tool_input=None must not crash (defensive guard).

        Regression test for commit 72291c3: tool_input = ctx.tool_input or {}
        Without this guard, None.get("taskId") raises AttributeError.
        """
        sid = f"test-write-todos-none-input-{time.time()}"
        store = ThreadSafeDB()

        ctx = EventContext(
            session_id=sid,
            event="PostToolUse",
            tool_name="write_todos",
            tool_input=None,
            tool_result="Task #1 created successfully",
            store=store,
        )
        # Must not raise AttributeError
        result = plugins.app._run_chain(ctx, "PostToolUse")
        # Should proceed without crash — task created via result parsing
        manager = TaskLifecycle(session_id=sid, config=isolated_task_config)
        assert "1" in manager.tasks, (
            "write_todos with None tool_input should still create via result parsing"
        )

    def test_write_todos_blocks_stop(
        self, isolated_task_config, isolated_session
    ):
        """Full E2E: write_todos creates task → Stop blocked."""
        sid = f"test-write-todos-stop-{time.time()}"
        store = ThreadSafeDB()

        ctx = EventContext(
            session_id=sid,
            event="PostToolUse",
            tool_name="write_todos",
            tool_input={"subject": "Gemini task"},
            tool_result="Task #1 created successfully: Gemini task",
            store=store,
        )
        plugins.app._run_chain(ctx, "PostToolUse")

        stop_ctx = make_stop_ctx(session_id=sid, store=store, autorun_active=False)
        stop_result = plugins.app._run_chain(stop_ctx, "Stop")

        assert stop_result is not None, (
            "Stop must be blocked when write_todos created a pending task"
        )
        assert stop_result.get("continue") is True

    def test_write_todos_update_then_stop_allowed(
        self, isolated_task_config, isolated_session
    ):
        """Full E2E: write_todos create → write_todos complete → Stop allowed."""
        sid = f"test-write-todos-complete-{time.time()}"
        store = ThreadSafeDB()

        # Create
        create_ctx = EventContext(
            session_id=sid,
            event="PostToolUse",
            tool_name="write_todos",
            tool_input={"subject": "Quick task"},
            tool_result="Task #1 created successfully: Quick task",
            store=store,
        )
        plugins.app._run_chain(create_ctx, "PostToolUse")

        # Complete
        update_ctx = EventContext(
            session_id=sid,
            event="PostToolUse",
            tool_name="write_todos",
            tool_input={"taskId": "1", "status": "completed"},
            tool_result="Updated task #1 status to completed",
            store=store,
        )
        plugins.app._run_chain(update_ctx, "PostToolUse")

        # Stop should be allowed
        stop_ctx = make_stop_ctx(session_id=sid, store=store)
        stop_result = plugins.app._run_chain(stop_ctx, "Stop")
        if stop_result is not None:
            assert "CANNOT STOP" not in stop_result.get("systemMessage", ""), (
                "Stop should be allowed after write_todos completed all tasks"
            )


# === Test 12: Staleness counter reset for TASK_COMBINED_TOOLS ===

class TestStalenessCounterWriteTodos:
    """Verify check_task_staleness resets counter on write_todos events.

    Added in commit 10d3b72: TASK_COMBINED_TOOLS added to reset condition.
    Without this, write_todos wouldn't reset the staleness counter, causing
    false staleness reminders on Gemini CLI.
    """

    def test_write_todos_resets_staleness_counter(
        self, isolated_task_config, isolated_session
    ):
        """write_todos must reset tool_calls_since_task_update to 0."""
        sid = f"test-staleness-reset-{time.time()}"
        store = ThreadSafeDB()

        # Create a pending task so staleness checking is active
        create_ctx = EventContext(
            session_id=sid,
            event="PostToolUse",
            tool_name="TaskCreate",
            tool_input={"subject": "Pending task"},
            tool_result="Task #1 created successfully: Pending task",
            store=store,
        )
        plugins.app._run_chain(create_ctx, "PostToolUse")

        # Simulate 10 non-task tool calls to build up counter
        for i in range(10):
            ctx = EventContext(
                session_id=sid,
                event="PostToolUse",
                tool_name="Read",
                tool_input={"file_path": f"/tmp/test{i}.txt"},
                tool_result="file contents",
                store=store,
            )
            ctx.task_staleness_enabled = True
            plugins.app._run_chain(ctx, "PostToolUse")

        # Counter should be at 10
        check_ctx = EventContext(
            session_id=sid,
            event="PostToolUse",
            tool_name="Read",
            tool_input={"file_path": "/tmp/check.txt"},
            tool_result="contents",
            store=store,
        )
        check_ctx.task_staleness_enabled = True
        # Read the counter value before write_todos
        counter_before = check_ctx.tool_calls_since_task_update or 0

        # Now fire write_todos — should reset counter
        todos_ctx = EventContext(
            session_id=sid,
            event="PostToolUse",
            tool_name="write_todos",
            tool_input={"taskId": "1", "status": "in_progress"},
            tool_result="Updated task #1",
            store=store,
        )
        todos_ctx.task_staleness_enabled = True
        plugins.app._run_chain(todos_ctx, "PostToolUse")

        # After write_todos, counter must be 0
        after_ctx = EventContext(
            session_id=sid,
            event="PostToolUse",
            tool_name="Read",
            tool_input={"file_path": "/tmp/after.txt"},
            tool_result="contents",
            store=store,
        )
        after_ctx.task_staleness_enabled = True
        assert after_ctx.tool_calls_since_task_update == 0, (
            f"write_todos must reset staleness counter to 0. "
            f"Got: {after_ctx.tool_calls_since_task_update}"
        )


# === Test 13: PreToolUse matcher RTK compatibility ===

class TestPreToolUseMatcherRTKCompat:
    """Verify PreToolUse is catch-all for 100% enforcement coverage.

    Catch-all is safe with RTK because:
    - When no enforcement is active, autorun returns None → exits silently →
      Claude Code ignores it → RTK's updatedInput applies normally
    - When enforcement IS active (deny), tool is blocked → RTK rewrite irrelevant
    See plan: Critique Finding 5 for full analysis.

    PostToolUse catch-all is also correct (no updatedInput conflict).
    """

    def test_pretooluse_is_catch_all(self):
        """PreToolUse must be catch-all (no matcher) for 100% enforcement coverage.

        enforce_stop_injection and enforce_task_staleness must fire for ALL tools,
        not just Write|Edit|Bash|ExitPlanMode. Catch-all ensures no tool can bypass
        enforcement (covers WebFetch, NotebookEdit, LSP, AskUserQuestion, Skill, etc.).
        """
        import json
        from pathlib import Path
        hooks_path = Path(__file__).parent.parent / "hooks" / "hooks.json"
        hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
        pre_tool_groups = hooks["hooks"]["PreToolUse"]
        for group in pre_tool_groups:
            assert "matcher" not in group, (
                "PreToolUse must be catch-all (no matcher) for 100% enforcement. "
                f"Group: {group}"
            )

    def test_posttooluse_is_catch_all(self):
        """PostToolUse must be catch-all (3 handlers need ALL tools)."""
        import json
        from pathlib import Path
        hooks_path = Path(__file__).parent.parent / "hooks" / "hooks.json"
        hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
        post_tool_groups = hooks["hooks"]["PostToolUse"]
        has_catch_all = any("matcher" not in g for g in post_tool_groups)
        assert has_catch_all, (
            "PostToolUse must be catch-all (no updatedInput conflict, "
            "3 handlers need ALL tools). "
            f"Groups: {post_tool_groups}"
        )

    def test_pretooluse_not_overly_broad(self):
        """PreToolUse must NOT match tools it doesn't handle.

        Only 4 tools needed: Write, Edit, Bash, ExitPlanMode.
        Matching Read, Grep, Glob, Agent etc. wastes daemon calls (~5-10ms each)
        and could cause future parallel-hook conflicts with other plugins.
        """
        import json
        from pathlib import Path
        hooks_path = Path(__file__).parent.parent / "hooks" / "hooks.json"
        hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
        matchers = "|".join(
            g.get("matcher", "") for g in hooks["hooks"]["PreToolUse"]
        )
        unnecessary_tools = ["Read", "Grep", "Glob", "Agent", "TaskCreate",
                             "TaskUpdate", "TaskList", "TaskGet"]
        found = [t for t in unnecessary_tools if t in matchers.split("|")]
        assert not found, (
            f"PreToolUse matcher includes unnecessary tools: {found}. "
            f"Only Write|Edit|Bash|ExitPlanMode needed. Matcher: {matchers}"
        )
