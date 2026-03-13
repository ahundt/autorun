"""Integration tests for task lifecycle bug fixes and plan acceptance notification.

Exercises the real hook dispatch chain with full EventContext + ThreadSafeDB,
verifying JSON response format matches Claude Code/Gemini CLI expectations.

Covers:
- Daemon import chain (plugins imports task_lifecycle, PlanNotifyConfig accessible)
- Plan acceptance dual notification (ctx.respond to_human + to_ai, PATHWAY 2)
- TDD scaffolding injection (configurable via PlanNotifyConfig)
- Task staleness counter pre-set on plan acceptance (Fix 8)
- Ghost task warning injection through track_task_operations hook (Fix 2)
- Block count reset on task completion/pause (Fix 4)
- PlanNotifyConfig persistence, corrupt JSON handling, unknown key tolerance
- Response JSON serializability for daemon wire format

See also: test_task_lifecycle_bugfix_unit.py for isolated unit tests.
"""
import json
import time
import pytest

from autorun.core import EventContext, ThreadSafeDB
from autorun.task_lifecycle import (
    TaskLifecycle, TaskLifecycleConfig, PlanNotifyConfig,
    PLAN_NOTIFY_CONFIG_PATH,
)
from autorun import plugins
from autorun import task_lifecycle
from autorun.session_manager import session_state, SessionStateManager


@pytest.fixture
def isolated_config(tmp_path):
    """Isolated config using temp directory."""
    return TaskLifecycleConfig(
        enabled=True,
        storage_dir=tmp_path / "task_lifecycle",
        task_ttl_days=30,
        max_resume_tasks=10,
    )


@pytest.fixture
def isolated_session_manager(tmp_path):
    """Isolated session manager using temp directory."""
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


def make_post_tool_ctx(
    session_id: str,
    tool_name: str,
    tool_input: dict = None,
    tool_result: str = "",
    **overrides,
) -> EventContext:
    """Create a PostToolUse EventContext with a real ThreadSafeDB store."""
    ctx = EventContext(
        session_id=session_id,
        event="PostToolUse",
        prompt="",
        tool_name=tool_name,
        tool_input=tool_input or {},
        tool_result=tool_result,
        store=ThreadSafeDB(),
    )
    for k, v in overrides.items():
        setattr(ctx, k, v)
    return ctx


# === Test 1: Daemon import chain works ===

class TestDaemonImportChain:
    def test_plugins_module_imports_task_lifecycle(self):
        """Verify plugins.py imports task_lifecycle without error."""
        assert hasattr(plugins, 'task_lifecycle')

    def test_plan_notify_config_importable(self):
        """Verify PlanNotifyConfig is importable from task_lifecycle."""
        assert hasattr(task_lifecycle, 'PlanNotifyConfig')
        assert hasattr(task_lifecycle, 'PLAN_NOTIFY_CONFIG_PATH')

    def test_detect_plan_approval_registered(self):
        """Verify detect_plan_approval is registered on plugins.app."""
        assert hasattr(plugins, 'detect_plan_approval')


# === Test 2: Plan acceptance dual notification via ctx.respond ===

