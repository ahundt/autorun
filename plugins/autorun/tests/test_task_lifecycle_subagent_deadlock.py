"""Tests for the subagent-task-lifecycle deadlock fix.

Bug: When a parent session spawned child agents via the Agent tool and those
agents completed, SubagentStop fired in the parent's context. The Stop chain
blocked SubagentStop because the parent had in_progress tasks — creating an
infinite deadlock where parent could never receive child results.

Fix summary:
- Fix 1: handle_stop() returns None immediately for SubagentStop events
- Fix 2: "delegated" status added to NON_BLOCKING_STATUSES (explicit-only)
- Fix 3: Stop and SessionStart messages mention the delegate option
- Fix 4: SessionStart injection includes delegated tasks (🤝) for follow-up

This file is self-contained and deletable — all classes are bracketed by the
fix they verify. Regression tests for auto-resume and Gemini compat are also
here to keep the complete picture in one file.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root / "src"))

from autorun.core import EventContext, ThreadSafeDB
from autorun.task_lifecycle import TaskLifecycle, TaskLifecycleConfig
from autorun.session_manager import SessionStateManager
from autorun import session_manager as _sm_module
from autorun.session_manager import _reset_for_testing


# ── Shared fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def isolated_session(tmp_path):
    """Redirect session state to a temp directory so tests don't collide."""
    temp_dir = tmp_path / "sessions"
    temp_dir.mkdir(parents=True, exist_ok=True)
    _reset_for_testing()
    mgr = SessionStateManager(state_dir=temp_dir)
    _sm_module._manager = mgr
    _sm_module._store = mgr._store
    yield mgr
    _reset_for_testing()


@pytest.fixture
def isolated_config(tmp_path):
    """Isolated TaskLifecycleConfig pointing at tmp storage."""
    return TaskLifecycleConfig(
        enabled=True,
        storage_dir=tmp_path / "task_lifecycle",
        max_resume_tasks=10,
    )


def _stop_ctx(session_id: str, event: str = "Stop", cli_type: str = "claude") -> EventContext:
    """Build a minimal Stop/SubagentStop EventContext."""
    ctx = EventContext(
        session_id=session_id,
        event=event,
        prompt="",
        tool_name="",
        tool_input={},
        tool_result="",
        session_transcript=[],
        store=ThreadSafeDB(),
    )
    ctx.autorun_active = False
    ctx.autorun_stage = EventContext.STAGE_INACTIVE
    if cli_type == "gemini":
        ctx._cli_type = "gemini"
    return ctx


def _session_start_ctx(session_id: str, cli_type: str = "claude") -> EventContext:
    """Build a minimal SessionStart EventContext."""
    ctx = EventContext(
        session_id=session_id,
        event="SessionStart",
        prompt="",
        tool_name="",
        tool_input={},
        tool_result="",
        session_transcript=[],
        store=ThreadSafeDB(),
    )
    ctx.autorun_active = False
    ctx.autorun_stage = EventContext.STAGE_INACTIVE
    if cli_type == "gemini":
        ctx._cli_type = "gemini"
    return ctx


# ── Fix 1: SubagentStop must NEVER block ──────────────────────────────────────

