"""Tests for task system bug fixes (Fixes 1-10) and plan acceptance notification (Fixes 6-8).

TDD approach: tests written BEFORE implementation to verify failures, then fixes applied.
"""
import json
import time
import pytest
from unittest.mock import MagicMock

from autorun.config import CONFIG
from autorun.task_lifecycle import TaskLifecycle, TaskLifecycleConfig
from autorun.session_manager import session_state, SessionStateManager


@pytest.fixture
def isolated_config(tmp_path):
    """Isolated config using temp directory (no impact on production)."""
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


# --- Fix 9: stage2_completion must differ from stage2_message ---

class TestFix9Stage2Completion:
    def test_stage2_completion_differs_from_stage2_message(self):
        assert CONFIG["stage2_completion"] != CONFIG["stage2_message"], \
            "stage2_completion should be a lowercase descriptive, not identical to stage2_message"

    def test_stage2_completion_is_lowercase(self):
        assert not CONFIG["stage2_completion"].isupper(), \
            "stage2_completion should be lowercase descriptive text"


# --- Fix 1: BLOCKING_STATUSES renamed to NON_BLOCKING_STATUSES ---

class TestFix1NonBlockingStatuses:
    def test_non_blocking_statuses_exists(self):
        assert hasattr(TaskLifecycle, 'NON_BLOCKING_STATUSES'), \
            "TaskLifecycle should have NON_BLOCKING_STATUSES attribute"

    def test_blocking_statuses_removed(self):
        assert not hasattr(TaskLifecycle, 'BLOCKING_STATUSES'), \
            "TaskLifecycle should NOT have old BLOCKING_STATUSES attribute"

    def test_non_blocking_statuses_values(self):
        assert TaskLifecycle.NON_BLOCKING_STATUSES == frozenset(
            ["completed", "deleted", "paused", "ignored"])


# --- Fix 2: Ghost task returns sentinel ---

class TestFix2GhostTaskSentinel:
    def test_ghost_task_returns_ghost_skip(self, isolated_config, isolated_session_manager):
        session_id = f"test-ghost-sentinel-{time.time()}"
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)
        result = manager.update_task('999', {'status': 'in_progress'}, 'Ghost update')
        assert result == "ghost_skip", \
            "Ghost task non-terminal update should return 'ghost_skip'"

    def test_ghost_task_terminal_returns_none(self, isolated_config, isolated_session_manager):
        session_id = f"test-ghost-terminal-{time.time()}"
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)
        # First touch creates the ghost
        manager.update_task('999', {'status': 'in_progress'}, 'Ghost create')
        # Terminal status should succeed
        result = manager.update_task('999', {'status': 'completed'}, 'Complete ghost')
        assert result is None, \
            "Ghost task terminal update should return None (success)"

    def test_normal_task_returns_none(self, isolated_config, isolated_session_manager):
        session_id = f"test-normal-{time.time()}"
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)
        manager.create_task('1', {'subject': 'Normal task'}, 'Created')
        result = manager.update_task('1', {'status': 'in_progress'}, 'Start')
        assert result is None, \
            "Normal task update should return None"


# --- Fix 4: Block count resets on task completion ---

class TestFix4BlockCountReset:
    def test_block_count_resets_on_completion(self, isolated_config, isolated_session_manager):
        session_id = f"test-block-reset-{time.time()}"
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)
        manager.create_task('1', {'subject': 'Task A'}, 'Created')

        # Simulate stop blocks
        def set_count(metadata):
            metadata['stop_block_count'] = 3
        manager.atomic_update_metadata(set_count)

        # Complete task should reset counter
        manager.update_task('1', {'status': 'completed'}, 'Done')
        assert manager.session_metadata.get('stop_block_count', 0) == 0, \
            "Block count should reset to 0 on task completion"

    def test_block_count_no_reset_on_ghost_skip(self, isolated_config, isolated_session_manager):
        session_id = f"test-block-ghost-{time.time()}"
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)

        def set_count(metadata):
            metadata['stop_block_count'] = 3
        manager.atomic_update_metadata(set_count)

        # Ghost task skip should NOT reset counter
        manager.update_task('999', {'status': 'in_progress'}, 'Ghost')
        assert manager.session_metadata.get('stop_block_count', 0) == 3, \
            "Block count should NOT reset on ghost skip"


# --- Fix 5: is_premature_stop documents task mitigation ---

class TestFix5IsPrematureStopDocstring:
    def test_docstring_mentions_task_checking(self):
        import inspect
        from autorun.plugins import is_premature_stop
        source = inspect.getsource(is_premature_stop)
        assert "prevent_premature_stop" in source, \
            "is_premature_stop should document that task checking is in prevent_premature_stop"


# --- Fix 7: PlanNotifyConfig ---

class TestFix7PlanNotifyConfig:
    def test_plan_notify_config_load_defaults(self, tmp_path, monkeypatch):
        import autorun.task_lifecycle as tl
        monkeypatch.setattr(tl, 'PLAN_NOTIFY_CONFIG_PATH', tmp_path / 'pn.json')
        from autorun.task_lifecycle import PlanNotifyConfig
        cfg = PlanNotifyConfig.load()
        assert cfg.tdd_scaffolding is True
        assert cfg.task_update_enforcement is True
        assert cfg.dependency_wiring is True

    def test_plan_notify_config_save_load_roundtrip(self, tmp_path, monkeypatch):
        import autorun.task_lifecycle as tl
        monkeypatch.setattr(tl, 'PLAN_NOTIFY_CONFIG_PATH', tmp_path / 'pn.json')
        from autorun.task_lifecycle import PlanNotifyConfig
        cfg = PlanNotifyConfig(tdd_scaffolding=False)
        cfg.save()
        cfg2 = PlanNotifyConfig.load()
        assert cfg2.tdd_scaffolding is False
        assert cfg2.task_update_enforcement is True


# --- Fix 6: No standalone helper functions ---

class TestFix6NoStandaloneHelpers:
    def test_no_standalone_helper_functions(self):
        import inspect
        from autorun import plugins
        source = inspect.getsource(plugins)
        assert '_load_plan_notify_config' not in source
        assert '_get_plan_task_injection' not in source
        assert '_build_plan_acceptance_notification' not in source


# --- Fix 6: get_plan_approval_injection returns string ---

class TestFix6PlanApprovalInjection:
    def test_returns_none_without_plan_key(self, isolated_config, isolated_session_manager):
        session_id = f"test-plan-inject-{time.time()}"
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)
        ctx = MagicMock()
        ctx.plan_arguments = ''
        result = manager.get_plan_approval_injection(ctx)
        assert result is None

    def test_returns_string_with_tasks(self, isolated_config, isolated_session_manager):
        session_id = f"test-plan-tasks-{time.time()}"
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)
        # Create task and link to plan
        manager.create_task('1', {'subject': 'Test task'}, 'Created')
        manager.link_task_to_plan('1', 'test-plan')
        ctx = MagicMock()
        ctx.plan_arguments = 'test-plan'
        result = manager.get_plan_approval_injection(ctx)
        assert isinstance(result, str), \
            "get_plan_approval_injection should return a string, not a dict"
        assert "Test task" in result