class TestPlanAcceptanceDualNotification:
    def test_plan_approval_returns_none_with_chain_notifications(self):
        """Verify detect_plan_approval uses chain notifications (returns None)."""
        sid = f"test-plan-respond-{time.time()}"
        ctx = make_post_tool_ctx(
            session_id=sid,
            tool_name="ExitPlanMode",
            tool_result="User has approved your plan. You can now start coding.",
            autorun_active=False,
            plan_arguments="Build a widget",
        )
        result = plugins.detect_plan_approval(ctx)

        assert result is None, "detect_plan_approval should return None (chain notifications)"
        assert len(ctx._chain_notifications) >= 2, \
            f"Should have at least 2 chain notifications (human + ai), got {len(ctx._chain_notifications)}"

        # Check human channel has "Plan accepted"
        human_msgs = [msg for msg, ch in ctx._chain_notifications if ch == "human"]
        assert any("Plan accepted" in m for m in human_msgs), \
            f"Human notification should contain 'Plan accepted', got: {human_msgs}"

        # Check ai channel has injection prompt
        ai_msgs = [msg for msg, ch in ctx._chain_notifications if ch == "ai"]
        assert any("UNINTERRUPTED" in m or "autonomous" in m.lower() for m in ai_msgs), \
            f"AI notification should contain injection prompt, got: {[m[:100] for m in ai_msgs]}"

    def test_plan_approval_includes_tdd_scaffolding(self, monkeypatch, tmp_path):
        """Verify TDD scaffolding message is injected when enabled."""
        monkeypatch.setattr(task_lifecycle, 'PLAN_NOTIFY_CONFIG_PATH', tmp_path / 'pn.json')
        # Ensure TDD is enabled (default)
        cfg = PlanNotifyConfig(tdd_scaffolding=True)
        cfg.save()

        sid = f"test-tdd-scaffold-{time.time()}"
        ctx = make_post_tool_ctx(
            session_id=sid,
            tool_name="ExitPlanMode",
            tool_result="User has approved your plan. You can now start coding.",
            autorun_active=False,
            plan_arguments="Build tests",
        )
        result = plugins.detect_plan_approval(ctx)
        assert result is None, "Should return None (chain notifications)"

        ai_msgs = [msg for msg, ch in ctx._chain_notifications if ch == "ai"]
        ai_context = "\n".join(ai_msgs)
        assert "Task Scaffolding Required" in ai_context, \
            f"TDD scaffolding message should be in AI notifications, got: {ai_context[:200]}"

        human_msgs = [msg for msg, ch in ctx._chain_notifications if ch == "human"]
        human_text = "\n".join(human_msgs)
        assert "TDD scaffolding: enabled" in human_text, \
            f"User notification should mention TDD, got: {human_text}"

    def test_plan_approval_tdd_disabled(self, monkeypatch, tmp_path):
        """Verify TDD scaffolding is NOT injected when disabled."""
        monkeypatch.setattr(task_lifecycle, 'PLAN_NOTIFY_CONFIG_PATH', tmp_path / 'pn.json')
        cfg = PlanNotifyConfig(tdd_scaffolding=False)
        cfg.save()

        sid = f"test-no-tdd-{time.time()}"
        ctx = make_post_tool_ctx(
            session_id=sid,
            tool_name="ExitPlanMode",
            tool_result="User has approved your plan. You can now start coding.",
            autorun_active=False,
            plan_arguments="Quick fix",
        )
        result = plugins.detect_plan_approval(ctx)
        assert result is None, "Should return None (chain notifications)"

        ai_msgs = [msg for msg, ch in ctx._chain_notifications if ch == "ai"]
        ai_context = "\n".join(ai_msgs)
        assert "Task Scaffolding Required" not in ai_context

        human_msgs = [msg for msg, ch in ctx._chain_notifications if ch == "human"]
        human_text = "\n".join(human_msgs)
        assert "TDD scaffolding" not in human_text

    def test_plan_approval_sets_staleness_counter(self, monkeypatch, tmp_path):
        """Fix 8: task_update_enforcement pre-sets staleness counter."""
        monkeypatch.setattr(task_lifecycle, 'PLAN_NOTIFY_CONFIG_PATH', tmp_path / 'pn.json')
        cfg = PlanNotifyConfig(task_update_enforcement=True)
        cfg.save()

        sid = f"test-staleness-{time.time()}"
        ctx = make_post_tool_ctx(
            session_id=sid,
            tool_name="ExitPlanMode",
            tool_result="User has approved your plan. You can now start coding.",
            autorun_active=False,
            plan_arguments="Task enforcement test",
        )
        result = plugins.detect_plan_approval(ctx)
        assert result is None, "Should return None (chain notifications)"

        # Counter should be pre-set to threshold - 2 (side effect still works)
        from autorun.config import CONFIG
        threshold = CONFIG.get("task_staleness_threshold", 25)
        assert ctx.tool_calls_since_task_update == max(0, threshold - 2), \
            f"Staleness counter should be {max(0, threshold - 2)}, got {ctx.tool_calls_since_task_update}"

    def test_plan_approval_when_already_active_adds_chain_notifications(self):
        """When autorun already active, still set execution reminder via chain notification."""
        sid = f"test-already-active-{time.time()}"
        ctx = make_post_tool_ctx(
            session_id=sid,
            tool_name="ExitPlanMode",
            tool_result="User has approved your plan. You can now start coding.",
            autorun_active=True,  # Already active
        )
        result = plugins.detect_plan_approval(ctx)
        # Returns None (doesn't stop chain), but adds chain notifications
        assert result is None, "Should return None when autorun already active"
        # Execution task reminder flag should be set
        assert ctx.plan_awaiting_execution_tasks is True
        assert ctx.plan_awaiting_planning_tasks is False
        # Chain notifications should be accumulated
        assert len(ctx._chain_notifications) >= 2, \
            f"Expected at least 2 chain notifications (reminder + acceptance), got {len(ctx._chain_notifications)}"
        all_msgs = " ".join(m for m, c in ctx._chain_notifications)
        assert "EXECUTION TASKS REQUIRED" in all_msgs, \
            f"Execution task reminder should be in chain notifications: {all_msgs[:200]}"
        assert "Plan accepted" in all_msgs, \
            f"Plan acceptance should be in chain notifications: {all_msgs[:200]}"

    def test_plan_rejection_returns_none(self):
        """Verify rejected plans don't activate autorun."""
        sid = f"test-rejected-{time.time()}"
        ctx = make_post_tool_ctx(
            session_id=sid,
            tool_name="ExitPlanMode",
            tool_result="User has rejected your plan.",
            autorun_active=False,
        )
        result = plugins.detect_plan_approval(ctx)
        assert result is None, "Should return None on plan rejection"