class TestSubagentStopNeverBlocks:
    """Fix 1: SubagentStop returns None regardless of task state."""

    def test_subagent_stop_allowed_with_inprogress_tasks(self, isolated_session, isolated_config):
        mgr = TaskLifecycle(session_id="sub-inprog", config=isolated_config)
        mgr.create_task("1", {"subject": "Task A"}, "created")
        mgr.update_task("1", {"status": "in_progress"}, "started")
        ctx = _stop_ctx("sub-inprog", event="SubagentStop")
        result = mgr.handle_stop(ctx)
        assert result is None, "SubagentStop must not block even when tasks are in_progress"

    def test_subagent_stop_allowed_with_pending_tasks(self, isolated_session, isolated_config):
        mgr = TaskLifecycle(session_id="sub-pending", config=isolated_config)
        mgr.create_task("1", {"subject": "Task A"}, "created")
        # task stays "pending"
        ctx = _stop_ctx("sub-pending", event="SubagentStop")
        result = mgr.handle_stop(ctx)
        assert result is None, "SubagentStop must not block even when tasks are pending"

    def test_subagent_stop_allowed_with_no_tasks(self, isolated_session, isolated_config):
        mgr = TaskLifecycle(session_id="sub-empty", config=isolated_config)
        ctx = _stop_ctx("sub-empty", event="SubagentStop")
        result = mgr.handle_stop(ctx)
        assert result is None

    def test_stop_still_blocks_with_inprogress_tasks(self, isolated_session, isolated_config):
        """[REGRESSION] Parent Stop is still blocked when tasks are in_progress."""
        mgr = TaskLifecycle(session_id="stop-inprog", config=isolated_config)
        mgr.create_task("1", {"subject": "Task A"}, "created")
        mgr.update_task("1", {"status": "in_progress"}, "started")
        ctx = _stop_ctx("stop-inprog", event="Stop")
        result = mgr.handle_stop(ctx)
        assert result is not None, "Stop must still block when tasks are in_progress"
        assert result.get("continue") is True

    def test_stop_allowed_when_all_tasks_complete(self, isolated_session, isolated_config):
        """[REGRESSION] Parent Stop is allowed when all tasks are done."""
        mgr = TaskLifecycle(session_id="stop-done", config=isolated_config)
        mgr.create_task("1", {"subject": "Task A"}, "created")
        mgr.update_task("1", {"status": "completed"}, "done")
        ctx = _stop_ctx("stop-done", event="Stop")
        result = mgr.handle_stop(ctx)
        assert result is None, "Stop must allow when all tasks completed"


# ── Fix 2: "delegated" status is non-blocking ─────────────────────────────────

class TestDelegatedStatus:
    """Fix 2: 'delegated' is an explicit non-blocking status."""

    def test_delegated_in_non_blocking_statuses(self):
        assert "delegated" in TaskLifecycle.NON_BLOCKING_STATUSES

    def test_delegated_not_prunable(self):
        assert "delegated" not in TaskLifecycle.PRUNABLE_STATUSES

    def test_delegated_task_does_not_block_stop(self, isolated_session, isolated_config):
        mgr = TaskLifecycle(session_id="del-stop", config=isolated_config)
        mgr.create_task("1", {"subject": "Task A"}, "created")
        mgr.update_task("1", {"status": "delegated"}, "delegated")
        ctx = _stop_ctx("del-stop", event="Stop")
        result = mgr.handle_stop(ctx)
        assert result is None, "Stop must be allowed when only task is delegated"

    def test_delegated_not_returned_by_get_incomplete_tasks(self, isolated_session, isolated_config):
        mgr = TaskLifecycle(session_id="del-incomplete", config=isolated_config)
        mgr.create_task("1", {"subject": "Task A"}, "created")
        mgr.update_task("1", {"status": "delegated"}, "delegated")
        incomplete = mgr.get_incomplete_tasks(exclude_blocking=True)
        assert not any(t["id"] == "1" for t in incomplete)

    def test_ghost_task_can_be_set_to_delegated(self, isolated_session, isolated_config):
        """Ghost task protection allows transition to delegated (it's a terminal status)."""
        mgr = TaskLifecycle(session_id="ghost-del", config=isolated_config)
        # No create_task — this is a ghost (unknown ID)
        result = mgr.update_task("999", {"status": "delegated"}, "delegated")
        assert result != "ghost_skip", "Ghost tasks may transition to delegated (terminal)"
        tasks = mgr.tasks
        assert tasks["999"]["status"] == "delegated"

    def test_ghost_task_cannot_be_set_to_inprogress(self, isolated_session, isolated_config):
        """[REGRESSION] Ghost task protection still blocks in_progress."""
        mgr = TaskLifecycle(session_id="ghost-ip", config=isolated_config)
        result = mgr.update_task("999", {"status": "in_progress"}, "start")
        assert result == "ghost_skip"
        tasks = mgr.tasks
        assert tasks["999"]["status"] == "ignored"

    def test_inprogress_still_blocks_stop(self, isolated_session, isolated_config):
        """[REGRESSION] in_progress tasks still block Stop."""
        mgr = TaskLifecycle(session_id="ip-block", config=isolated_config)
        mgr.create_task("1", {"subject": "Task A"}, "created")
        mgr.update_task("1", {"status": "in_progress"}, "started")
        incomplete = mgr.get_incomplete_tasks(exclude_blocking=True)
        assert len(incomplete) == 1

    def test_mixed_delegated_and_inprogress_blocks_stop(self, isolated_session, isolated_config):
        """Stop blocks if there is any in_progress task alongside a delegated one."""
        mgr = TaskLifecycle(session_id="mixed-block", config=isolated_config)
        mgr.create_task("1", {"subject": "In progress task"}, "created")
        mgr.create_task("2", {"subject": "Delegated task"}, "created")
        mgr.update_task("1", {"status": "in_progress"}, "started")
        mgr.update_task("2", {"status": "delegated"}, "delegated")
        ctx = _stop_ctx("mixed-block", event="Stop")
        result = mgr.handle_stop(ctx)
        assert result is not None, "Stop must block when in_progress tasks remain alongside delegated"


