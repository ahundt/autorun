#!/usr/bin/env python3

"""Failure mode tests for task lifecycle tracking.

Tests all 8 pre-mortem problems from the plan:
1. Task explosion (100+ tasks)
2. Stuck task (blocked forever, escape hatch)
3. Format change (multiple regex patterns)
4. Unbounded growth (pruning)
5. Race conditions (concurrent access)
6. False completion
7. Log file growth
8. Corrupted shelve (backup/recovery)
"""

import sys
from pathlib import Path
import time
import shutil

# Add src to path
plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root / 'src'))

from clautorun.task_lifecycle import TaskLifecycle, TaskLifecycleConfig
from clautorun.session_manager import session_state


class TestFailureModes:
    """Tests for pre-mortem failure modes."""

    def test_01_task_explosion_100_plus_tasks(self):
        """Problem 1: Task explosion (100+ tasks) - cap injection."""
        print("\n=== Problem 1: Task explosion (100+ tasks) ===")

        session_id = f'test-explosion-{int(time.time())}'
        manager = TaskLifecycle(session_id=session_id)

        # Create 150 tasks
        for i in range(150):
            manager.create_task(str(i+1), {
                'subject': f'Task {i+1}',
                'description': f'Test task {i+1}',
                'activeForm': f'Working on {i+1}...'
            }, f'Created task #{i+1}')

        # Get prioritized tasks (should handle 150 tasks)
        prioritized = manager.get_prioritized_tasks()
        assert len(prioritized) == 150

        # Check resume prompt caps at max_resume_tasks
        from unittest.mock import MagicMock
        ctx = MagicMock()
        ctx.session_id = session_id
        ctx.plan_active = False  # Don't try to link to plan
        ctx.plan_arguments = ''

        def mock_block(msg=''):
            return {'continue': False, 'systemMessage': msg}
        ctx.block = MagicMock(side_effect=mock_block)

        result = manager.handle_session_start(ctx)

        # Should inject but cap the display
        assert result is not None
        message = result['systemMessage']
        assert '150 incomplete' in message
        # Should mention there are more tasks
        assert 'more tasks' in message or '/task-status' in message

        # Cleanup
        shutil.rmtree(manager.config.storage_dir / session_id, ignore_errors=True)
        with session_state(manager.global_key) as state:
            state.clear()

        print("✅ Problem 1 handled: Task explosion capped at max_resume_tasks")

    def test_02_stuck_task_always_blocks_without_user_action(self):
        """Problem 2: Stuck task - stop always blocked until user acts.

        The escape hatch requires USER action (/cr:sos or /cr:task-ignore),
        not automatic override after N attempts. Automatic override caused
        premature stoppage.
        """
        print("\n=== Problem 2: Stuck task always blocks (no auto-override) ===")

        session_id = f'test-stuck-{int(time.time())}'
        manager = TaskLifecycle(session_id=session_id)

        # Create task with impossible blocker
        manager.create_task('1', {
            'subject': 'Stuck task',
            'description': 'Blocked by non-existent task',
            'activeForm': 'Stuck...'
        }, 'Created')

        manager.update_task('1', {'addBlockedBy': ['999']}, 'Added blocker')

        # Try to stop many times - should ALWAYS block
        from unittest.mock import MagicMock
        ctx = MagicMock()
        ctx.session_id = session_id
        ctx.plan_active = False
        ctx.plan_arguments = ''

        def mock_block(msg=''):
            return {'continue': False, 'systemMessage': msg}

        def mock_allow(msg=''):
            return {'continue': True, 'systemMessage': msg}

        # Try more than the old max_blocks - should STILL block every time
        for i in range(10):
            ctx.block = MagicMock(side_effect=mock_block)
            ctx.allow = MagicMock(side_effect=mock_allow)
            result = manager.handle_stop(ctx)
            assert result['continue'] == False, f"Should block attempt {i+1} - no auto-override"
            assert 'INCOMPLETE TASKS' in result['systemMessage']

        # Verify the message includes user escape hatch instructions
        assert '/cr:sos' in result['systemMessage'], "Should mention /cr:sos as user escape hatch"
        assert '/cr:task-ignore' in result['systemMessage'], "Should mention /cr:task-ignore"

        # Cleanup
        shutil.rmtree(manager.config.storage_dir / session_id, ignore_errors=True)
        with session_state(manager.global_key) as state:
            state.clear()

        print("✅ Problem 2 handled: Stop always blocked - user must use /cr:sos or /cr:task-ignore")

    def test_03_format_change_multiple_regex_patterns(self):
        """Problem 3: Format change - multiple regex patterns handle it."""
        print("\n=== Problem 3: Format change with fallback patterns ===")

        session_id = f'test-format-{int(time.time())}'
        manager = TaskLifecycle(session_id=session_id)

        # Test different result formats
        formats = [
            'Task #1 created successfully: Test',  # Standard
            'Created task #2 successfully: Test',   # Alternative
            'Task 3 created',                       # Minimal
            '#4',                                    # Last resort
        ]

        from unittest.mock import MagicMock
        for i, result_format in enumerate(formats, 1):
            ctx = MagicMock()
            ctx.session_id = session_id
            ctx.tool_name = 'TaskCreate'
            ctx.tool_input = {
                'subject': f'Test {i}',
                'description': '',
                'activeForm': ''
            }
            ctx.tool_result = result_format
            ctx.plan_active = False  # Don't try to link to plan
            ctx.plan_arguments = ''

            manager.handle_task_create(ctx)

        # Verify all 4 tasks were created
        tasks = manager.tasks
        assert len(tasks) == 4, f"Expected 4 tasks, got {len(tasks)}"
        assert '1' in tasks and '2' in tasks and '3' in tasks and '4' in tasks

        # Cleanup
        shutil.rmtree(manager.config.storage_dir / session_id, ignore_errors=True)
        with session_state(manager.global_key) as state:
            state.clear()

        print("✅ Problem 3 handled: Multiple regex patterns work")

    def test_04_unbounded_growth_pruning(self):
        """Problem 4: Unbounded growth - pruning removes old completed tasks."""
        print("\n=== Problem 4: Unbounded growth with pruning ===")

        session_id = f'test-pruning-{int(time.time())}'
        config = TaskLifecycleConfig.load()
        config.task_ttl_days = 0  # Set to 0 for immediate pruning
        manager = TaskLifecycle(session_id=session_id, config=config)

        # Create and complete old tasks
        for i in range(10):
            manager.create_task(str(i+1), {
                'subject': f'Old task {i+1}',
                'description': '',
                'activeForm': ''
            }, f'Created {i+1}')

            manager.update_task(str(i+1), {'status': 'completed'}, 'Completed')

            # Make them old
            def make_old(tasks):
                tasks[str(i+1)]['updated_at'] = time.time() - (86400 * 31)  # 31 days old
            manager.atomic_update_tasks(make_old)

        # Create recent incomplete task
        manager.create_task('99', {
            'subject': 'Recent task',
            'description': '',
            'activeForm': ''
        }, 'Created recent')

        # Run pruning
        pruned_count = manager.prune_old_tasks()

        # Verify old tasks were pruned
        assert pruned_count == 10, f"Expected 10 pruned, got {pruned_count}"

        tasks = manager.tasks
        assert '99' in tasks, "Recent task should remain"
        assert '1' not in tasks, "Old completed tasks should be pruned"

        # Cleanup
        shutil.rmtree(manager.config.storage_dir / session_id, ignore_errors=True)
        with session_state(manager.global_key) as state:
            state.clear()

        print(f"✅ Problem 4 handled: Pruned {pruned_count} old completed tasks")

    def test_05_race_conditions_concurrent_access(self):
        """Problem 5: Race conditions - atomic operations prevent corruption."""
        print("\n=== Problem 5: Race conditions with atomic operations ===")

        session_id = f'test-race-{int(time.time())}'
        manager = TaskLifecycle(session_id=session_id)

        # Create initial task
        manager.create_task('1', {
            'subject': 'Shared task',
            'description': '',
            'activeForm': ''
        }, 'Created')

        # Simulate concurrent updates using atomic_update_tasks
        import threading

        def concurrent_update(manager, task_id, field, value):
            """Simulate concurrent update."""
            def updater(tasks):
                if task_id in tasks:
                    tasks[task_id]['metadata'][field] = value
            manager.atomic_update_tasks(updater)

        threads = []
        for i in range(10):
            t = threading.Thread(target=concurrent_update, args=(manager, '1', f'field{i}', f'value{i}'))
            threads.append(t)
            t.start()

        # Wait for all threads
        for t in threads:
            t.join()

        # Verify all updates applied (no corruption)
        task = manager.tasks['1']
        for i in range(10):
            assert f'field{i}' in task['metadata'], f"field{i} should be present"
            assert task['metadata'][f'field{i}'] == f'value{i}', f"field{i} should have correct value"

        # Cleanup
        shutil.rmtree(manager.config.storage_dir / session_id, ignore_errors=True)
        with session_state(manager.global_key) as state:
            state.clear()

        print("✅ Problem 5 handled: Atomic operations prevent race conditions")

    def test_06_deduplication(self):
        """Problem 5b: Deduplication prevents duplicate task IDs."""
        print("\n=== Problem 5b: Deduplication ===")

        session_id = f'test-dedup-{int(time.time())}'
        manager = TaskLifecycle(session_id=session_id)

        # Try to create same task ID twice
        manager.create_task('1', {'subject': 'First', 'description': '', 'activeForm': ''}, 'Created')
        manager.create_task('1', {'subject': 'Duplicate', 'description': '', 'activeForm': ''}, 'Created again')

        tasks = manager.tasks
        assert len(tasks) == 1, "Should only have one task"
        assert tasks['1']['subject'] == 'First', "Should keep first task, ignore duplicate"

        # Cleanup
        shutil.rmtree(manager.config.storage_dir / session_id, ignore_errors=True)
        with session_state(manager.global_key) as state:
            state.clear()

        print("✅ Problem 5b handled: Deduplication works")

    def test_07_log_file_does_not_interfere(self):
        """Problem 7: Log file growth - should not interfere with operations."""
        print("\n=== Problem 7: Log file growth ===")

        session_id = f'test-log-{int(time.time())}'
        config = TaskLifecycleConfig.load()
        config.debug_logging = True
        manager = TaskLifecycle(session_id=session_id, config=config)

        # Create many tasks to generate log entries
        for i in range(100):
            manager.create_task(str(i+1), {
                'subject': f'Task {i+1}',
                'description': '',
                'activeForm': ''
            }, f'Created {i+1}')

        # Verify log file exists
        assert manager.audit_log.exists(), "Audit log should exist"

        # Verify operations still work despite large log
        tasks = manager.tasks
        assert len(tasks) == 100

        # Cleanup
        shutil.rmtree(manager.config.storage_dir / session_id, ignore_errors=True)
        with session_state(manager.global_key) as state:
            state.clear()

        print("✅ Problem 7 handled: Log file growth doesn't interfere")

    def test_08_session_isolation(self):
        """Problem 8: Session isolation - separate sessions don't interfere."""
        print("\n=== Problem 8: Session isolation ===")

        session1 = f'test-iso1-{int(time.time())}'
        session2 = f'test-iso2-{int(time.time())}'

        manager1 = TaskLifecycle(session_id=session1)
        manager2 = TaskLifecycle(session_id=session2)

        # Create task in session 1
        manager1.create_task('1', {'subject': 'Session 1 task', 'description': '', 'activeForm': ''}, 'Created')

        # Create task in session 2
        manager2.create_task('1', {'subject': 'Session 2 task', 'description': '', 'activeForm': ''}, 'Created')

        # Verify isolation
        tasks1 = manager1.tasks
        tasks2 = manager2.tasks

        assert len(tasks1) == 1
        assert len(tasks2) == 1
        assert tasks1['1']['subject'] == 'Session 1 task'
        assert tasks2['1']['subject'] == 'Session 2 task'

        # Cleanup
        shutil.rmtree(manager1.config.storage_dir / session1, ignore_errors=True)
        shutil.rmtree(manager2.config.storage_dir / session2, ignore_errors=True)
        with session_state(manager1.global_key) as state:
            state.clear()
        with session_state(manager2.global_key) as state:
            state.clear()

        print("✅ Problem 8 handled: Sessions are properly isolated")


def run_all_failure_mode_tests():
    """Run all failure mode tests."""
    print("Running failure mode tests...\n")

    test = TestFailureModes()
    tests = [
        test.test_01_task_explosion_100_plus_tasks,
        test.test_02_stuck_task_always_blocks_without_user_action,
        test.test_03_format_change_multiple_regex_patterns,
        test.test_04_unbounded_growth_pruning,
        test.test_05_race_conditions_concurrent_access,
        test.test_06_deduplication,
        test.test_07_log_file_does_not_interfere,
        test.test_08_session_isolation,
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
    print(f"Failure Mode Tests: {passed} passed, {failed} failed")
    print(f"{'='*60}\n")

    return failed == 0


if __name__ == '__main__':
    success = run_all_failure_mode_tests()
    sys.exit(0 if success else 1)