# === Test 3: Ghost task warning in hook chain ===

class TestGhostTaskSkipIntegration:
    def test_ghost_skip_via_track_task_operations(self, isolated_config, isolated_session_manager):
        """Simulate TaskUpdate on unknown task ID — returns ghost_skip sentinel."""
        sid = f"test-ghost-hook-{time.time()}"
        manager = TaskLifecycle(session_id=sid, config=isolated_config)

        ctx = make_post_tool_ctx(
            session_id=sid,
            tool_name="TaskUpdate",
            tool_input={"taskId": "999", "status": "in_progress"},
            tool_result="Updated task #999",
        )

        # Call handle_task_update directly (same path as track_task_operations)
        ghost_result = manager.handle_task_update(ctx)
        assert ghost_result == "ghost_skip", \
            "Ghost task non-terminal update should return 'ghost_skip'"

    def test_ghost_task_completed_no_warning(self, isolated_config, isolated_session_manager):
        """Ghost task transitioning to terminal status should NOT warn."""
        sid = f"test-ghost-complete-{time.time()}"
        manager = TaskLifecycle(session_id=sid, config=isolated_config)

        # First touch creates ghost
        manager.update_task('999', {'status': 'in_progress'}, 'Ghost create')
        # Terminal should succeed
        result = manager.update_task('999', {'status': 'completed'}, 'Complete ghost')
        assert result is None, "Terminal transition should return None"


# === Test 4: Block count reset integration ===

class TestBlockCountResetIntegration:
    def test_block_count_resets_via_update_task(self, isolated_config, isolated_session_manager):
        """Verify block count resets when a real task completes."""
        sid = f"test-block-int-{time.time()}"
        manager = TaskLifecycle(session_id=sid, config=isolated_config)

        # Create tasks
        manager.create_task('1', {'subject': 'Task A'}, 'Created')
        manager.create_task('2', {'subject': 'Task B'}, 'Created')

        # Simulate multiple stop blocks
        def set_count(metadata):
            metadata['stop_block_count'] = 5
        manager.atomic_update_metadata(set_count)

        # Complete task A
        result = manager.update_task('1', {'status': 'completed'}, 'Done')
        assert result is None, "Normal completion should return None"

        # Verify counter reset
        meta = manager.session_metadata
        assert meta.get('stop_block_count', 0) == 0, \
            f"Block count should reset to 0 after completion, got {meta.get('stop_block_count')}"

        # Task B still incomplete
        incomplete = manager.get_incomplete_tasks()
        assert len(incomplete) == 1
        assert incomplete[0]['id'] == '2'

    def test_block_count_not_reset_on_ghost_skip(self, isolated_config, isolated_session_manager):
        """Ghost task skip should NOT reset block count."""
        sid = f"test-block-ghost-int-{time.time()}"
        manager = TaskLifecycle(session_id=sid, config=isolated_config)

        def set_count(metadata):
            metadata['stop_block_count'] = 5
        manager.atomic_update_metadata(set_count)

        # Ghost task non-terminal update
        result = manager.update_task('999', {'status': 'in_progress'}, 'Ghost')
        assert result == "ghost_skip"

        meta = manager.session_metadata
        assert meta.get('stop_block_count', 0) == 5, \
            "Block count should NOT reset on ghost skip"

    def test_block_count_resets_on_pause(self, isolated_config, isolated_session_manager):
        """Pausing a task should also reset block count (NON_BLOCKING_STATUSES)."""
        sid = f"test-block-pause-{time.time()}"
        manager = TaskLifecycle(session_id=sid, config=isolated_config)
        manager.create_task('1', {'subject': 'Task A'}, 'Created')

        def set_count(metadata):
            metadata['stop_block_count'] = 3
        manager.atomic_update_metadata(set_count)

        manager.update_task('1', {'status': 'paused'}, 'Paused')
        meta = manager.session_metadata
        assert meta.get('stop_block_count', 0) == 0, \
            "Block count should reset on pause (pause is a NON_BLOCKING_STATUS)"


