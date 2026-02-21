#!/usr/bin/env python3

"""Edge case and error handling tests for task lifecycle tracking.

Tests unusual scenarios and boundary conditions:
1. Empty task creation (minimal fields)
2. Very long task subject/description (stress test)
3. Invalid task ID characters
4. Circular dependencies (A blocks B, B blocks A)
5. Self-blocking task (A blocks A)
6. Update non-existent task (creates minimal entry)
7. Ignore already-ignored task (idempotent)
8. Prune with zero TTL (immediate pruning)
9. Multiple session_id formats (UUID, timestamps, custom)
10. Config override validation
"""

import sys
from pathlib import Path
import time
import shutil

# Add src to path
plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root / 'src'))

from autorun.task_lifecycle import TaskLifecycle, TaskLifecycleConfig
from autorun.session_manager import session_state


class TestEdgeCases:
    """Edge case and error handling tests."""

    def test_01_empty_task_creation_minimal_fields(self):
        """Edge 1: Create task with minimal/empty fields."""
        print("\n=== Edge 1: Empty task creation ===")

        session_id = f'test-empty-{int(time.time())}'
        manager = TaskLifecycle(session_id=session_id)

        # Create task with empty strings
        manager.create_task('1', {
            'subject': '',
            'description': '',
            'activeForm': ''
        }, 'Created empty task')

        tasks = manager.tasks
        assert '1' in tasks
        assert tasks['1']['subject'] == ''
        assert tasks['1']['description'] == ''
        assert tasks['1']['activeForm'] == ''
        assert tasks['1']['status'] == 'pending'

        # Cleanup
        shutil.rmtree(manager.config.storage_dir / session_id, ignore_errors=True)
        with session_state(manager.global_key) as state:
            state.clear()

        print("✅ Edge 1 passed: Empty task creation works")

    def test_02_very_long_task_fields(self):
        """Edge 2: Very long subject/description (stress test)."""
        print("\n=== Edge 2: Very long task fields ===")

        session_id = f'test-long-{int(time.time())}'
        manager = TaskLifecycle(session_id=session_id)

        # Create task with very long strings
        long_subject = 'A' * 10000  # 10k characters
        long_description = 'B' * 50000  # 50k characters
        long_active_form = 'C' * 5000  # 5k characters

        manager.create_task('1', {
            'subject': long_subject,
            'description': long_description,
            'activeForm': long_active_form
        }, 'Created long task')

        tasks = manager.tasks
        assert '1' in tasks
        assert tasks['1']['subject'] == long_subject
        assert tasks['1']['description'] == long_description
        assert tasks['1']['activeForm'] == long_active_form

        # Cleanup
        shutil.rmtree(manager.config.storage_dir / session_id, ignore_errors=True)
        with session_state(manager.global_key) as state:
            state.clear()

        print("✅ Edge 2 passed: Very long fields handled")

    def test_03_special_characters_in_task_id(self):
        """Edge 3: Special characters in task IDs."""
        print("\n=== Edge 3: Special characters in task ID ===")

        session_id = f'test-special-{int(time.time())}'
        manager = TaskLifecycle(session_id=session_id)

        # Task IDs with special characters (should work - strings are valid)
        special_ids = ['task-1', 'task_2', 'task.3', 'task:4']

        for task_id in special_ids:
            manager.create_task(task_id, {
                'subject': f'Task {task_id}',
                'description': '',
                'activeForm': ''
            }, f'Created {task_id}')

        tasks = manager.tasks
        for task_id in special_ids:
            assert task_id in tasks, f"Task {task_id} should exist"

        # Cleanup
        shutil.rmtree(manager.config.storage_dir / session_id, ignore_errors=True)
        with session_state(manager.global_key) as state:
            state.clear()

        print("✅ Edge 3 passed: Special character task IDs work")

    def test_04_circular_dependencies(self):
        """Edge 4: Circular dependencies (A blocks B, B blocks A)."""
        print("\n=== Edge 4: Circular dependencies ===")

        session_id = f'test-circular-{int(time.time())}'
        manager = TaskLifecycle(session_id=session_id)

        # Create two tasks
        manager.create_task('A', {'subject': 'Task A', 'description': '', 'activeForm': ''}, 'Created A')
        manager.create_task('B', {'subject': 'Task B', 'description': '', 'activeForm': ''}, 'Created B')

        # Create circular dependency
        manager.update_task('A', {'addBlockedBy': ['B']}, 'A blocked by B')
        manager.update_task('B', {'addBlockedBy': ['A']}, 'B blocked by A')

        # Both tasks should be blocked (neither can proceed)
        prioritized = manager.get_prioritized_tasks()
        assert len(prioritized) == 2

        # Both should be in "blocked" category (no progress possible)
        tasks = manager.tasks
        assert 'B' in tasks['A']['blockedBy']
        assert 'A' in tasks['B']['blockedBy']

        # Cleanup
        shutil.rmtree(manager.config.storage_dir / session_id, ignore_errors=True)
        with session_state(manager.global_key) as state:
            state.clear()

        print("✅ Edge 4 passed: Circular dependencies handled (both blocked)")

    def test_05_self_blocking_task(self):
        """Edge 5: Self-blocking task (A blocks A)."""
        print("\n=== Edge 5: Self-blocking task ===")

        session_id = f'test-self-{int(time.time())}'
        manager = TaskLifecycle(session_id=session_id)

        # Create task that blocks itself
        manager.create_task('1', {'subject': 'Self-blocking', 'description': '', 'activeForm': ''}, 'Created')
        manager.update_task('1', {'addBlockedBy': ['1']}, 'Self-blocked')

        tasks = manager.tasks
        assert '1' in tasks['1']['blockedBy'], "Task should block itself"

        # Should be in blocked category (can never complete)
        prioritized = manager.get_prioritized_tasks()
        assert len(prioritized) == 1

        # Cleanup
        shutil.rmtree(manager.config.storage_dir / session_id, ignore_errors=True)
        with session_state(manager.global_key) as state:
            state.clear()

        print("✅ Edge 5 passed: Self-blocking task handled")

    def test_06_update_nonexistent_task(self):
        """Edge 6: Update non-existent task (creates ghost entry that stays ignored).

        Ghost tasks (created before tracking) must NEVER transition to
        in_progress/pending because we can't reliably track their completion.
        Only terminal statuses (completed, deleted, ignored) are accepted.
        """
        print("\n=== Edge 6: Update non-existent task ===")

        session_id = f'test-nonexist-{int(time.time())}'
        manager = TaskLifecycle(session_id=session_id)

        # Update task that doesn't exist with in_progress - should be BLOCKED
        manager.update_task('999', {'status': 'in_progress'}, 'Updated non-existent')

        # Ghost task created but stays ignored (in_progress blocked for ghosts)
        tasks = manager.tasks
        assert '999' in tasks
        assert tasks['999']['status'] == 'ignored', \
            "Ghost task must stay ignored when non-terminal status requested (prevents stale blocking)"
        assert tasks['999']['subject'] == '(unknown - created before tracking)'
        assert tasks['999']['metadata'].get('ghost_task') == True

        # Ghost task CAN transition to completed (terminal status accepted)
        manager.update_task('999', {'status': 'completed'}, 'Completed ghost')
        tasks = manager.tasks
        assert tasks['999']['status'] == 'completed', \
            "Ghost task should accept terminal status 'completed'"

        # Verify ghost task without status update defaults to "ignored" (not "pending")
        # so it doesn't block stopping
        manager.update_task('998', {'subject': 'Updated ghost'}, 'Just metadata update')
        tasks = manager.tasks
        assert '998' in tasks
        assert tasks['998']['status'] == 'ignored', \
            "Ghost task without status update should default to 'ignored', not block stopping"
        assert tasks['998']['subject'] == 'Updated ghost'
        assert tasks['998']['metadata'].get('ghost_task') == True

        # Cleanup
        shutil.rmtree(manager.config.storage_dir / session_id, ignore_errors=True)
        with session_state(manager.global_key) as state:
            state.clear()

        print("✅ Edge 6 passed: Ghost tasks default to 'ignored' and can't become blocking")

    def test_07_ignore_already_ignored_task(self):
        """Edge 7: Ignore already-ignored task (idempotent)."""
        print("\n=== Edge 7: Ignore already-ignored task ===")

        session_id = f'test-idempotent-{int(time.time())}'
        manager = TaskLifecycle(session_id=session_id)

        # Create and ignore task
        manager.create_task('1', {'subject': 'To ignore', 'description': '', 'activeForm': ''}, 'Created')
        manager.update_task('1', {'status': 'ignored'}, 'Ignored first time')

        # Ignore again
        manager.update_task('1', {'status': 'ignored'}, 'Ignored second time')

        tasks = manager.tasks
        assert tasks['1']['status'] == 'ignored'
        # Should have 3 tool outputs: create + 2 updates
        assert len(tasks['1']['tool_outputs']) >= 3

        # Cleanup
        shutil.rmtree(manager.config.storage_dir / session_id, ignore_errors=True)
        with session_state(manager.global_key) as state:
            state.clear()

        print("✅ Edge 7 passed: Ignoring already-ignored task is idempotent")

    def test_08_prune_with_zero_ttl(self):
        """Edge 8: Prune with zero TTL (immediate pruning)."""
        print("\n=== Edge 8: Prune with zero TTL ===")

        session_id = f'test-zero-ttl-{int(time.time())}'
        config = TaskLifecycleConfig.load()
        config.task_ttl_days = 0  # Zero TTL
        manager = TaskLifecycle(session_id=session_id, config=config)

        # Create and complete task
        manager.create_task('1', {'subject': 'Zero TTL', 'description': '', 'activeForm': ''}, 'Created')
        manager.update_task('1', {'status': 'completed'}, 'Completed')

        # Make it old (1 second old)
        def make_old(tasks):
            tasks['1']['updated_at'] = time.time() - 1
        manager.atomic_update_tasks(make_old)

        # Prune (should remove immediately)
        pruned_count = manager.prune_old_tasks()
        assert pruned_count == 1, "Should prune task with 0 TTL"

        tasks = manager.tasks
        assert '1' not in tasks, "Task should be pruned"

        # Cleanup
        shutil.rmtree(manager.config.storage_dir / session_id, ignore_errors=True)
        with session_state(manager.global_key) as state:
            state.clear()

        print("✅ Edge 8 passed: Zero TTL prunes immediately")

    def test_09_various_session_id_formats(self):
        """Edge 9: Multiple session_id formats (UUID, timestamps, custom)."""
        print("\n=== Edge 9: Various session_id formats ===")

        session_ids = [
            'simple',
            'with-dashes',
            'with_underscores',
            'with.dots',
            '12345678-1234-1234-1234-123456789012',  # UUID format
            f'timestamp-{int(time.time())}',
            'mixed-123_test.session'
        ]

        for sid in session_ids:
            manager = TaskLifecycle(session_id=sid)
            manager.create_task('1', {'subject': 'Test', 'description': '', 'activeForm': ''}, 'Created')

            tasks = manager.tasks
            assert '1' in tasks, f"Should work with session_id: {sid}"

            # Cleanup
            shutil.rmtree(manager.config.storage_dir / sid, ignore_errors=True)
            with session_state(manager.global_key) as state:
                state.clear()

        print("✅ Edge 9 passed: Various session_id formats work")

    def test_10_config_override_validation(self):
        """Edge 10: Config override validation."""
        print("\n=== Edge 10: Config override validation ===")

        session_id = f'test-config-{int(time.time())}'

        # Create custom config
        config = TaskLifecycleConfig()
        config.max_resume_tasks = 5
        config.stop_block_max_count = 10
        config.task_ttl_days = 60

        manager = TaskLifecycle(session_id=session_id, config=config)

        # Verify config was applied
        assert manager.config.max_resume_tasks == 5
        assert manager.config.stop_block_max_count == 10
        assert manager.config.task_ttl_days == 60

        # Cleanup
        shutil.rmtree(manager.config.storage_dir / session_id, ignore_errors=True)
        with session_state(manager.global_key) as state:
            state.clear()

        print("✅ Edge 10 passed: Config override works")


def run_all_edge_case_tests():
    """Run all edge case tests."""
    print("Running edge case tests...\n")

    test = TestEdgeCases()
    tests = [
        test.test_01_empty_task_creation_minimal_fields,
        test.test_02_very_long_task_fields,
        test.test_03_special_characters_in_task_id,
        test.test_04_circular_dependencies,
        test.test_05_self_blocking_task,
        test.test_06_update_nonexistent_task,
        test.test_07_ignore_already_ignored_task,
        test.test_08_prune_with_zero_ttl,
        test.test_09_various_session_id_formats,
        test.test_10_config_override_validation,
    ]

    passed = 0
    failed = 0

    for test_func in tests:
        try:
            test_func()
            passed += 1
        except AssertionError as e:
            print(f"❌ {test_func.__name__} failed: {e}")
            failed += 1
        except Exception as e:
            print(f"❌ {test_func.__name__} error: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*60}")
    print(f"Edge Case Tests: {passed} passed, {failed} failed")
    print(f"{'='*60}\n")

    return failed == 0


if __name__ == '__main__':
    success = run_all_edge_case_tests()
    sys.exit(0 if success else 1)