# ── Fix 3: Stop message mentions delegate option ──────────────────────────────

class TestStopMessageIncludesDelegateOption:
    """Fix 3: AI is informed about the 'delegated' escape hatch."""

    def test_stop_message_mentions_delegated(self, isolated_session, isolated_config):
        mgr = TaskLifecycle(session_id="msg-stop", config=isolated_config)
        mgr.create_task("1", {"subject": "Task A"}, "created")
        mgr.update_task("1", {"status": "in_progress"}, "started")
        ctx = _stop_ctx("msg-stop", event="Stop")
        result = mgr.handle_stop(ctx)
        assert result is not None
        msg = result.get("systemMessage", "")
        assert "delegated" in msg.lower(), f"Stop message must mention 'delegated'. Got: {msg[:200]}"

    def test_session_start_message_mentions_delegated(self, isolated_session, isolated_config):
        mgr = TaskLifecycle(session_id="msg-ss", config=isolated_config)
        mgr.create_task("1", {"subject": "Task A"}, "created")
        mgr.update_task("1", {"status": "in_progress"}, "started")
        ctx = _session_start_ctx("msg-ss")
        result = mgr.handle_session_start(ctx)
        assert result is not None
        msg = result.get("systemMessage", "")
        assert "delegated" in msg.lower(), f"SessionStart message must mention 'delegated'. Got: {msg[:200]}"


# ── Fix 4: SessionStart shows delegated tasks for follow-up ───────────────────

class TestDelegatedTasksInSessionStart:
    """Fix 4: Delegated tasks appear in SessionStart injection with 🤝 icon."""

    def test_delegated_shown_alongside_inprogress(self, isolated_session, isolated_config):
        mgr = TaskLifecycle(session_id="ss-mixed", config=isolated_config)
        mgr.create_task("1", {"subject": "In-progress task"}, "created")
        mgr.create_task("2", {"subject": "Delegated task"}, "created")
        mgr.update_task("1", {"status": "in_progress"}, "started")
        mgr.update_task("2", {"status": "delegated"}, "delegated")
        ctx = _session_start_ctx("ss-mixed")
        result = mgr.handle_session_start(ctx)
        assert result is not None
        msg = result.get("systemMessage", "")
        assert "🤝" in msg, f"Delegated task should appear with 🤝 icon. Got: {msg[:300]}"

    def test_delegated_only_triggers_session_start(self, isolated_session, isolated_config):
        """SessionStart fires even when only delegated tasks exist (no in_progress)."""
        mgr = TaskLifecycle(session_id="ss-del-only", config=isolated_config)
        mgr.create_task("1", {"subject": "Delegated task"}, "created")
        mgr.update_task("1", {"status": "delegated"}, "delegated")
        ctx = _session_start_ctx("ss-del-only")
        result = mgr.handle_session_start(ctx)
        assert result is not None, "SessionStart must fire when delegated tasks exist"
        assert "🤝" in result.get("systemMessage", "")

    def test_no_delegated_no_inprogress_no_injection(self, isolated_session, isolated_config):
        """[REGRESSION] No injection when all tasks complete."""
        mgr = TaskLifecycle(session_id="ss-clean", config=isolated_config)
        mgr.create_task("1", {"subject": "Done task"}, "created")
        mgr.update_task("1", {"status": "completed"}, "done")
        ctx = _session_start_ctx("ss-clean")
        result = mgr.handle_session_start(ctx)
        assert result is None

    def test_completed_tasks_not_shown_in_session_start(self, isolated_session, isolated_config):
        """[REGRESSION] Completed tasks don't appear in SessionStart."""
        mgr = TaskLifecycle(session_id="ss-comp", config=isolated_config)
        mgr.create_task("1", {"subject": "Done task"}, "created")
        mgr.update_task("1", {"status": "completed"}, "done")
        ctx = _session_start_ctx("ss-comp")
        result = mgr.handle_session_start(ctx)
        assert result is None