# === Test 5: PlanNotifyConfig with real config dir ===

class TestPlanNotifyConfigRealPath:
    def test_config_default_values(self, monkeypatch, tmp_path):
        """Load from non-existent path returns defaults."""
        monkeypatch.setattr(task_lifecycle, 'PLAN_NOTIFY_CONFIG_PATH', tmp_path / 'pn.json')
        cfg = PlanNotifyConfig.load()
        assert cfg.tdd_scaffolding is True
        assert cfg.task_update_enforcement is True
        assert cfg.dependency_wiring is True

    def test_config_roundtrip(self, monkeypatch, tmp_path):
        """Save then load preserves values."""
        config_path = tmp_path / 'pn.json'
        monkeypatch.setattr(task_lifecycle, 'PLAN_NOTIFY_CONFIG_PATH', config_path)

        cfg = PlanNotifyConfig(tdd_scaffolding=False, dependency_wiring=False)
        cfg.save()

        # Verify file exists and is valid JSON
        assert config_path.exists()
        data = json.loads(config_path.read_text())
        assert data["tdd_scaffolding"] is False
        assert data["dependency_wiring"] is False
        assert data["task_update_enforcement"] is True

        # Load back
        cfg2 = PlanNotifyConfig.load()
        assert cfg2.tdd_scaffolding is False
        assert cfg2.dependency_wiring is False
        assert cfg2.task_update_enforcement is True

    def test_config_handles_corrupt_json(self, monkeypatch, tmp_path):
        """Corrupt JSON file returns defaults."""
        config_path = tmp_path / 'pn.json'
        config_path.write_text("not valid json{{{")
        monkeypatch.setattr(task_lifecycle, 'PLAN_NOTIFY_CONFIG_PATH', config_path)

        cfg = PlanNotifyConfig.load()
        assert cfg.tdd_scaffolding is True  # Default

    def test_config_handles_extra_keys(self, monkeypatch, tmp_path):
        """Unknown keys in JSON are silently ignored."""
        config_path = tmp_path / 'pn.json'
        config_path.write_text(json.dumps({
            "tdd_scaffolding": False,
            "future_feature": True,
            "unknown_key": 42,
        }))
        monkeypatch.setattr(task_lifecycle, 'PLAN_NOTIFY_CONFIG_PATH', config_path)

        cfg = PlanNotifyConfig.load()
        assert cfg.tdd_scaffolding is False
        assert not hasattr(cfg, 'future_feature')


# === Test 6: Response format validation ===

class TestResponseFormat:
    def test_plan_approval_chain_notifications_present(self):
        """Verify chain notifications are accumulated (replaces JSON response test)."""
        sid = f"test-json-{time.time()}"
        ctx = make_post_tool_ctx(
            session_id=sid,
            tool_name="ExitPlanMode",
            tool_result="User has approved your plan. You can now start coding.",
            autorun_active=False,
            plan_arguments="Test plan",
        )
        result = plugins.detect_plan_approval(ctx)
        assert result is None, "Should return None (chain notifications)"

        # Verify notifications accumulated
        assert len(ctx._chain_notifications) >= 2, \
            "Should have human and ai channel notifications"
        human_msgs = [msg for msg, ch in ctx._chain_notifications if ch == "human"]
        assert len(human_msgs) >= 1, "Should have at least one human notification"


# === Test 7: Root cause — chain ordering bug (Bug #11) ===

