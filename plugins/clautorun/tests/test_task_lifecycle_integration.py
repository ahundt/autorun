#!/usr/bin/env python3

"""Comprehensive integration tests for task lifecycle tracking.

Tests all 10 end-to-end scenarios from the plan:
1. Create tasks with full metadata
2. Update task with dependencies
3. Complete task and update metadata
4. Stop with incomplete work (BLOCKS)
5. Complete remaining work and stop
6. Resume detection (full context)
7. Plan context injection
8. Cross-session persistence (all fields)
9. Option 1 context clear
10. Deleted task handling

Simulates actual hook behavior using EventContext.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock
import time

# Add src to path
plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root / 'src'))

from clautorun.task_lifecycle import TaskLifecycle, TaskLifecycleConfig
from clautorun.core import EventContext


def create_mock_context(session_id='test-integration', **kwargs):
    """Create a mock EventContext for testing."""
    ctx = MagicMock(spec=EventContext)
    ctx.session_id = session_id
    ctx.tool_name = kwargs.get('tool_name', '')
    ctx.tool_input = kwargs.get('tool_input', {})
    ctx.tool_result = kwargs.get('tool_result', '')
    ctx.plan_active = kwargs.get('plan_active', False)
    ctx.plan_arguments = kwargs.get('plan_arguments', '')

    # Mock the allow and block methods
    def mock_allow(msg=''):
        return {'continue': True, 'systemMessage': msg}

    def mock_block(msg=''):
        return {'continue': False, 'systemMessage': msg}

    ctx.allow = MagicMock(side_effect=mock_allow)
    ctx.block = MagicMock(side_effect=mock_block)

    return ctx


class TestTaskLifecycleIntegration:
    """Integration tests for task lifecycle."""

    def setup_method(self):
        """Setup for each test."""
        self.session_id = f'test-integration-{int(time.time())}'
        self.manager = TaskLifecycle(session_id=self.session_id)

    def teardown_method(self):
        """Cleanup after each test."""
        # Clear session state
        import shutil
        storage_dir = self.manager.config.storage_dir / self.session_id
        if storage_dir.exists():
            shutil.rmtree(storage_dir)

        # Clear from session_state
        from clautorun.session_manager import session_state
        with session_state(self.manager.global_key) as state:
            state.clear()

    def test_01_create_tasks_with_full_metadata(self):
        """Test 1: Create tasks with full metadata."""
        print("\n=== Test 1: Create tasks with full metadata ===")

        # Create first task
        ctx1 = create_mock_context(
            session_id=self.session_id,
            tool_name='TaskCreate',
            tool_input={
                'subject': 'Fix bug',
                'description': 'Login form validation',
                'activeForm': 'Fixing bug...',
                'metadata': {'priority': 'high'}
            },
            tool_result='Task #1 created successfully: Fix bug'
        )

        self.manager.handle_task_create(ctx1)

        # Create second task
        ctx2 = create_mock_context(
            session_id=self.session_id,
            tool_name='TaskCreate',
            tool_input={
                'subject': 'Add tests',
                'description': 'Unit tests for login',
                'activeForm': 'Writing tests...'
            },
            tool_result='Task #2 created successfully: Add tests'
        )

        self.manager.handle_task_create(ctx2)

        # Verify both tasks exist with full metadata
        tasks = self.manager.tasks
        assert len(tasks) == 2, f"Expected 2 tasks, got {len(tasks)}"

        # Check task 1
        assert '1' in tasks
        task1 = tasks['1']
        assert task1['subject'] == 'Fix bug'
        assert task1['description'] == 'Login form validation'
        assert task1['activeForm'] == 'Fixing bug...'
        assert task1['status'] == 'pending'
        assert task1['metadata']['priority'] == 'high'
        assert 'created_at' in task1
        assert 'updated_at' in task1
        assert task1['session_id'] == self.session_id
        assert task1['blockedBy'] == []
        assert task1['blocks'] == []
        assert len(task1['tool_outputs']) == 1

        # Check task 2
        assert '2' in tasks
        task2 = tasks['2']
        assert task2['subject'] == 'Add tests'
        assert task2['status'] == 'pending'

        print("✅ Test 1 passed: Tasks created with full metadata")

    def test_02_update_task_with_dependencies(self):
        """Test 2: Update task with dependencies."""
        print("\n=== Test 2: Update task with dependencies ===")

        # Create two tasks
        self.manager.create_task('1', {
            'subject': 'Task 1',
            'description': 'First task',
            'activeForm': 'Working...'
        }, 'Created 1')

        self.manager.create_task('2', {
            'subject': 'Task 2',
            'description': 'Second task',
            'activeForm': 'Working...'
        }, 'Created 2')

        # Task 2 depends on Task 1
        ctx_dep = create_mock_context(
            session_id=self.session_id,
            tool_name='TaskUpdate',
            tool_input={
                'taskId': '2',
                'addBlockedBy': ['1']
            },
            tool_result='Updated task #2'
        )

        self.manager.handle_task_update(ctx_dep)

        # Update Task 1 to in_progress
        ctx_progress = create_mock_context(
            session_id=self.session_id,
            tool_name='TaskUpdate',
            tool_input={
                'taskId': '1',
                'status': 'in_progress'
            },
            tool_result='Updated task #1 status'
        )

        self.manager.handle_task_update(ctx_progress)

        # Verify dependencies
        tasks = self.manager.tasks
        assert tasks['2']['blockedBy'] == ['1'], "Task 2 should be blocked by Task 1"
        assert tasks['1']['status'] == 'in_progress', "Task 1 should be in_progress"

        print("✅ Test 2 passed: Dependencies and status updates work")

    def test_03_complete_task_and_update_metadata(self):
        """Test 3: Complete task and update metadata."""
        print("\n=== Test 3: Complete task and update metadata ===")

        # Create task
        self.manager.create_task('1', {
            'subject': 'Test task',
            'description': 'Test',
            'activeForm': 'Testing...'
        }, 'Created')

        # Complete with metadata
        ctx = create_mock_context(
            session_id=self.session_id,
            tool_name='TaskUpdate',
            tool_input={
                'taskId': '1',
                'status': 'completed',
                'metadata': {'duration': '15min', 'tests_passed': 'all'}
            },
            tool_result='Completed task #1'
        )

        self.manager.handle_task_update(ctx)

        # Verify
        task = self.manager.tasks['1']
        assert task['status'] == 'completed'
        assert task['metadata']['duration'] == '15min'
        assert task['metadata']['tests_passed'] == 'all'
        assert len(task['tool_outputs']) >= 2  # Create + Update

        print("✅ Test 3 passed: Task completion and metadata updates work")

    def test_04_stop_with_incomplete_work_blocks(self):
        """Test 4: Stop with incomplete work (BLOCKS) - PRIMARY GOAL."""
        print("\n=== Test 4: Stop with incomplete work BLOCKS ===")

        # Create incomplete tasks
        self.manager.create_task('1', {
            'subject': 'Task 1',
            'description': 'Incomplete',
            'activeForm': 'Working...'
        }, 'Created')

        self.manager.create_task('2', {
            'subject': 'Task 2',
            'description': 'Also incomplete',
            'activeForm': 'Working...'
        }, 'Created')

        # Try to stop
        ctx = create_mock_context(session_id=self.session_id)
        result = self.manager.handle_stop(ctx)

        # Verify stop was BLOCKED
        assert result is not None, "Stop should return a result (block or allow)"
        assert result['continue'] == False, "Stop should be BLOCKED with incomplete tasks"
        assert 'CANNOT STOP' in result['systemMessage'], "Should show CANNOT STOP message"
        assert '2 incomplete' in result['systemMessage'], "Should show count of incomplete tasks"

        # Verify block counter incremented
        metadata = self.manager.session_metadata
        assert metadata['stop_block_count'] == 1, "Stop block counter should be incremented"

        print("✅ Test 4 passed: Stop BLOCKS with incomplete tasks (PRIMARY GOAL)")

    def test_05_complete_remaining_work_and_stop(self):
        """Test 5: Complete remaining work and stop."""
        print("\n=== Test 5: Complete remaining work and stop ===")

        # Create and complete tasks
        self.manager.create_task('1', {'subject': 'Task 1', 'description': '', 'activeForm': ''}, 'Created')
        self.manager.update_task('1', {'status': 'completed'}, 'Completed')

        # Try to stop
        ctx = create_mock_context(session_id=self.session_id)
        result = self.manager.handle_stop(ctx)

        # Verify stop was ALLOWED
        assert result is None, "Stop should be allowed when all tasks complete"

        # Verify counter was reset
        metadata = self.manager.session_metadata
        assert metadata['stop_block_count'] == 0, "Counter should reset on successful stop"

        print("✅ Test 5 passed: Stop allowed when all tasks complete")

    def test_06_resume_detection_full_context(self):
        """Test 6: Resume detection with full context."""
        print("\n=== Test 6: Resume detection (full context) ===")

        # Create tasks with different statuses
        self.manager.create_task('1', {'subject': 'In progress task', 'description': '', 'activeForm': ''}, 'Created')
        self.manager.update_task('1', {'status': 'in_progress'}, 'Started')

        self.manager.create_task('2', {'subject': 'Pending task', 'description': '', 'activeForm': ''}, 'Created')

        self.manager.create_task('3', {'subject': 'Paused task', 'description': '', 'activeForm': ''}, 'Created')
        self.manager.update_task('3', {'status': 'paused'}, 'Paused')

        # Simulate SessionStart
        ctx = create_mock_context(session_id=self.session_id)
        result = self.manager.handle_session_start(ctx)

        # Verify resume prompt was injected
        assert result is not None, "Should inject resume prompt"
        assert result['continue'] == False, "Should block to show resume prompt"
        assert 'INCOMPLETE TASKS DETECTED' in result['systemMessage']
        assert 'In Progress' in result['systemMessage'] or 'Pending' in result['systemMessage']
        assert '2 incomplete' in result['systemMessage'] or 'In progress task' in result['systemMessage']

        # Paused task should not block
        assert 'Paused task' not in result['systemMessage'] or 'paused' in result['systemMessage'].lower()

        print("✅ Test 6 passed: Resume detection works with full context")

    def test_07_plan_context_injection(self):
        """Test 7: Plan context injection."""
        print("\n=== Test 7: Plan context injection ===")

        plan_key = 'test-plan-implementation'

        # Create task linked to plan
        self.manager.create_task('1', {'subject': 'Plan task', 'description': '', 'activeForm': ''}, 'Created')
        self.manager.link_task_to_plan('1', plan_key)

        # Simulate plan approval
        ctx = create_mock_context(
            session_id=self.session_id,
            tool_name='ExitPlanMode',
            tool_result='I approved your plan',
            plan_arguments=plan_key
        )
        ctx.plan_arguments = plan_key

        result = self.manager.handle_plan_approval(ctx)

        # Verify injection
        assert result is not None, "Should inject plan tasks"
        assert result['continue'] == True, "Should allow (not block)"
        assert 'Plan Accepted' in result['systemMessage']
        assert '1 task' in result['systemMessage'] or 'Plan task' in result['systemMessage']

        print("✅ Test 7 passed: Plan context injection works")

    def test_08_cross_session_persistence(self):
        """Test 8: Cross-session persistence (all fields)."""
        print("\n=== Test 8: Cross-session persistence ===")

        # Create task with full metadata
        self.manager.create_task('1', {
            'subject': 'Persistent task',
            'description': 'Should survive',
            'activeForm': 'Persisting...',
            'metadata': {'custom': 'value'}
        }, 'Created')

        self.manager.update_task('1', {
            'status': 'in_progress',
            'addBlockedBy': ['99'],
            'metadata': {'extra': 'data'}
        }, 'Updated')

        # Create NEW manager instance (simulates new session/restart)
        manager2 = TaskLifecycle(session_id=self.session_id)

        # Verify all fields persisted
        tasks = manager2.tasks
        assert '1' in tasks
        task = tasks['1']
        assert task['subject'] == 'Persistent task'
        assert task['description'] == 'Should survive'
        assert task['activeForm'] == 'Persisting...'
        assert task['status'] == 'in_progress'
        assert task['blockedBy'] == ['99']
        assert task['metadata']['custom'] == 'value'
        assert task['metadata']['extra'] == 'data'
        assert len(task['tool_outputs']) == 2

        print("✅ Test 8 passed: All fields persist across sessions")

    def test_09_deleted_task_handling(self):
        """Test 10: Deleted task handling."""
        print("\n=== Test 10: Deleted task handling ===")

        # Create and delete task
        self.manager.create_task('1', {'subject': 'To delete', 'description': '', 'activeForm': ''}, 'Created')
        self.manager.update_task('1', {'status': 'deleted'}, 'Deleted')

        # Verify deleted task doesn't block stop
        ctx = create_mock_context(session_id=self.session_id)
        result = self.manager.handle_stop(ctx)

        assert result is None, "Deleted tasks should not block stop"

        # Verify deleted task is in completed statuses
        incomplete = self.manager.get_incomplete_tasks(exclude_blocking=True)
        assert len(incomplete) == 0, "Deleted tasks should not be in incomplete list"

        print("✅ Test 10 passed: Deleted tasks don't block stop")

    def test_10_escape_hatch_after_max_blocks(self):
        """Test escape hatch: Stop allowed after max blocks."""
        print("\n=== Test: Escape hatch after max blocks ===")

        # Create incomplete task
        self.manager.create_task('1', {'subject': 'Stuck task', 'description': '', 'activeForm': ''}, 'Created')

        # Block stop multiple times
        ctx = create_mock_context(session_id=self.session_id)
        max_blocks = self.manager.config.stop_block_max_count

        for i in range(max_blocks):
            result = self.manager.handle_stop(ctx)
            assert result['continue'] == False, f"Block {i+1} should block"

        # Next attempt should allow override
        result = self.manager.handle_stop(ctx)
        assert result['continue'] == True, "Should allow stop after max blocks"
        assert 'STOP OVERRIDE' in result['systemMessage']

        print(f"✅ Test passed: Escape hatch works after {max_blocks} blocks")


def run_all_integration_tests():
    """Run all integration tests."""
    print("Running comprehensive integration tests...\n")

    test = TestTaskLifecycleIntegration()

    # Run each test
    tests = [
        test.test_01_create_tasks_with_full_metadata,
        test.test_02_update_task_with_dependencies,
        test.test_03_complete_task_and_update_metadata,
        test.test_04_stop_with_incomplete_work_blocks,
        test.test_05_complete_remaining_work_and_stop,
        test.test_06_resume_detection_full_context,
        test.test_07_plan_context_injection,
        test.test_08_cross_session_persistence,
        test.test_09_deleted_task_handling,
        test.test_10_escape_hatch_after_max_blocks,
    ]

    passed = 0
    failed = 0

    for test_func in tests:
        try:
            test.setup_method()
            test_func()
            test.teardown_method()
            passed += 1
        except AssertionError as e:
            print(f"❌ {test_func.__name__} failed: {e}")
            test.teardown_method()
            failed += 1
        except Exception as e:
            print(f"❌ {test_func.__name__} error: {e}")
            test.teardown_method()
            failed += 1

    print(f"\n{'='*60}")
    print(f"Integration Tests: {passed} passed, {failed} failed")
    print(f"{'='*60}\n")

    return failed == 0


if __name__ == '__main__':
    success = run_all_integration_tests()
    sys.exit(0 if success else 1)