# ── Schema v3 migration ───────────────────────────────────────────────────────

class TestSchemaV3Migration:
    """Schema version bumped to 3; v2 state migrates cleanly (no-op migration)."""

    def test_schema_version_is_3(self):
        assert TaskLifecycle.SCHEMA_VERSION == 3

    def test_v2_state_migrates_to_v3(self, isolated_session, isolated_config):
        from autorun.session_manager import session_state
        mgr = TaskLifecycle(session_id="migrate-v2", config=isolated_config)
        # Manually write v2 state
        with session_state(mgr.global_key) as state:
            state["schema_version"] = 2
            state["tasks"] = {}
        # Access tasks to trigger migration
        _ = mgr.tasks
        with session_state(mgr.global_key) as state:
            assert state["schema_version"] == 3, "State should be migrated to v3"

    def test_v1_state_migrates_to_v3(self, isolated_session, isolated_config):
        from autorun.session_manager import session_state
        mgr = TaskLifecycle(session_id="migrate-v1", config=isolated_config)
        with session_state(mgr.global_key) as state:
            state["schema_version"] = 1
            state["tasks"] = {}
        _ = mgr.tasks
        with session_state(mgr.global_key) as state:
            assert state["schema_version"] == 3


# ── Regression: auto-resume must be preserved ─────────────────────────────────

class TestRegressionAutoResume:
    """[REGRESSION] Auto-resume via Stop/SessionStart must still work."""

    def test_session_start_injects_inprogress_tasks(self, isolated_session, isolated_config):
        mgr = TaskLifecycle(session_id="ar-ss", config=isolated_config)
        mgr.create_task("1", {"subject": "Research task"}, "created")
        mgr.update_task("1", {"status": "in_progress"}, "started")
        ctx = _session_start_ctx("ar-ss")
        result = mgr.handle_session_start(ctx)
        assert result is not None
        assert "Research task" in result.get("systemMessage", "")

    def test_session_start_skips_if_all_complete(self, isolated_session, isolated_config):
        mgr = TaskLifecycle(session_id="ar-done", config=isolated_config)
        mgr.create_task("1", {"subject": "Done task"}, "created")
        mgr.update_task("1", {"status": "completed"}, "done")
        ctx = _session_start_ctx("ar-done")
        result = mgr.handle_session_start(ctx)
        assert result is None

    def test_three_stage_reset_on_stop_block(self, isolated_session, isolated_config):
        """[REGRESSION] Three-stage system resets from STAGE_2_COMPLETED when blocked."""
        mgr = TaskLifecycle(session_id="ar-3s", config=isolated_config)
        mgr.create_task("1", {"subject": "Task A"}, "created")
        mgr.update_task("1", {"status": "in_progress"}, "started")
        ctx = _stop_ctx("ar-3s", event="Stop")
        ctx.autorun_stage = EventContext.STAGE_2_COMPLETED
        mgr.handle_stop(ctx)
        assert ctx.autorun_stage == EventContext.STAGE_2, "Stage must reset from STAGE_2_COMPLETED when stop is blocked"

    def test_stop_blocks_with_pending_tasks(self, isolated_session, isolated_config):
        """[REGRESSION] Stop also blocks when tasks are pending."""
        mgr = TaskLifecycle(session_id="ar-pending", config=isolated_config)
        mgr.create_task("1", {"subject": "Pending task"}, "created")
        # task stays "pending"
        ctx = _stop_ctx("ar-pending", event="Stop")
        result = mgr.handle_stop(ctx)
        assert result is not None, "Stop must block when tasks are pending"