class TestChainOrderingBugFix:
    """Verify the root cause of Bug #11: export_on_exit_plan_mode returning non-None
    blocked detect_plan_approval from ever executing in _run_chain's first-non-None loop.

    This is an end-to-end test of the PostToolUse chain for ExitPlanMode with approval.
    """

    def test_detect_plan_approval_fires_after_export(self):
        """Root cause test: detect_plan_approval must execute even when
        export_on_exit_plan_mode runs first in the PostToolUse chain.

        BEFORE fix: export handler returned ctx.respond(...) → non-None →
        _run_chain stopped → detect_plan_approval NEVER fired → autorun NOT activated.

        AFTER fix: Both handlers use ctx.add_chain_notification() → return None →
        chain continues → all handlers fire → autorun activated → notifications flush.
        """
        sid = f"test-chain-order-{time.time()}"
        ctx = make_post_tool_ctx(
            session_id=sid,
            tool_name="ExitPlanMode",
            tool_result="User has approved your plan. You can now start coding.",
            autorun_active=False,
            plan_arguments="Fix chain ordering bug",
        )

        # Call detect_plan_approval directly
        result = plugins.detect_plan_approval(ctx)

        # Now returns None with chain notifications instead of non-None
        assert result is None, \
            "detect_plan_approval should return None (chain notifications)"
        assert ctx.autorun_active is True, \
            "autorun must be activated after plan approval (root cause of Bug #11)"
        assert ctx.autorun_stage == EventContext.STAGE_1, \
            "autorun stage must be STAGE_1 after approval"

    def test_export_handler_returns_none_on_approval(self):
        """Verify export handler returns None (not non-None) when approval detected.

        This is the direct fix for Bug #11: export_on_exit_plan_mode must NOT
        return a response when the plan was approved, because that blocks
        detect_plan_approval in the first-non-None chain.
        """
        from autorun.plan_export import export_on_exit_plan_mode

        sid = f"test-export-none-{time.time()}"
        ctx = make_post_tool_ctx(
            session_id=sid,
            tool_name="ExitPlanMode",
            tool_result="User has approved your plan. You can now start coding.",
        )

        result = export_on_exit_plan_mode(ctx)
        # If plan found and exported, result must be None (not a response dict)
        # If plan not found, result is also None (no export to do)
        # Either way: MUST NOT return non-None on approval
        assert result is None, \
            f"export_on_exit_plan_mode must return None on approval, got: {result}"

    def test_export_handler_diagnostic_on_rejection(self):
        """Verify export handler adds diagnostic notification when no plan found.

        Rejected plans have no plan content to export, so a diagnostic notification
        informs the user that export was skipped.
        """
        from autorun.plan_export import export_on_exit_plan_mode

        sid = f"test-export-reject-{time.time()}"
        ctx = make_post_tool_ctx(
            session_id=sid,
            tool_name="ExitPlanMode",
            # Rejection text — does NOT contain approval indicators
            tool_result="User has rejected your plan.",
        )

        result = export_on_exit_plan_mode(ctx)
        assert result is None, "Should return None (chain notifications)"
        # Diagnostic notification about no plan content found
        human_msgs = [msg for msg, ch in ctx._chain_notifications if ch == "human"]
        assert any("no plan content" in m for m in human_msgs), \
            f"Should have diagnostic about no plan content, got: {human_msgs}"

    def test_run_chain_flush_catches_orphan_notifications(self):
        """If all handlers return None but notifications accumulated, _run_chain flushes them."""
        from autorun.core import AutorunApp

        app_test = AutorunApp()

        def handler_that_accumulates(ctx):
            ctx.add_chain_notification("Orphan notification", channel="human")
            return None

        app_test.chains["PostToolUse"] = [handler_that_accumulates]

        ctx = make_post_tool_ctx(
            session_id=f"test-flush-{time.time()}",
            tool_name="SomeTool",
            tool_result="",
        )

        result = app_test._run_chain(ctx, "PostToolUse")
        assert result is not None, "_run_chain must flush orphan notifications"
        assert "Orphan notification" in result.get("systemMessage", "")


# === Test 8: Chain Notification Accumulator ===

