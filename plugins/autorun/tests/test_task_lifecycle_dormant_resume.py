"""Test dormant session resume preserves task history.

CRITICAL: Completed tasks are historical evidence, not garbage.
When users resume dormant sessions (even after months), they expect:
1. Completed task history intact (shows what was accomplished)
2. Paused tasks preserved (explicit user intent to resume later)
3. NO automatic deletion without their consent

This tests the lifecycle contract that history is preserved.
"""
import time
from pathlib import Path
import pytest

from clautorun.task_lifecycle import TaskLifecycle, TaskLifecycleConfig
from clautorun.session_manager import session_state


@pytest.fixture
def isolated_config(tmp_path):
    """Isolated config for testing."""
    return TaskLifecycleConfig(
        enabled=True,
        storage_dir=tmp_path / "task_lifecycle",
        task_ttl_days=30,
    )


class TestDormantSessionResume:
    """Test resuming dormant sessions preserves history."""

    def test_completed_tasks_preserved_after_35_days(self, isolated_config):
        """TEST CRITICAL: Completed tasks NOT auto-deleted on dormant session resume.

        Scenario:
        1. User completes 100 tasks in a session
        2. Session goes dormant for 35 days (> 30 day TTL)
        3. User resumes session with claude --resume
        4. SessionStart fires
        5. CRITICAL: Completed tasks MUST be preserved (historical evidence)
        6. NO automatic pruning without user consent

        VIOLATION: If completed tasks auto-deleted, user loses work history.
        """
        session_id = f'dormant-{int(time.time())}'
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)

        # Simulate productive session: create and complete 100 tasks
        for i in range(1, 101):
            manager.create_task(str(i), {'subject': f'Task {i}', 'description': f'Work {i}'}, 'Created')
            manager.update_task(str(i), {'status': 'completed'}, 'Done')

        # Age all tasks to 35 days old (past TTL)
        def age_all(tasks):
            old_time = time.time() - (35 * 86400)
            for task in tasks.values():
                task['updated_at'] = old_time
        manager.atomic_update_tasks(age_all)

        # Verify setup
        tasks_before = manager.tasks
        assert len(tasks_before) == 100
        assert all(t['status'] == 'completed' for t in tasks_before.values())

        # CRITICAL TEST: Simulate session resume (SessionStart)
        from clautorun.core import EventContext
        ctx = EventContext(
            session_id=session_id,
            event="SessionStart",
            prompt="",
            store=None
        )

        # Manually create TaskLifecycle for this session (simulates resume)
        manager_resumed = TaskLifecycle(session_id=session_id, config=isolated_config, ctx=ctx)

        # Call handle_session_start (what happens on resume)
        injection = manager_resumed.handle_session_start(ctx)

        # VERIFY: Completed tasks MUST still exist (not auto-pruned)
        tasks_after = manager_resumed.tasks
        assert len(tasks_after) == 100, \
            "Completed tasks must NOT be auto-pruned on SessionStart (historical evidence)"

        # Verify all still completed
        assert all(t['status'] == 'completed' for t in tasks_after.values()), \
            "Task status should be unchanged"

        # Verify no injection (no incomplete tasks)
        assert injection is None, "Should not inject resume prompt (no incomplete work)"

    def test_paused_tasks_preserved_after_40_days(self, isolated_config):
        """TEST: Paused tasks preserved indefinitely (user resume intent)."""
        session_id = f'paused-dormant-{int(time.time())}'
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)

        # Create paused tasks (explicit user intent to resume later)
        for i in range(1, 11):
            manager.create_task(str(i), {'subject': f'Paused {i}', 'description': f'Later {i}'}, 'Created')
            manager.update_task(str(i), {'status': 'paused'}, 'User paused')

        # Age to 40 days (way past TTL)
        def age_all(tasks):
            old_time = time.time() - (40 * 86400)
            for task in tasks.values():
                task['updated_at'] = old_time
        manager.atomic_update_tasks(age_all)

        # Resume session
        from clautorun.core import EventContext
        ctx = EventContext(session_id=session_id, event="SessionStart", prompt="", store=None)
        manager_resumed = TaskLifecycle(session_id=session_id, config=isolated_config, ctx=ctx)
        manager_resumed.handle_session_start(ctx)

        # VERIFY: All paused tasks preserved
        tasks_after = manager_resumed.tasks
        assert len(tasks_after) == 10, "Paused tasks must be preserved (user resume intent)"
        assert all(t['status'] == 'paused' for t in tasks_after.values())

    def test_mixed_status_history_preserved(self, isolated_config):
        """TEST: Mixed status tasks (completed + paused + pending) all preserved."""
        session_id = f'mixed-{int(time.time())}'
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)

        # Completed tasks (history)
        for i in range(1, 51):
            manager.create_task(str(i), {'subject': f'Done {i}', 'description': 'History'}, 'Created')
            manager.update_task(str(i), {'status': 'completed'}, 'Done')

        # Paused tasks (user intent)
        for i in range(51, 61):
            manager.create_task(str(i), {'subject': f'Paused {i}', 'description': 'Later'}, 'Created')
            manager.update_task(str(i), {'status': 'paused'}, 'Paused')

        # Pending tasks (active work)
        for i in range(61, 71):
            manager.create_task(str(i), {'subject': f'Pending {i}', 'description': 'Todo'}, 'Created')

        # Age all to 35 days
        def age(tasks):
            for task in tasks.values():
                task['updated_at'] = time.time() - (35 * 86400)
        manager.atomic_update_tasks(age)

        # Resume
        from clautorun.core import EventContext
        ctx = EventContext(session_id=session_id, event="SessionStart", prompt="", store=None)
        manager_resumed = TaskLifecycle(session_id=session_id, config=isolated_config, ctx=ctx)
        manager_resumed.handle_session_start(ctx)

        # VERIFY: ALL tasks preserved (no auto-pruning)
        tasks_after = manager_resumed.tasks
        assert len(tasks_after) == 70, "ALL tasks must be preserved (no auto-pruning)"

        # Verify counts by status
        by_status = {}
        for t in tasks_after.values():
            by_status[t['status']] = by_status.get(t['status'], 0) + 1

        assert by_status['completed'] == 50, "Completed history preserved"
        assert by_status['paused'] == 10, "Paused tasks preserved"
        assert by_status['pending'] == 10, "Pending tasks preserved"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