# ── Gemini CLI compatibility ───────────────────────────────────────────────────

class TestGeminiCompatibility:
    """Gemini CLI: Fix 1 is invisible; Fixes 2-4 work identically for both CLIs."""

    def test_gemini_event_map_has_no_subagentstop(self):
        """Gemini has no SubagentStop — GEMINI_EVENT_MAP does not map it."""
        from autorun.core import GEMINI_EVENT_MAP
        assert "SubagentStop" not in GEMINI_EVENT_MAP

    def test_gemini_after_agent_maps_to_stop(self):
        """AfterAgent → Stop: Gemini behavior is UNCHANGED (known gap)."""
        from autorun.core import GEMINI_EVENT_MAP
        assert GEMINI_EVENT_MAP.get("AfterAgent") == "Stop"

    def test_gemini_stop_still_blocks_inprogress(self, isolated_session, isolated_config):
        """[REGRESSION] Gemini Stop with in_progress tasks still blocks."""
        mgr = TaskLifecycle(session_id="gem-block", config=isolated_config)
        mgr.create_task("1", {"subject": "Task A"}, "created")
        mgr.update_task("1", {"status": "in_progress"}, "started")
        ctx = _stop_ctx("gem-block", event="Stop", cli_type="gemini")
        result = mgr.handle_stop(ctx)
        assert result is not None, "Gemini Stop must still block in_progress tasks"

    def test_gemini_delegated_task_does_not_block_stop(self, isolated_session, isolated_config):
        """Fix 2 is shared code: delegated is non-blocking for Gemini too."""
        mgr = TaskLifecycle(session_id="gem-del", config=isolated_config)
        mgr.create_task("1", {"subject": "Task A"}, "created")
        mgr.update_task("1", {"status": "delegated"}, "delegated")
        ctx = _stop_ctx("gem-del", event="Stop", cli_type="gemini")
        result = mgr.handle_stop(ctx)
        assert result is None, "Gemini Stop must allow when only task is delegated"

    def test_gemini_delegated_shown_in_session_start(self, isolated_session, isolated_config):
        """Fix 4 is shared code: delegated appears in SessionStart for Gemini."""
        mgr = TaskLifecycle(session_id="gem-ss-del", config=isolated_config)
        mgr.create_task("1", {"subject": "Gemini task"}, "created")
        mgr.update_task("1", {"status": "delegated"}, "delegated")
        ctx = _session_start_ctx("gem-ss-del", cli_type="gemini")
        result = mgr.handle_session_start(ctx)
        assert result is not None
        assert "🤝" in result.get("systemMessage", "")

    def test_gemini_stop_message_includes_delegate_option(self, isolated_session, isolated_config):
        """Fix 3 is shared code: Gemini stop message mentions delegated."""
        mgr = TaskLifecycle(session_id="gem-msg", config=isolated_config)
        mgr.create_task("1", {"subject": "Task A"}, "created")
        mgr.update_task("1", {"status": "in_progress"}, "started")
        ctx = _stop_ctx("gem-msg", event="Stop", cli_type="gemini")
        result = mgr.handle_stop(ctx)
        assert result is not None
        assert "delegated" in result.get("systemMessage", "").lower()

    def test_gemini_after_agent_blocked_known_gap(self, isolated_session, isolated_config):
        """[KNOWN GAP] AfterAgent → Stop still blocks if tasks in_progress.
        This is the Gemini-specific known gap: no SubagentStop equivalent exists,
        so Gemini will still deadlock when using parallel agent delegation with
        in_progress tasks. Tracked here as expected behavior, not a regression.
        """
        from autorun.core import GEMINI_EVENT_MAP
        assert GEMINI_EVENT_MAP.get("AfterAgent") == "Stop"
        mgr = TaskLifecycle(session_id="gem-gap", config=isolated_config)
        mgr.create_task("1", {"subject": "Task A"}, "created")
        mgr.update_task("1", {"status": "in_progress"}, "started")
        # AfterAgent → Stop → handle_stop() with event="Stop" (not SubagentStop)
        ctx = _stop_ctx("gem-gap", event="Stop", cli_type="gemini")
        result = mgr.handle_stop(ctx)
        assert result is not None  # still blocks — known gap, deferred to separate PR