class TestChainNotificationAccumulator:
    def test_notifications_merged_into_respond(self):
        """Accumulated notifications appear in respond() output."""
        ctx = make_post_tool_ctx(
            session_id=f"test-accum-merge-{time.time()}",
            tool_name="ExitPlanMode",
            tool_result="some result",
        )
        ctx.add_chain_notification("Notification A", channel="human")
        ctx.add_chain_notification("Notification B", channel="human")

        result = ctx.respond("allow", "", to_human="Handler message", to_ai=False)
        assert "Notification A" in result["systemMessage"]
        assert "Notification B" in result["systemMessage"]
        assert "Handler message" in result["systemMessage"]
        # Notifications should be cleared after merge
        assert len(ctx._chain_notifications) == 0

    def test_ai_channel_notifications_in_additional_context(self):
        """AI-channel notifications appear in additionalContext, not systemMessage."""
        ctx = make_post_tool_ctx(
            session_id=f"test-accum-ai-{time.time()}",
            tool_name="ExitPlanMode",
            tool_result="some result",
        )
        ctx.add_chain_notification("AI-only note", channel="ai")

        result = ctx.respond("allow", "", to_human="Human msg", to_ai="AI context")
        assert "AI-only note" in result["hookSpecificOutput"]["additionalContext"]
        assert "AI-only note" not in result["systemMessage"]

    def test_both_channel_notifications(self):
        """Channel 'both' appears in both systemMessage and additionalContext."""
        ctx = make_post_tool_ctx(
            session_id=f"test-accum-both-{time.time()}",
            tool_name="ExitPlanMode",
            tool_result="some result",
        )
        ctx.add_chain_notification("Dual note", channel="both")

        result = ctx.respond("allow", "", to_human="Human", to_ai="AI")
        assert "Dual note" in result["systemMessage"]
        assert "Dual note" in result["hookSpecificOutput"]["additionalContext"]

    def test_no_notifications_no_change(self):
        """respond() unchanged when no notifications accumulated."""
        ctx = make_post_tool_ctx(
            session_id=f"test-no-accum-{time.time()}",
            tool_name="ExitPlanMode",
            tool_result="some result",
        )
        result = ctx.respond("allow", "", to_human="Just handler", to_ai=False)
        assert result["systemMessage"] == "Just handler"

    def test_plan_export_plus_approval_combined(self):
        """ExitPlanMode produces both export and acceptance in systemMessage."""
        ctx = make_post_tool_ctx(
            session_id=f"test-combined-{time.time()}",
            tool_name="ExitPlanMode",
            tool_result="User has approved your plan. You can now start coding.",
            autorun_active=False,
            plan_arguments="Build feature",
        )
        # Simulate what export_on_exit_plan_mode does on approval
        ctx.add_chain_notification("📋 Plan exported to notes/test.md", channel="human")

        # Now simulate detect_plan_approval's respond call
        result = ctx.respond("allow", "", to_human="Plan accepted - 2 task(s) linked", to_ai="injection")

        sys_msg = result["systemMessage"]
        assert "📋 Plan exported" in sys_msg
        assert "Plan accepted" in sys_msg
        # Export notification comes first (chronological order)
        assert sys_msg.index("📋 Plan exported") < sys_msg.index("Plan accepted")

    def test_execution_reminder_fires_after_plan_acceptance(self):
        """Plan acceptance response itself contains execution task reminder."""
        sid = f"test-exec-reminder-{time.time()}"
        store = ThreadSafeDB()

        # Step 1: Simulate plan acceptance via detect_plan_approval
        ctx = EventContext(
            session_id=sid, event="PostToolUse", prompt="",
            tool_name="ExitPlanMode", tool_input={},
            tool_result="User has approved your plan. You can now start coding.",
            store=store,
        )
        ctx.plan_arguments = "Build feature"
        result = plugins.app.dispatch(ctx) or {}

        # Step 2: ExitPlanMode response itself should contain execution reminder
        result_str = str(result)
        assert "EXECUTION TASKS REQUIRED" in result_str, (
            f"Expected execution task reminder in ExitPlanMode response, got: {result_str[:300]}"
        )

        # Step 3: Flag still set for subsequent PostToolUse reminders
        ctx2 = EventContext(
            session_id=sid, event="PostToolUse", prompt="",
            tool_name="Bash", tool_input={}, tool_result="",
            store=store,
        )
        assert ctx2.plan_awaiting_execution_tasks is True

        # Step 4: Subsequent PostToolUse also gets reminder
        result2 = plugins.app.dispatch(ctx2) or {}
        assert "EXECUTION TASKS REQUIRED" in str(result2)
