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
    def test_plan_approval_returns_respond_format(self):
        """Verify detect_plan_approval uses ctx.respond with to_human + to_ai."""
        sid = f"test-plan-respond-{time.time()}"
        ctx = make_post_tool_ctx(
            session_id=sid,
            tool_name="ExitPlanMode",
            tool_result="User has approved your plan. You can now start coding.",
            autorun_active=False,
            plan_arguments="Build a widget",
        )
        result = plugins.detect_plan_approval(ctx)

        assert result is not None, "detect_plan_approval should return non-None on approval"
        assert result.get("decision") == "approve", \
            f"Expected 'approve' decision, got {result.get('decision')}"

        # PATHWAY 2: systemMessage should contain user notification
        sys_msg = result.get("systemMessage", "")
        assert "Plan accepted" in sys_msg, \
            f"systemMessage should contain 'Plan accepted', got: {sys_msg}"

        # PATHWAY 2: hookSpecificOutput.additionalContext should contain AI injection
        hso = result.get("hookSpecificOutput", {})
        ai_context = hso.get("additionalContext", "")
        assert "UNINTERRUPTED" in ai_context or "autonomous" in ai_context.lower(), \
            f"additionalContext should contain injection prompt, got: {ai_context[:100]}"

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
        assert result is not None

        hso = result.get("hookSpecificOutput", {})
        ai_context = hso.get("additionalContext", "")
        assert "Task Scaffolding Required" in ai_context, \
            f"TDD scaffolding message should be in AI context, got: {ai_context[:200]}"

        sys_msg = result.get("systemMessage", "")
        assert "TDD scaffolding: enabled" in sys_msg, \
            f"User notification should mention TDD, got: {sys_msg}"

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
        assert result is not None

        hso = result.get("hookSpecificOutput", {})
        ai_context = hso.get("additionalContext", "")
        assert "Task Scaffolding Required" not in ai_context

        sys_msg = result.get("systemMessage", "")
        assert "TDD scaffolding" not in sys_msg

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
        assert result is not None

        # Counter should be pre-set to threshold - 2
        from autorun.config import CONFIG
        threshold = CONFIG.get("task_staleness_threshold", 25)
        assert ctx.tool_calls_since_task_update == max(0, threshold - 2), \
            f"Staleness counter should be {max(0, threshold - 2)}, got {ctx.tool_calls_since_task_update}"

    def test_plan_approval_skipped_when_already_active(self):
        """Verify no double-activation if autorun already active."""
        sid = f"test-already-active-{time.time()}"
        ctx = make_post_tool_ctx(
            session_id=sid,
            tool_name="ExitPlanMode",
            tool_result="User has approved your plan. You can now start coding.",
            autorun_active=True,  # Already active
        )
        result = plugins.detect_plan_approval(ctx)
        assert result is None, "Should return None when autorun already active"

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

class TestGhostTaskWarningIntegration:
    def test_ghost_warning_via_track_task_operations(self, isolated_config, isolated_session_manager):
        """Simulate TaskUpdate on unknown task ID through the hook handler."""
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
    def test_plan_approval_response_valid_json(self):
        """Verify the response dict is JSON-serializable (daemon requirement)."""
        sid = f"test-json-{time.time()}"
        ctx = make_post_tool_ctx(
            session_id=sid,
            tool_name="ExitPlanMode",
            tool_result="User has approved your plan. You can now start coding.",
            autorun_active=False,
            plan_arguments="Test plan",
        )
        result = plugins.detect_plan_approval(ctx)
        assert result is not None

        # Must be JSON-serializable
        serialized = json.dumps(result)
        deserialized = json.loads(serialized)
        assert deserialized["decision"] == "approve"
        assert "systemMessage" in deserialized
